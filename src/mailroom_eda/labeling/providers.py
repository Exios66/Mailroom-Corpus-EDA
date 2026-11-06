"""One OpenAI-compatible chat client for both labeling backends.

* ``modal``: the sandbox's vLLM app (``VLLM_BASE_URL`` / ``VLLM_API_KEY``). Thinking is
  switched with ``chat_template_kwargs.enable_thinking`` (Qwen3 chat template).
* ``openrouter``: ``https://openrouter.ai/api/v1`` with ``OPENROUTER_API_KEY``. The
  request pins the routing from ``models.yaml`` ``route`` (only the listed providers,
  optional quantization allowlist and price ceiling, ``require_parameters`` so no
  provider silently ignores the schema), sets reasoning explicitly and asks for usage
  accounting, so each response reports its billed cost and serving provider.

Both use stdlib ``urllib`` so the playbook adds no dependency. Keys are read from the
environment only and never written anywhere.
"""
from __future__ import annotations

import http.client
import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PROVIDERS = ("modal", "openrouter")
#: Seconds one request may take. A Modal request can wait out a cold start (about 7 min to
#: load the 32B weights and capture graphs) and then a long prefill, so it gets the app's
#: 20-min startup timeout plus room; OpenRouter answers or fails well inside 5 min.
TIMEOUT_SECONDS = {"openrouter": 300.0, "modal": 1500.0}
REASONING = ("off", "low")
RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
SCHEMA_REJECT_STATUS = frozenset({400, 422})
_SCHEMA_WORDS = ("schema", "response_format", "structured", "json_object", "json mode")


class ProviderError(RuntimeError):
    """A request failed after its retries, or returned an unusable body.

    ``usage`` is set when the provider billed the failed call (a truncated or non-JSON
    answer still costs its tokens): ``{"prompt_tokens", "completion_tokens", "cost_usd"}``.
    The runner books it against the budget instead of releasing the reservation.
    """

    def __init__(self, message: str, usage: Mapping[str, Any] | None = None) -> None:
        """``usage`` carries the billed tokens and cost when the provider charged for the call."""
        super().__init__(message)
        self.usage = dict(usage) if usage else None


class _ReadError(Exception):
    """A 200 response whose body could not be read or decoded (never retried: it may be billed)."""


@dataclass(frozen=True)
class Backend:
    """Where requests go and how they authenticate."""

    provider: str
    base_url: str
    api_key: str = field(repr=False)

    @classmethod
    def from_env(cls, provider: str, env: Mapping[str, str] | None = None) -> "Backend":
        """Build a backend from ``env``, defaulting to the process environment.

        Raises ``ProviderError`` for an unknown provider, a missing API key, or a
        missing Modal base URL. Modal URLs receive a ``/v1`` suffix if needed.
        """
        env = os.environ if env is None else env
        if provider == "openrouter":
            key = env.get("OPENROUTER_API_KEY", "")
            base = env.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL)
        elif provider == "modal":
            key = env.get("VLLM_API_KEY", "")
            base = env.get("VLLM_BASE_URL", "")
            if not base:
                raise ProviderError("VLLM_BASE_URL is not set (the Modal vLLM app URL)")
            base = base.rstrip("/")
            if not base.endswith("/v1"):
                base += "/v1"
        else:
            raise ProviderError(f"unknown provider {provider!r}; use one of {PROVIDERS}")
        if not key:
            name = "OPENROUTER_API_KEY" if provider == "openrouter" else "VLLM_API_KEY"
            raise ProviderError(f"{name} is not set")
        return cls(provider, base.rstrip("/"), key)


@dataclass
class Completion:
    """One parsed response."""

    content: str
    parsed: Any
    served_model: str
    served_provider: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float | None
    finish_reason: str
    used_schema: bool
    cached_tokens: int = 0


