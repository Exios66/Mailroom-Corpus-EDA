"""Deterministic labels: values set from data already in the row, with no model call.

Used by the v10 build (to fill them) and by ``tasks`` (to send a field to a model
only when it cannot be derived, e.g. ``merger_consideration`` on the merger agreements
that have no expert MAUD "Type of Consideration" answer).
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .registry import dojo_snapshot, fields_registry, vocabulary

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _gt(row: Mapping[str, Any]) -> dict[str, Any]:
    """``gt_fields`` as a dict."""
    g = row.get("gt_fields") or {}
    return json.loads(g) if isinstance(g, str) else dict(g)


def _obj(value: Any) -> Any:
    """Decode a JSON-encoded string; pass anything else through."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def compact(text: Any) -> str:
    """Lower-case alphanumerics only, for surface matching."""
    return _NON_ALNUM.sub("", str(text or "").lower())


def cuad_family(subclass: str) -> dict[str, Any] | None:
    """The Dojo CUAD family whose key, label or surfaces match ``subclass``."""
    want = compact(subclass)
    if not want:
        return None
    for fam in dojo_snapshot()["cuad_families"]:
        names = [fam.get("key"), fam.get("label"), *(fam.get("surfaces") or [])]
        if want in {compact(n) for n in names if n}:
            return fam
    return None


def maud_consideration_answer(row: Mapping[str, Any]) -> str | None:
    """The expert MAUD "Type of Consideration" answer as a Dojo token, if any."""
    labels = _obj(_gt(row).get("maud_clause_labels"))
    if not isinstance(labels, dict):
        return None
    ans = (labels.get("Type of Consideration") or {}).get("answer")
    return dojo_snapshot()["maud_type_of_consideration_to_token"].get(str(ans)) if ans else None


def merger_consideration(row: Mapping[str, Any]) -> str | None:
    """The merger subclass when the expert MAUD answer confirms it; else ``None``.

    A missing or unrecognized MAUD answer returns ``None``, leaving the label for the
    ``merger_consideration`` task. Raises ``ValueError`` when a recognized expert answer
    disagrees with the row's subclass.
    """
    token = maud_consideration_answer(row)
    sub = str(row.get("expected_subclass") or "")
    if token and sub == token:
        return token
    if token and sub != token:
        raise ValueError(f"{row.get('filename')}: subclass {sub!r} disagrees with MAUD answer {token!r}")
    return None


def communication_type(row: Mapping[str, Any]) -> str:
    """The correspondence subclass, which is the communication type."""
    sub = str(row.get("expected_subclass") or "")
    return sub if sub in vocabulary("communication_type") else "other"


def canonical_subclass(row: Mapping[str, Any]) -> str:
    """The Dojo canonical subclass key for the row's class."""
    cls = row["expected"]
    sub = str(row.get("expected_subclass") or "")
    if cls == "contract":
        fam = cuad_family(sub)
        return fam["key"] if fam else "other"
    if cls == "correspondence":
        return communication_type(row)
    return _NON_ALNUM.sub("_", sub.lower()).strip("_") or "other"


def specialist(row: Mapping[str, Any]) -> str:
    """The routing specialist for the row's class, from the field registry."""
    return fields_registry()["classes"][row["expected"]]["specialist"]


def deterministic_value(row: Mapping[str, Any], field: str) -> Any:
    """Derive a supported deterministic field, returning ``None`` if evidence is missing.

    Raises ``KeyError`` for an unsupported class/field pair, or ``ValueError`` when a
    merger's subclass conflicts with its recognized expert MAUD answer.
    """
    cls = row["expected"]
    if cls == "contract" and field == "cuad_family":
        return canonical_subclass(row)
    if cls == "merger_agreement" and field == "merger_consideration":
        return merger_consideration(row)
    if cls == "correspondence" and field == "communication_type":
        return communication_type(row)
    raise KeyError(f"no derivation for {cls}.{field}")
