"""Central configuration for the Mailroom Corpus EDA."""
from __future__ import annotations

import hashlib
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt

REPO_ID = "Lucius-Morningstar/mailroom-corpus"
REPO_URL = f"https://huggingface.co/datasets/{REPO_ID}"
HF_USERNAME = "Lucius-Morningstar"

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
DATA_DIR = ROOT / "data"
PARQUET_DIR = DATA_DIR / "parquet"
FIG_DIR = ROOT / "reports" / "figures"
TABLE_DIR = ROOT / "reports" / "tables"
REPORT_DIR = ROOT / "reports"
INTERACTIVE_FIG_DIR = ROOT / "reports" / "figures_interactive"

MANIFEST_PATH = DATA_DIR / "manifest.txt"
JSONL_PATH = DATA_DIR / "docclass_merged.jsonl"
HF_EXPORT_DIR = DATA_DIR / "hf_export"

DOC_TYPES = ["contract", "merger_agreement", "corporate_record", "correspondence", "insurance_claim"]
TYPE_COLORS = {
    "contract": "#4C72B0",
    "merger_agreement": "#DD8452",
    "corporate_record": "#55A868",
    "correspondence": "#C44E52",
    "insurance_claim": "#8172B3",
}

TOKEN_BUDGETS = [4_096, 8_192, 16_384, 32_768, 65_536, 131_072, 200_000]
CHARS_PER_TOKEN = 4  # heuristic; tiktoken o200k used for accurate estimates

RANDOM_STATE = 42


def split_rule(filename: str, encoding: str = "utf-8") -> str:
    """Family split rule: md5(filename) % 10 == 0 -> test else train."""
    h = hashlib.md5(filename.encode(encoding)).hexdigest()
    return "test" if int(h, 16) % 10 == 0 else "train"


def setup_matplotlib() -> None:
    mpl.use("Agg")
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
            "axes.grid": True,
            "grid.alpha": 0.3,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 10,
            "axes.titlesize": 11,
            "figure.facecolor": "white",
        }
    )


def ensure_dirs() -> None:
    for d in (DATA_DIR, FIG_DIR, TABLE_DIR, REPORT_DIR, INTERACTIVE_FIG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# CUAD clause types (41 clauses from CUAD v1)
CUAD_CLAUSES = [
    "Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date",
    "Governing Law", "Anti-Assignment", "Audit Rights", "Cap On Liability", "Change Of Control",
    "Competitive Restriction Exception", "Covenant Not To Sue", "Exclusivity",
    "Insurance", "Ip Ownership Assignment", "Irrevocable Or Perpetual License", "Joint Ip Ownership",
    "License Grant", "Liquidated Damages", "Minimum Commitment", "Most Favored Nation",
    "Non-Compete", "Non-Disparagement", "Non-Transferable License", "Notice Period To Terminate Renewal",
    "No-Solicit Of Customers", "No-Solicit Of Employees", "Post-Termination Services",
    "Price Restrictions", "Renewal Term", "Revenue/Profit Sharing", "Rofr/Rofo/Rofn",
    "Source Code Escrow", "Termination For Convenience", "Third Party Beneficiary",
    "Uncapped Liability", "Unlimited/All-You-Can-Eat-License", "Volume Restriction",
    "Warranty Duration", "Affiliate License-Licensee", "Affiliate License-Licensor",
]

# MAUD task categories
MAUD_CATEGORIES = {
    "Conditions to Closing": [
        "Accuracy of Target R&W Closing Condition",
        "MAE Definition",
        "Tail Period & Acquisition Proposal Details",
        "Absence of Litigation Closing Condition",
        "Compliance with Covenant Closing Condition",
        "Intervening Event Definition",
    ],
    "Covenants": [
        "Ordinary course covenant",
        "Negative interim operating covenant",
        "Fiduciary exception to COR covenant",
        "Fiduciary exception:  Board determination (no-shop)",
        "Breach of Meeting Covenant",
        "Breach of No Shop",
    ],
    "No-Shop / FTR": [
        "No-Shop",
        "Agreement provides for matching rights in connection with COR",
        "Agreement provides for matching rights in connection with FTR",
        "FTR Triggers",
        "Limitations on FTR Exercise",
    ],
    "Definitions": [
        "Superior Offer Definition",
        "Knowledge Definition",
        "General Antitrust Efforts Standard",
        "Type of Consideration",
        "Specific Performance",
    ],
}