def build_payload(provider: str, model: str, messages: Sequence[Mapping[str, str]],
                  schema: Mapping[str, Any] | None, *, reasoning: str = "off",
                  max_tokens: int = 4096, schema_name: str = "labels",
                  route: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The request body for ``provider``. ``schema=None`` asks for plain JSON mode.

    ``route`` is the model's ``models.yaml`` routing block. For OpenRouter, ``seed``
    defaults to enabled and is omitted when the route sets it to false.
    Raises ``ValueError`` unless ``reasoning`` is ``off`` or ``low``.
    """
    if reasoning not in REASONING:
        raise ValueError(f"reasoning must be one of {REASONING}")
    route = effective_route(route, reasoning)
    body: dict[str, Any] = {
        "model": model,
        "messages": [dict(m) for m in messages],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if provider == "openrouter" and route.get("temperature", True) is False:
        del body["temperature"]  # the endpoint rejects it; require_parameters would route nowhere
    if provider != "openrouter" or route.get("seed", True):
        body["seed"] = 42
    if schema is not None:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": schema_name, "strict": True,
                                                   "schema": schema}}
    else:
        body["response_format"] = {"type": "json_object"}
    if provider == "openrouter":
        prov: dict[str, Any] = {"require_parameters": True, "allow_fallbacks": True}
        for key in ("only", "quantizations", "max_price", "ignore", "data_collection"):
            if route.get(key):
                prov[key] = route[key]
        if "only" not in prov:
            prov["allow_fallbacks"] = False
        body["provider"] = prov
        body["reasoning"] = {"enabled": False} if reasoning == "off" else {"effort": "low"}
        body["usage"] = {"include": True}
    else:
        body["chat_template_kwargs"] = {"enable_thinking": reasoning != "off"}
    return body


def temperature(model: str) -> int | None:
    """The temperature sent for ``model``: 0, or ``None`` where its route omits it."""
    return None if (model_route(model) or {}).get("temperature", True) is False else 0


def effective_route(route: Mapping[str, Any] | None, reasoning: str) -> dict[str, Any]:
    """The route for one reasoning level: ``by_reasoning[<level>]`` overlays the base keys.

    Pinning providers per level matters because they do not honour reasoning alike (on
    2026-11-06 one DeepSeek provider spent 0 reasoning tokens at ``low``, another 241),
    so a calibration covers only the providers that serve its level.
    """
    route = dict(route or {})
    overlay = (route.pop("by_reasoning", None) or {}).get(reasoning) or {}
    return {**route, **overlay}


def _slug(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


#: Display names OpenRouter reports for a served provider whose slug differs (2026-11-06:
#: a Claude Haiku answer through ``google-vertex`` reported ``Google``).
SERVED_ALIASES = {"google": "googlevertex", "googlevertex": "googlevertex", "amazonbedrock": "amazonbedrock",
                  "bedrock": "amazonbedrock", "azureopenai": "azure"}


def served_outside_route(route: Mapping[str, Any] | None, reasoning: str, served: Any) -> list[str]:
    """Served provider names (display names such as ``CoreWeave``) outside the route's ``only`` pin."""
    only = {_slug(p) for p in effective_route(route, reasoning).get("only") or []}
    if not only:
        return []
    return sorted(str(p) for p in served
                  if p and _slug(p) not in only and SERVED_ALIASES.get(_slug(p)) not in only)


def model_route(model: str) -> dict[str, Any]:
    """The ``route`` block for an approved model (empty for unlisted models)."""
    from .registry import models_registry  # noqa: PLC0415

    return dict((models_registry()["models"].get(model) or {}).get("route") or {})


def _post(url: str, key: str, body: Mapping[str, Any], timeout: float) -> tuple[int, dict, dict]:
    """POST JSON; return (status, headers, parsed body). Network errors raise URLError."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Title": "mailroom-corpus-eda labeling"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, headers = resp.status, dict(resp.headers)
            try:
                return status, headers, json.loads(resp.read() or b"{}")
            except (http.client.HTTPException, ValueError, OSError) as err:
                raise _ReadError(f"HTTP {status} body could not be read: {type(err).__name__}: {err}") from err
    except urllib.error.HTTPError as err:
        raw = err.read() or b"{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"error": raw.decode("utf-8", "replace")[:500]}
        return err.code, dict(err.headers or {}), payload


def _retry_after(headers: Mapping[str, str], attempt: int) -> float:
    """Seconds to wait: ``Retry-After`` when given, else exponential backoff with jitter."""
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return min(float(v), 120.0)
            except ValueError:
                break
    return min(2.0 ** attempt + random.random(), 60.0)


def parse_completion(payload: Mapping[str, Any], *, used_schema: bool) -> Completion:
    """Parse a chat response whose content must be a JSON object.

    Raises ``ProviderError`` for missing choices, truncated output, invalid JSON, or a
    non-object answer. Truncated and invalid answers carry reported billing usage on
    the error when available. ``used_schema`` records the request mode; this function
    does not validate the answer against a schema.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderError(f"no choices in response: {str(payload)[:300]}")
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[content.find("{"):] if "{" in content else content
    finish = choices[0].get("finish_reason") or ""
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    cost = usage.get("cost")
    billed = {"prompt_tokens": int(usage.get("prompt_tokens") or 0),
              "completion_tokens": int(usage.get("completion_tokens") or 0),
              "cost_usd": float(cost) if isinstance(cost, (int, float)) else None}
    billed = billed if (billed["prompt_tokens"] or billed["completion_tokens"] or billed["cost_usd"]) else None
    if finish == "length":
        raise ProviderError("response truncated (finish_reason=length); raise max_tokens or shorten the window",
                            billed)
    try:
        parsed = json.loads(content)
    except ValueError as err:
        raise ProviderError(f"response is not JSON: {content[:200]!r}", billed) from err
    if not isinstance(parsed, dict):
        raise ProviderError(f"response is JSON but not an object: {content[:200]!r}", billed)
    return Completion(
        content=content, parsed=parsed,
        served_model=str(payload.get("model") or ""),
        served_provider=str(payload.get("provider") or ""),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_tokens=int(details.get("reasoning_tokens") or 0),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        finish_reason=finish, used_schema=used_schema,
        cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
    )


def chat(backend: Backend, model: str, messages: Sequence[Mapping[str, str]],
         schema: Mapping[str, Any] | None, *, reasoning: str = "off", max_tokens: int = 4096,
         attempts: int = 5, timeout: float | None = None,
         post: Callable[[str, str, Mapping[str, Any], float], tuple[int, dict, dict]] = _post,
         sleep: Callable[[float], None] = time.sleep,
         route: Mapping[str, Any] | None = None) -> Completion:
    """Send one request with retries.

    Retries on 408/409/425/429/500/502/503/504 and network errors, honoring
    ``Retry-After`` up to 120 seconds. ``attempts`` includes the initial request and
    any schema fallback; ``timeout`` is the timeout in seconds per request (default:
    :data:`TIMEOUT_SECONDS` for the backend's provider). If the
    provider rejects the JSON schema (a 400/422 whose error names the schema or response
    format) the request is repeated in plain JSON mode; the caller's validation then does
    the schema's job. Other HTTP failures, unreadable responses, and invalid completions
    raise ``ProviderError`` without retrying; exhausting attempts also raises it.
    ``ValueError`` from an invalid reasoning setting propagates. ``post`` and ``sleep``
    are injectable for tests.
    """
    url = f"{backend.base_url}/chat/completions"
    timeout = TIMEOUT_SECONDS.get(backend.provider, 300.0) if timeout is None else timeout
    use_schema = schema is not None
    last = ""
    for attempt in range(attempts):
        body = build_payload(backend.provider, model, messages, schema if use_schema else None,
                             reasoning=reasoning, max_tokens=max_tokens,
                             route=model_route(model) if route is None else route)
        try:
            status, headers, payload = post(url, backend.api_key, body, timeout)
        except (_ReadError, http.client.HTTPException) as err:
            raise ProviderError(f"response could not be read: {err}") from err
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = f"network error: {err}"
            sleep(_retry_after({}, attempt))
            continue
        if status == 200:
            return parse_completion(payload, used_schema=use_schema)
        last = f"HTTP {status}: {str(payload)[:300]}"
        if status in SCHEMA_REJECT_STATUS and use_schema and any(w in last.lower() for w in _SCHEMA_WORDS):
            use_schema = False
            continue
        if status == 404 and "endpoint" in last.lower():
            raise ProviderError(f"{last} -- no pinned provider serves {model} with these parameters; "
                                "run `labeling prices` and update models.yaml route")
        if status in RETRY_STATUS:
            sleep(_retry_after(headers, attempt))
            continue
        raise ProviderError(last)
    raise ProviderError(f"gave up after {attempts} attempts; last: {last}")


def count_tokens(backend: Backend, model: str, messages: Sequence[Mapping[str, str]], *,
                 reasoning: str = "off",
                 post: Callable[[str, str, Mapping[str, Any], float], tuple[int, dict, dict]] = _post,
                 timeout: float | None = None) -> tuple[int, int]:
    """``(prompt tokens after the chat template, served max_model_len)`` from vLLM's
    ``/tokenize``: exact, and no generation is billed. Modal only (OpenRouter has no such
    endpoint). Raises ``ProviderError`` unless it answers 200 with both numbers."""
    if backend.provider != "modal":
        raise ValueError("count_tokens asks the Modal vLLM app; OpenRouter has no /tokenize")
    root = backend.base_url.rstrip("/").removesuffix("/v1")
    body = {"model": model, "messages": [dict(m) for m in messages], "add_generation_prompt": True,
            "chat_template_kwargs": {"enable_thinking": reasoning != "off"}}
    try:
        status, _, payload = post(f"{root}/tokenize", backend.api_key, body,
                                  TIMEOUT_SECONDS["modal"] if timeout is None else timeout)
    except (_ReadError, http.client.HTTPException, urllib.error.URLError, TimeoutError, ConnectionError) as err:
        raise ProviderError(f"/tokenize failed: {err}") from err
    if status != 200 or not isinstance(payload.get("count"), int) or not payload.get("max_model_len"):
        raise ProviderError(f"/tokenize HTTP {status}: {str(payload)[:200]}")
    return int(payload["count"]), int(payload["max_model_len"])


def key_status(backend: Backend, *, timeout: float = 30.0) -> dict[str, Any]:
    """OpenRouter ``GET /key``: the key's limit and remaining credit (``data`` object)."""
    if backend.provider != "openrouter":
        raise ProviderError("key_status is OpenRouter-only")
    req = urllib.request.Request(f"{backend.base_url}/key",
                                 headers={"Authorization": f"Bearer {backend.api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read()).get("data") or {}
    except urllib.error.HTTPError as err:
        raise ProviderError(f"GET /key failed: HTTP {err.code}") from err
