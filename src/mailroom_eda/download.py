"""Dataset acquisition + manifest reconciliation (P0)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from huggingface_hub import snapshot_download

from .config import (
    DATA_DIR,
    JSONL_PATH,
    MANIFEST_PATH,
    PARQUET_DIR,
    REPO_ID,
    REPO_REVISION,
)

# v9 repo tree: parquet configs (default/ground_truth/bundles/streams/fixtures)
# + manifest + the hardened GT sidecar. The legacy `docclass_merged.jsonl` no
# longer ships; sidecar JSONLs (bundles/streams/fixtures) are staged-only.
ALLOW_PATTERNS = [
    "parquet/*",
    "manifest.txt",
    "ground_truth_hardened.jsonl",
    "README.md",
]


def download_corpus(force: bool = False) -> Path:
    """Snapshot-download the pinned v9 parquet configs + manifest into data/."""
    marker = PARQUET_DIR / "ground_truth" / "train"
    if marker.exists() and list(marker.glob("*.parquet")) and not force:
        return DATA_DIR
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
        revision=REPO_REVISION,
        local_dir=DATA_DIR,
        allow_patterns=ALLOW_PATTERNS,
    )
    return DATA_DIR


def parse_manifest(path: Path = MANIFEST_PATH) -> dict:
    """Parse the flat manifest.txt into a dict (multi-line values joined)."""
    raw: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or set(line.strip()) == {"="}:
            continue
        m = re.match(r"^(\w+)\s*:\s*(.*)$", line)
        if m:
            raw.setdefault(m.group(1), []).append(m.group(2).strip())
        elif raw:
            raw[last_key].append(line.strip())
            continue
        last_key = m.group(1) if m else None
    return {k: " ".join(v) for k, v in raw.items()}


def load_default() -> pd.DataFrame:
    return _load_config("default")


def load_ground_truth() -> pd.DataFrame:
    return _load_config("ground_truth")


def _load_config(cfg: str) -> pd.DataFrame:
    frames = []
    for split in ("train", "test"):
        p = PARQUET_DIR / cfg / split
        for f in sorted(p.glob("*.parquet")):
            df = pd.read_parquet(f)
            if "split" not in df.columns:
                df = df.assign(split=split)  # default config: split implicit in directory
            frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    if cfg == "ground_truth" and "gt_fields" in df.columns:
        # v9 schema: all label/annotation fields live inside the `gt_fields`
        # JSON column (uniform 29-key set — the §84 complete-GT pass). Expand
        # them to top-level columns so consumers keep the flat v8-era schema.
        df = _expand_gt_fields(df)
    return df


def _expand_gt_fields(df: pd.DataFrame) -> pd.DataFrame:
    """Promote the v9 `gt_fields` JSON column to top-level GT columns.

    Values are kept as native JSON (strings stay strings; nested label maps
    stay JSON-encoded strings, which the consumers' `_parse_labels()` handles).
    """
    parsed = df["gt_fields"].apply(
        lambda v: json.loads(v)
        if isinstance(v, str) and v.strip()
        else (v if isinstance(v, dict) else {})
    )
    expanded = pd.DataFrame(parsed.tolist(), index=df.index)
    return pd.concat([df, expanded], axis=1)


def load_jsonl() -> pd.DataFrame:
    return pd.read_json(JSONL_PATH, lines=True, dtype=False)


def row_counts(df: pd.DataFrame, split_col: str = "split") -> dict:
    out = {"total": int(len(df))}
    out.update({k: int(v) for k, v in df[split_col].value_counts().items()})
    return out


def validate_against_manifest() -> dict:
    """Compare on-disk reality to manifest claims. Returns a report dict."""
    man = parse_manifest()
    report: dict = {"manifest": man}
    # v8 manifests used `rows_total`; v9 uses `rows` — accept either.
    m_total = re.search(r"(\d+)", man.get("rows_total", "") or man.get("rows", ""))
    report["manifest_rows_total"] = int(m_total.group(1)) if m_total else None

    blind = load_default()
    gt = load_ground_truth()
    report["on_disk_rows"] = int(len(blind))
    report["manifest_rows_by_config"] = {
        "default": {"train": int((blind["split"] == "train").sum()),
                    "test": int((blind["split"] == "test").sum())},
        "ground_truth": {"train": int((gt["split"] == "train").sum()),
                         "test": int((gt["split"] == "test").sum())},
    }
    report["manifest_type_counts"] = gt["expected"].value_counts().to_dict()
    report["manifest_matches_on_disk"] = (
        report["manifest_rows_total"] == report["on_disk_rows"]
    )
    return report


def jsonl_preview(n: int = 2) -> list[dict]:
    with open(JSONL_PATH) as f:
        return [json.loads(next(f)) for _ in range(n)]
