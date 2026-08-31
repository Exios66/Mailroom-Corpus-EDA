"""Dataset acquisition + manifest reconciliation (P0)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
from huggingface_hub import snapshot_download

from .config import DATA_DIR, JSONL_PATH, MANIFEST_PATH, PARQUET_DIR, REPO_ID

ALLOW_PATTERNS = ["parquet/*", "manifest.txt", "docclass_merged.jsonl", "README.md"]


def download_corpus(force: bool = False) -> Path:
    """Snapshot-download parquet configs + manifest + legacy JSONL into data/."""
    marker = PARQUET_DIR / "ground_truth" / "train"
    if marker.exists() and list(marker.glob("*.parquet")) and not force:
        return DATA_DIR
    snapshot_download(
        repo_id=REPO_ID,
        repo_type="dataset",
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
    return df


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
    m_total = re.search(r"(\d+)", man.get("rows_total", ""))
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
