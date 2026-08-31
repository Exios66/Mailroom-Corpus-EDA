"""P2 — corpus composition: strata, imbalance, provenance, metadata coverage."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

from .config import DOC_TYPES, FIG_DIR, TABLE_DIR, TYPE_COLORS, setup_matplotlib
from .download import load_default, load_ground_truth
from .integrity import _meta_series, metadata_coverage


def strata_table(gt: pd.DataFrame) -> pd.DataFrame:
    t = (
        gt.groupby(["expected", "expected_subclass"])
        .agg(total=("filename", "size"), train=("split", lambda s: (s == "train").sum()), test=("split", lambda s: (s == "test").sum()))
        .reset_index()
        .sort_values(["expected", "total"], ascending=[True, False])
    )
    t["share_pct"] = 100 * t["total"] / t["total"].sum()
    t["test_share_pct"] = 100 * t["test"] / t["total"].clip(lower=1)
    return t


def imbalance_metrics(gt: pd.DataFrame) -> dict:
    counts = gt["expected"].value_counts()
    p = counts / counts.sum()
    sub = gt.groupby("expected")["expected_subclass"].value_counts()
    strata = gt.groupby(["expected", "expected_subclass"]).size()
    chi2, pval, dof, _ = stats.chi2_contingency(
        pd.crosstab(gt["expected"], gt["split"]).reindex(index=DOC_TYPES)
    )
    return {
        "type_counts": counts.to_dict(),
        "type_entropy_bits": float(-(p * np.log2(p)).sum()),
        "max_min_imbalance_ratio_type": float(counts.max() / counts.min()),
        "n_strata": int(len(strata)),
        "max_min_imbalance_ratio_strata": float(strata.max() / strata.min()),
        "min_stratum": {"stratum": list(strata.idxmin()), "count": int(strata.min())},
        "split_homogeneity_chi2": float(chi2),
        "split_homogeneity_p": float(pval),
        "split_homogeneity_dof": int(dof),
        "subclass_counts": {f"{k[0]}|{k[1]}": int(v) for k, v in sub.items()},
    }


def provenance(blind: pd.DataFrame, gt: pd.DataFrame) -> pd.DataFrame:
    meta = _meta_series(blind)
    rows = []
    for dt in DOC_TYPES:
        idx = gt.index[gt["expected"] == dt]
        m = meta.loc[idx]
        rows.append(
            {
                "doc_type": dt,
                "rows": len(idx),
                "source_values": "; ".join(sorted({str(v) for v in m["source"].dropna() if str(v)})[:3]),
                "source_dataset_values": "; ".join(sorted({str(v) for v in m["source_dataset"].dropna() if str(v)})[:3]),
                "n_unique_cik": int(m["cik"].replace("", np.nan).dropna().nunique()) if "cik" in m else 0,
                "n_unique_custodian": int(m["custodian"].replace("", np.nan).dropna().nunique()) if "custodian" in m else 0,
                "n_unique_sender": int(m["sender_addr"].replace("", np.nan).dropna().nunique()) if "sender_addr" in m else 0,
                "pdf_path_filled": int(m["pdf_path"].ne("").sum()) if "pdf_path" in m else 0,
            }
        )
    return pd.DataFrame(rows)


def date_span(blind: pd.DataFrame, gt: pd.DataFrame) -> dict:
    meta = _meta_series(blind)
    out = {}
    for col in ("date", "filing_date", "year", "date_filed"):
        if col not in meta.columns:
            continue
        s = pd.to_numeric(meta[col].replace("", np.nan), errors="coerce")
        years = pd.to_datetime(meta[col].replace("", np.nan), errors="coerce", format="mixed").dt.year
        years = years.fillna(s)
        out[col] = {
            "filled": int(years.notna().sum()),
            "min": float(np.nanmin(years)) if years.notna().any() else None,
            "max": float(np.nanmax(years)) if years.notna().any() else None,
        }
    return out


def fig_type_distribution(gt: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    import seaborn as sns

    counts = gt["expected"].value_counts().reindex(DOC_TYPES)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    sns.barplot(x=counts.values, y=counts.index, hue=counts.index, palette=TYPE_COLORS, legend=False, ax=axes[0])
    axes[0].set_title(f"Documents per doc_type (n={counts.sum()})")
    axes[0].set_xlabel("rows")
    for i, v in enumerate(counts.values):
        axes[0].text(v + 4, i, str(v), va="center", fontsize=9)
    sub = gt.groupby("expected")["expected_subclass"].nunique().reindex(DOC_TYPES)
    sns.barplot(x=sub.values, y=sub.index, hue=sub.index, palette=TYPE_COLORS, legend=False, ax=axes[1])
    axes[1].set_title("Distinct expected_subclass per doc_type")
    axes[1].set_xlabel("subclasses")
    for i, v in enumerate(sub.values):
        axes[1].text(v + 0.2, i, str(v), va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "01_type_and_subclass_distribution.png")
    plt.close(fig)


def fig_strata(gt: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt

    t = strata_table(gt)
    order = t.sort_values(["expected", "total"], ascending=[True, False])
    labels = order["expected"].str[:4] + ":" + order["expected_subclass"]
    fig, ax = plt.subplots(figsize=(12, 6.5))
    y = np.arange(len(order))
    ax.barh(y, order["train"], color="#4C72B0", label="train")
    ax.barh(y, order["test"], left=order["train"], color="#DD8452", label="test")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.invert_yaxis()
    ax.set_title("All 48 strata: expected × expected_subclass (train/test)")
    ax.set_xlabel("rows")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "02_strata_train_test.png")
    plt.close(fig)


def fig_metadata_heatmap(cov: pd.DataFrame) -> None:
    import matplotlib.pyplot as plt
    import seaborn as sns

    cov = cov.reindex([d for d in DOC_TYPES if d in cov.index])
    fig, ax = plt.subplots(figsize=(10, 9))
    sns.heatmap(cov, annot=True, fmt=".2f", cmap="viridis", vmin=0, vmax=1, cbar_kws={"label": "fill rate"}, ax=ax)
    ax.set_title("Metadata field fill rate by doc_type (43-key union)")
    ax.set_xlabel("")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "03_metadata_fill_rate_heatmap.png")
    plt.close(fig)


def run(save: bool = True) -> dict:
    setup_matplotlib()
    blind = load_default()
    gt = load_ground_truth()
    out = {
        "imbalance": imbalance_metrics(gt),
        "provenance": provenance(blind, gt).to_dict(orient="records"),
        "date_span": date_span(blind, gt),
    }
    strata = strata_table(gt)
    cov = metadata_coverage(blind, gt)
    if save:
        strata.to_csv(TABLE_DIR / "strata_counts.csv", index=False)
        provenance(blind, gt).to_csv(TABLE_DIR / "provenance_by_type.csv", index=False)
        strata.to_json(TABLE_DIR / "imbalance_metrics.json", orient="records", indent=2)
        with open(TABLE_DIR / "imbalance_metrics.json", "w") as f:
            json.dump(out["imbalance"], f, indent=2, default=str)
        fig_type_distribution(gt)
        fig_strata(gt)
        fig_metadata_heatmap(cov)
    return out
