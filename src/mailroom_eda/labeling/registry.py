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


DOJO_TYPES = frozenset({"name", "label", "free_text", "id", "date", "money", "entity_list",
                        "entity_list:name", "entity_list:free_text"})


def validate_registry(reg: Mapping[str, Any], *, prompts_dir: Path = PROMPTS_DIR) -> list[str]:
    """Problems in a parsed ``fields.yaml`` (empty when it is valid).

    Checks every class and field for a Dojo type, source, absence policy, known
    vocabulary and a labeling task that targets the field's class; and every
    ``tasks:`` entry for a known class and an existing prompt with the placeholders
    the generic builder fills.
    """
    out: list[str] = []
    vocabs = reg.get("vocabularies") or {}
    declared = dict(reg.get("tasks") or {})
    builtin = {"cuad": "contract", "intent": "correspondence", "contract_entities": "contract",
               "merger_entities": "merger_agreement", "correspondence": "correspondence",
               "claim_checklist": "insurance_claim", "corporate": "corporate_record"}
    task_class = {**builtin, **{k: v.get("class") for k, v in declared.items()}}
    for cls, block in (reg.get("classes") or {}).items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", cls):
            out.append(f"class {cls!r}: use a snake_case key")
        if not str(block.get("specialist") or "").endswith("_specialist"):
            out.append(f"{cls}: specialist must be a <name>_specialist key")
        for name, f in (block.get("fields") or {}).items():
            where = f"{cls}.{name}"
            if f.get("type") not in DOJO_TYPES:
                out.append(f"{where}: type {f.get('type')!r} is not a Dojo scoring type")
            if f.get("retype") and f["retype"] not in DOJO_TYPES:
                out.append(f"{where}: retype {f['retype']!r} is not a Dojo scoring type")
            if f.get("source") not in SOURCES:
                out.append(f"{where}: source must be one of {sorted(SOURCES)}")
            if f.get("absence") not in ABSENCE_POLICIES:
                out.append(f"{where}: absence must be one of {sorted(ABSENCE_POLICIES)}")
            if f.get("vocabulary") and f["vocabulary"] not in vocabs:
                out.append(f"{where}: unknown vocabulary {f['vocabulary']!r}")
            if (f.get("retype") or f.get("type")) == "label" and not f.get("vocabulary") and f.get("source") == "llm":
                out.append(f"{where}: an llm label field needs a closed vocabulary")
            if f.get("source") == "llm":
                t = f.get("task")
                if t not in task_class:
                    out.append(f"{where}: task {t!r} is neither built in nor declared under tasks:")
                elif task_class[t] != cls:
                    out.append(f"{where}: task {t!r} labels {task_class[t]!r}, not {cls!r}")
                if t in declared and not f.get("description"):
                    out.append(f"{where}: fields of a declared task need a description for the prompt")
    for t, spec in declared.items():
        if spec.get("class") not in (reg.get("classes") or {}):
            out.append(f"task {t}: class {spec.get('class')!r} is not in classes:")
        path = prompts_dir / f"{spec.get('prompt', t)}.md"
        if not path.exists():
            out.append(f"task {t}: missing prompt {path.name}")
            continue
        found = set(_PLACEHOLDER.findall(path.read_text(encoding="utf-8")))
        missing = {"field_list", "doc_text_window"} - found
        extra = found - {"field_list", "doc_text_window", "subclass", "window_description"}
        if missing:
            out.append(f"task {t}: prompt {path.name} lacks ${{{'}, ${'.join(sorted(missing))}}}")
        if extra:
            out.append(f"task {t}: prompt {path.name} has placeholders the generic builder cannot fill: "
                       f"{sorted(extra)}")
    return out
