"""Load the playbook's data files: field registry, model registry, prompts, Dojo snapshot.

Everything a campaign needs to be replayed lives next to this module as package data
(``fields.yaml``, ``models.yaml``, ``prompts/*.md``, ``campaigns/<name>/``,
``dojo_snapshot/dojo_schemas.json``). Prompts use ``${name}`` placeholders. Rendering
fails on a missing or unknown placeholder, so a template edit cannot silently leave
a hole in a prompt.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

HERE = Path(__file__).resolve().parent
PROMPTS_DIR = HERE / "prompts"
CAMPAIGNS_DIR = HERE / "campaigns"
_PLACEHOLDER = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")

ABSENCE_POLICIES = frozenset({"absent_ok", "evidence_only", "never_absent"})
SOURCES = frozenset({"deterministic", "gold", "llm"})


@lru_cache(maxsize=None)
def fields_registry() -> dict[str, Any]:
    """The parsed ``fields.yaml``, with corporate subclass entity keys expanded.

    Each corporate subclass key from the Dojo snapshot becomes a field with
    ``source: llm``, ``task: corporate``, ``absence: absent_ok`` and a ``subclasses`` list
    naming the subclasses it applies to.
    """
    reg = yaml.safe_load((HERE / "fields.yaml").read_text(encoding="utf-8"))
    corp = reg["classes"]["corporate_record"]["fields"]
    for sub, schema in dojo_snapshot()["corporate_subclass_schemas"].items():
        for ent in schema["entities"]:
            spec = corp.setdefault(ent["key"], {
                "type": ent["type"], "source": "llm", "task": "corporate", "absence": "absent_ok",
                "subclass_entity": True, "subclasses": [],
            })
            if spec.get("subclass_entity"):
                spec["subclasses"].append(sub)
    return reg


@lru_cache(maxsize=None)
def models_registry() -> dict[str, Any]:
    """The parsed ``models.yaml``."""
    return yaml.safe_load((HERE / "models.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def dojo_snapshot() -> dict[str, Any]:
    """The JSON snapshot of the Dojo catalogs (see ``scripts/labeling/snapshot_dojo.py``)."""
    return json.loads((HERE / "dojo_snapshot" / "dojo_schemas.json").read_text(encoding="utf-8"))


def class_fields(doc_class: str, subclass: str | None = None) -> dict[str, dict[str, Any]]:
    """Fields that apply to one document class (and, for corporate records, one subclass).

    Corporate core fields are limited to the subclass's ``core_applicable`` set, and
    subclass entity keys to the subclasses that define them.
    """
    fields = fields_registry()["classes"][doc_class]["fields"]
    if doc_class != "corporate_record" or subclass is None:
        return dict(fields)
    schema = dojo_snapshot()["corporate_subclass_schemas"].get(subclass)
    if schema is None:
        raise KeyError(f"unknown corporate subclass {subclass!r}")
    core = set(schema["core_applicable"])
    out = {}
    for name, spec in fields.items():
        if spec.get("subclass_entity"):
            if subclass in spec["subclasses"]:
                out[name] = spec
        elif name in core:
            out[name] = spec
    return out


def vocabulary(name: str) -> list[str]:
    """A closed vocabulary from ``fields.yaml``."""
    return list(fields_registry()["vocabularies"][name])


@dataclass(frozen=True)
class Prompt:
    """One prompt template file and its content hash."""

    name: str
    text: str

    @property
    def sha256(self) -> str:
        """sha256 of the template bytes; recorded on every journal record."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def placeholders(self) -> frozenset[str]:
        """Names of the ``${...}`` placeholders in the template."""
        return frozenset(_PLACEHOLDER.findall(self.text))

    def render(self, values: Mapping[str, Any]) -> str:
        """Fill every placeholder. Raises ``KeyError`` on a missing value and
        ``ValueError`` on values that are not used by the template."""
        missing = self.placeholders - set(values)
        if missing:
            raise KeyError(f"{self.name}: missing values for {sorted(missing)}")
        extra = set(values) - self.placeholders
        if extra:
            raise ValueError(f"{self.name}: values not used by the template: {sorted(extra)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.text)


@lru_cache(maxsize=None)
def prompt(name: str) -> Prompt:
    """Load ``prompts/<name>.md``."""
    path = PROMPTS_DIR / f"{name}.md"
    return Prompt(name, path.read_text(encoding="utf-8"))


def prompt_bundle_sha(names: list[str]) -> str:
    """One hash for the ordered set of templates a task uses."""
    h = hashlib.sha256()
    for n in names:
        h.update(n.encode())
        h.update(b"\0")
        h.update(prompt(n).sha256.encode())
    return h.hexdigest()


def campaign(name: str) -> dict[str, Any]:
    """The parsed ``campaigns/<name>/campaign.yaml``."""
    return yaml.safe_load((CAMPAIGNS_DIR / name / "campaign.yaml").read_text(encoding="utf-8"))
