"""Spend control for paid labeling runs.

Rules (mirroring the sandbox spend gate):

* Every run names a human's initials (2-6 capital letters), a USD cap and a wall-clock
  limit. ``require_run_flags`` refuses anything else.
* Before the first OpenRouter call, the cap must fit in the key's remaining credit
  minus ``key_reserve_usd`` from ``models.yaml``.
* ``Budget.reserve`` is called before every request with a worst-case cost (prompt
  tokens plus ``max_tokens`` at the model's price). The request is refused when spent
  plus that worst case would exceed the cap. ``Budget.settle`` then books the billed
  cost from the response, or tokens times price when the provider omits it.

Token counts before a call are estimated as ceil(chars / 4). That is the same rule
the cost tables in ``campaigns/<name>/costs.md`` use.
"""
from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .registry import models_registry

INITIALS_RE = re.compile(r"^[A-Z]{2,6}$")


class SpendError(RuntimeError):
    """A run was refused or stopped by the spend rules."""


def estimate_tokens(text: str) -> int:
    """Rough token count for planning: ceil(chars / 4)."""
    return math.ceil(len(text) / 4)


def price(model: str) -> tuple[float, float]:
    """(prompt, completion) USD per 1M tokens for an approved or comparison model."""
    reg = models_registry()
    spec = reg["models"].get(model) or reg.get("comparison", {}).get(model)
    if not spec or "prompt" not in spec:
        raise SpendError(f"no token price for {model!r} in models.yaml")
    return float(spec["prompt"]), float(spec["completion"])


def gpu_spec(model: str) -> tuple[float, float] | None:
    """``(USD per GPU-second, scale-down tail seconds)`` for a Modal model, else ``None``.

    Modal bills the container's GPU time, not tokens: from the first request (a cold start
    included, since the request waits for it) to the last answer, plus the idle seconds
    before the app scales to zero."""
    spec = models_registry()["models"].get(model) or {}
    if spec.get("provider") != "modal":
        return None
    return float(spec["gpu_usd_per_hour"]) / 3600, float(spec.get("scaledown_seconds", 0))


def token_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD for a call at the registry price."""
    p, c = price(model)
    return (prompt_tokens * p + completion_tokens * c) / 1_000_000


def require_run_flags(*, initials: str | None, cap_usd: float | None,
                      max_wall_seconds: float | None) -> None:
    """Refuse a paid run that lacks a human's initials, a cap or a wall limit."""
    if not initials or not INITIALS_RE.match(initials):
        raise SpendError("--initials must be a human operator's 2-6 capital letters")
    if cap_usd is None or not math.isfinite(cap_usd) or cap_usd <= 0:
        raise SpendError("--api-cost-cap-usd must be a positive, finite number")
    if max_wall_seconds is None or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise SpendError("--max-wall-seconds must be a positive, finite number")


def check_key_headroom(key_data: Mapping[str, Any], cap_usd: float) -> float:
    """Return the usable headroom; raise when the cap does not fit.

    ``key_data`` is OpenRouter's ``GET /key`` ``data`` object. A key with no limit
    (``limit_remaining`` null) passes, because the cap alone bounds the run.
    """
    reserve = float(models_registry().get("key_reserve_usd", 3.0))
    remaining = key_data.get("limit_remaining")
    if remaining is None:
        return math.inf
    headroom = float(remaining) - reserve
    if cap_usd > headroom:
        raise SpendError(f"cap ${cap_usd:.2f} exceeds key headroom ${headroom:.2f} "
                         f"(remaining ${float(remaining):.2f} minus ${reserve:.2f} reserve)")
    return headroom


@dataclass
class Budget:
    """Running spend for one chunk; safe to share between worker threads.

    ``reserve`` books a call's worst case as in flight, so concurrent calls can never
    together exceed the cap; ``settle`` replaces that reservation with the billed cost
    and ``release`` drops it when the call fails.

    A Modal model (:func:`gpu_spec`) is billed by GPU time, not tokens: its calls book
    nothing per call, and a meter runs from its first reservation to its last answer,
    plus the scale-down tail. Every reservation checks the meter's cost so far, so a slow
    or cold-starting Modal leg stops at the cap like a token-billed one.
    """

    cap_usd: float
    max_wall_seconds: float
    spent_usd: float = 0.0
    calls: int = 0
    in_flight_usd: float = 0.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False, compare=False)
    started: float | None = None
    gpu_usd_per_second: float = 0.0
    gpu_tail_seconds: float = 0.0
    gpu_first: float | None = None
    gpu_last: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.started is None:
            self.started = self.clock()

    def gpu_seconds(self, now: float | None = None) -> float:
        """Billed Modal GPU seconds: first send to ``now`` (default: the last answer), plus the tail."""
        if self.gpu_first is None:
            return 0.0
        end = now if now is not None else (self.gpu_last or self.gpu_first)
        return max(0.0, end - self.gpu_first) + self.gpu_tail_seconds

    def gpu_usd(self, now: float | None = None) -> float:
        """The Modal GPU cost of :meth:`gpu_seconds`."""
        return self.gpu_seconds(now) * self.gpu_usd_per_second

    def total_usd(self) -> float:
        """Token-billed spend plus the Modal GPU time."""
        return self.spent_usd + self.gpu_usd()

    def reserve(self, model: str, prompt_tokens: int, max_tokens: int) -> float:
        """Book the worst-case cost of the next call; raise when it could breach the cap or wall."""
        gpu = gpu_spec(model)
        worst = 0.0 if gpu else token_cost(model, prompt_tokens, max_tokens)
        with self._lock:
            now = self.clock()
            if now - self.started > self.max_wall_seconds:
                raise SpendError(f"wall-clock limit {self.max_wall_seconds:.0f}s reached")
            if gpu and self.gpu_first is None:
                self.gpu_usd_per_second, self.gpu_tail_seconds = gpu
                self.gpu_first = now
            meter = self.gpu_usd(now)
            if self.spent_usd + self.in_flight_usd + meter + worst > self.cap_usd:
                raise SpendError(f"next call could cost ${worst:.4f}; spent ${self.spent_usd:.4f} "
                                 f"+ GPU time ${meter:.4f} (+${self.in_flight_usd:.4f} in flight) "
                                 f"of ${self.cap_usd:.2f} cap")
            self.in_flight_usd += worst
        return worst

    def release(self, reserved: float) -> None:
        """Drop a reservation whose call failed without a bill."""
        with self._lock:
            self.in_flight_usd = max(0.0, self.in_flight_usd - reserved)

    def settle(self, model: str, prompt_tokens: int, completion_tokens: int,
               billed_usd: float | None, reserved: float = 0.0) -> float:
        """Book one call (replacing its reservation); returns the amount booked.

        A Modal call books nothing here: its cost is the GPU meter, which this extends."""
        gpu = gpu_spec(model)
        cost = 0.0 if gpu else (billed_usd if billed_usd is not None
                                else token_cost(model, prompt_tokens, completion_tokens))
        with self._lock:
            self.in_flight_usd = max(0.0, self.in_flight_usd - reserved)
            self.spent_usd += cost
            self.calls += 1
            if gpu:
                self.gpu_last = self.clock()
        return cost
