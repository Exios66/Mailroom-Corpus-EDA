"""P3 — professional-grade static visualizations for the docclass corpus."""
from __future__ import annotations

import json
from collections import Counter
from math import pi

import numpy as np
import pandas as pd

from .config import (
    CUAD_CLAUSES,
    DOC_TYPES,
    FIG_DIR,
    TABLE_DIR,
    TOKEN_BUDGETS,
    TYPE_COLORS,
    setup_matplotlib,
)
from .download import load_default, load_ground_truth
from .integrity import _meta_series
from .token_budget import budget_coverage, budget_coverage_by_type, compute_token_stats, estimate_tokens, token_ecdf_by_type


def _load_all() -> tuple[pd.DataFrame, pd.DataFrame]:
    blind = load_default()
    gt = load_ground_truth()
    meta = _meta_series(blind)
    return blind, gt, meta


def _parse_labels(v):
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return v if isinstance(v, dict) else {}


def _text_frame(blind: pd.DataFrame, gt: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame({
        "filename": blind["filename"],
        "doc_type": gt["expected"],
        "subclass": gt["expected_subclass"],
        "chars": blind["doc_text"].str.len(),
        "tokens": blind["doc_text"].str.len() / 4.0,
    })
    return df


# ---------------------------------------------------------------------------
# 3.1 Text & token analysis
# ---------------------------------------------------------------------------

def fig_text_length_violin(blind: pd.DataFrame, gt: pd.DataFrame) -> None:
    """04 — log-scale char-length violin per doc_type."""
    import matplotlib.pyplot as plt

    df = _text_frame(blind, gt)
    order = [d for d in DOC_TYPES if d in df["doc_type"].unique()]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    parts = ax.violinplot(
        [np.log10(df.loc[df["doc_type"] == d, "chars"].values) for d in order],
        positions=range(len(order)), showmeans=True, showextrema=True,
    )
    for i, pc in enumerate(parts["bodies"]):
        pc.set_facecolor(TYPE_COLORS[order[i]])
        pc.set_alpha(0.7)
    medians = [np.log10(df.loc[df["doc_type"] == d, "chars"].median()) for d in order]
    means = [np.log10(df.loc[df["doc_type"] == d, "chars"].mean()) for d in order]
    ax.scatter(range(len(order)), medians, marker="o", s=40, color="black", zorder=5, label="median")
    ax.scatter(range(len(order)), means, marker="D", s=30, color="white", edgecolor="black", zorder=6, label="mean")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(order, rotation=15)
    ax.set_ylabel("log10(characters)")
    ax.set_title("Document length by doc_type (log scale)")
    ax.legend()
    for i, d in enumerate(order):
        n = int((df["doc_type"] == d).sum())
        ax.text(i, ax.get_ylim()[1] * 0.97, f"n={n}", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "04_text_length_violin.png")
    plt.close(fig)


def fig_token_budget_coverage(blind: pd.DataFrame, gt: pd.DataFrame) -> None:
    """05 — % of docs fitting in each token budget per doc_type."""
    import matplotlib.pyplot as plt

    df = _text_frame(blind, gt)
    cov = budget_coverage_by_type([{"expected": r.doc_type, "doc_text": ""} for r in df.itertuples()])
    # rebuild properly
    rows = []
    for d in DOC_TYPES:
        if d not in df["doc_type"].values:
            continue
        toks = df.loc[df["doc_type"] == d, "tokens"].values
        for b in TOKEN_BUDGETS:
            rows.append({"doc_type": d, "budget": b, "pct": 100 * (toks <= b).mean()})
    cov = pd.DataFrame(rows)
    pivot = cov.pivot(index="budget", columns="doc_type", values="pct").reindex(columns=DOC_TYPES)

    fig, ax = plt.subplots(figsize=(10, 6))
    bottom = np.zeros(len(pivot))
    x = np.arange(len(pivot))
    for d in DOC_TYPES:
        if d not in pivot.columns:
            continue
        vals = pivot[d].fillna(0).values
        ax.bar(x, vals, bottom=bottom, label=d, color=TYPE_COLORS[d], width=0.7)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([f"{b//1024}k" for b in pivot.index], rotation=0)
    ax.set_xlabel("token budget (heuristic chars/4)")
    ax.set_ylabel("cumulative % of documents")
    ax.set_title("Token budget coverage: share of corpus fitting each context window")
    ax.legend(title="doc_type", ncol=2)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "05_token_budget_coverage.png")
    plt.close(fig)


def fig_text_length_ecdf(blind: pd.DataFrame, gt: pd.DataFrame) -> None:
    """06 — ECDF of token estimates by doc_type."""
    import matplotlib.pyplot as plt

    df = _text_frame(blind, gt)
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for d in DOC_TYPES:
        toks = np.sort(df.loc[df["doc_type"] == d, "tokens"].values)
        y = np.arange(1, len(toks) + 1) / len(toks)
        ax.plot(toks, y, label=f"{d} (n={len(toks)})", color=TYPE_COLORS[d], lw=2)
    for b in (4096, 8192, 16384, 32768):
        ax.axvline(b, color="grey", ls=":", lw=1)
        ax.text(b, 0.02, f"{b//1024}k", rotation=90, fontsize=7, color="grey")
    ax.set_xscale("log")
    ax.set_xlabel("estimated tokens (chars/4, log scale)")
    ax.set_ylabel("empirical CDF")
    ax.set_title("Token-length ECDF by doc_type")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "06_text_length_ecdf.png")
    plt.close(fig)


def fig_length_by_subclass(blind: pd.DataFrame, gt: pd.DataFrame) -> None:
    """07 — token length by expected_subclass (top 15 by count)."""
    import matplotlib.pyplot as plt

    df = _text_frame(blind, gt)
    top = df["subclass"].value_counts().head(15).index.tolist()
    subset = df[df["subclass"].isin(top)]
    order = subset.groupby("subclass")["tokens"].median().sort_values().index.tolist()

    fig, ax = plt.subplots(figsize=(10, 6.5))
    bp = ax.boxplot(
        [subset.loc[subset["subclass"] == s, "tokens"].values for s in order],
        vert=False, tick_labels=order, patch_artist=True, showfliers=False,
    )
    for patch, s in zip(bp["boxes"], order):
        dt = subset.loc[subset["subclass"] == s, "doc_type"].mode()[0]
        patch.set_facecolor(TYPE_COLORS.get(dt, "#999999"))
        patch.set_alpha(0.75)
    ax.set_xscale("log")
    ax.set_xlabel("estimated tokens (log)")
    ax.set_title("Token length by expected_subclass (top 15 by count)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "07_length_by_subclass.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.2 CUAD clause deep-dive (contracts)
# ---------------------------------------------------------------------------

def _cuad_matrix(gt: pd.DataFrame) -> pd.DataFrame:
    """Return (n_contracts x n_clauses) presence DataFrame + span count df."""
    rows = []
    for _, r in gt[gt["expected"] == "contract"].iterrows():
        labels = _parse_labels(r["cuad_clause_labels"])
        row = {}
        for c in CUAD_CLAUSES:
            spans = (labels or {}).get(c, [])
            row[c] = len(spans) if spans else 0
        row["filename"] = r["filename"]
        rows.append(row)
    df = pd.DataFrame(rows)
    if "filename" in df.columns:
        df = df.set_index("filename")
    return df


def fig_cuad_clause_presence(gt: pd.DataFrame) -> None:
    """08 — contract × clause binary presence heatmap (509 × 41)."""
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    mat = _cuad_matrix(gt)
    presence = (mat > 0).astype(int).T
    fig, ax = plt.subplots(figsize=(13, 9))
    ax.imshow(presence.values, aspect="auto", cmap=ListedColormap(["#f0f0f0", "#4C72B0"]))
    ax.set_yticks(np.arange(len(presence)))
    ax.set_yticklabels(presence.index, fontsize=6)
    ax.set_xticks([])
    ax.set_title(f"CUAD clause presence — {presence.shape[1]} contracts × {presence.shape[0]} clause types")
    ax.set_xlabel(f"{presence.shape[1]} contracts (dark = clause present)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "08_cuad_clause_presence.png")
    plt.close(fig)


def fig_cuad_span_counts(gt: pd.DataFrame) -> None:
    """09 — mean spans per clause type with 95% CI."""
    import matplotlib.pyplot as plt

    mat = _cuad_matrix(gt)
    stats = mat.apply(lambda s: pd.Series({
        "mean": s[s > 0].mean() if (s > 0).any() else 0,
        "ci": 1.96 * s[s > 0].std() / np.sqrt(max((s > 0).sum(), 1)) if (s > 0).any() else 0,
        "n": (s > 0).sum(),
    })).T
    stats = stats.sort_values("mean", ascending=True)

    fig, ax = plt.subplots(figsize=(10, 11))
    y = np.arange(len(stats))
    ax.barh(y, stats["mean"], xerr=stats["ci"], color="#4C72B0", alpha=0.85,
            error_kw={"ecolor": "black", "capsize": 2})
    ax.set_yticks(y)
    ax.set_yticklabels(stats.index, fontsize=7)
    ax.set_xlabel("mean span count per contract (95% CI)")
    ax.set_title("CUAD clause density — mean spans per contract")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "09_cuad_span_counts.png")
    plt.close(fig)


def fig_cuad_top_clauses(gt: pd.DataFrame) -> None:
    """10 — top 20 clauses by contract coverage."""
    import matplotlib.pyplot as plt

    mat = _cuad_matrix(gt)
    coverage = (mat > 0).mean().sort_values(ascending=True).tail(20)
    fig, ax = plt.subplots(figsize=(9, 7))
    ax.barh(coverage.index, 100 * coverage.values, color="#DD8452")
    for i, v in enumerate(coverage.values):
        ax.text(100 * v + 0.6, i, f"{100*v:.0f}%", va="center", fontsize=8)
    ax.set_xlabel("% of contracts containing clause")
    ax.set_title("Top 20 CUAD clauses by contract coverage (n=509)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "10_cuad_top_clauses.png")
    plt.close(fig)


def fig_cuad_cooccurrence(gt: pd.DataFrame) -> None:
    """11 — clause co-occurrence heatmap (phi coefficient, top 15 clauses)."""
    import matplotlib.pyplot as plt

    mat = _cuad_matrix(gt)
    presence = (mat > 0).astype(int)
    top = presence.sum().sort_values(ascending=False).head(15).index
    sub = presence[top]

    # phi coefficient
    n = len(sub)
    cols = []
    for a in top:
        row = []
        for b in top:
            n11 = ((sub[a] == 1) & (sub[b] == 1)).sum()
            n1_ = (sub[a] == 1).sum()
            n_1 = (sub[b] == 1).sum()
            denom = np.sqrt(n1_ * (n - n1_) * n_1 * (n - n_1))
            phi = (n * n11 - n1_ * n_1) / denom if denom else 0
            row.append(phi)
        cols.append(row)
    phi = pd.DataFrame(cols, index=top, columns=top)

    fig, ax = plt.subplots(figsize=(10, 8.5))
    im = ax.imshow(phi.values, cmap="RdBu_r", vmin=-0.5, vmax=1.0)
    ax.set_xticks(np.arange(len(top)))
    ax.set_yticks(np.arange(len(top)))
    ax.set_xticklabels(top, rotation=45, ha="right", fontsize=7)
    ax.set_yticklabels(top, fontsize=7)
    for i in range(len(top)):
        for j in range(len(top)):
            ax.text(j, i, f"{phi.values[i, j]:.2f}", ha="center", va="center", fontsize=5.5)
    ax.set_title("CUAD clause co-occurrence (phi coefficient, top 15)")
    fig.colorbar(im, ax=ax, shrink=0.8, label="phi")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "11_cuad_cooccurrence.png")
    plt.close(fig)


def fig_cuad_spans_distribution(gt: pd.DataFrame) -> None:
    """12 — ridgeline of span-count distribution for top clauses."""
    import matplotlib.pyplot as plt
    from scipy.stats import gaussian_kde

    mat = _cuad_matrix(gt)
    top = (mat > 0).sum().sort_values(ascending=False).head(8).index

    fig, axes = plt.subplots(len(top), 1, figsize=(10, 11), sharex=True)
    for ax, clause in zip(axes, top):
        vals = mat[clause].values
        x = np.linspace(0, min(vals.max(), 20), 200)
        if (vals > 0).sum() > 1:
            kde = gaussian_kde(vals[vals > 0])
            ax.fill_between(x, kde(x), alpha=0.5, color="#4C72B0")
            ax.plot(x, kde(x), color="#4C72B0", lw=1)
        ax.axvline(vals.mean(), color="red", ls="--", lw=1)
        ax.set_ylabel(clause, fontsize=7, rotation=0, ha="right", va="center")
        ax.set_yticks([])
        ax.set_xlim(0, 20)
        ax.tick_params(labelsize=7)
    axes[0].set_title("CUAD span-count distributions (top 8 clauses by presence)")
    axes[-1].set_xlabel("spans per contract (red = mean)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "12_cuad_spans_distribution.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.3 MAUD task deep-dive (merger agreements)
# ---------------------------------------------------------------------------

def _maud_frame(gt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in gt[gt["expected"] == "merger_agreement"].iterrows():
        labels = _parse_labels(r["maud_clause_labels"])
        for task, info in (labels or {}).items():
            rows.append({
                "filename": r["filename"],
                "task": task,
                "category": info.get("category", "Unknown") if isinstance(info, dict) else "Unknown",
                "answer": info.get("answer", "") if isinstance(info, dict) else str(info),
                "excerpt_chars": info.get("excerpt_chars", 0) if isinstance(info, dict) else 0,
            })
    return pd.DataFrame(rows)


def fig_maud_task_frequency(gt: pd.DataFrame) -> None:
    """13 — all MAUD tasks by frequency."""
    import matplotlib.pyplot as plt

    mf = _maud_frame(gt)
    counts = mf.groupby("task")["filename"].nunique().sort_values()
    fig, ax = plt.subplots(figsize=(9, 8))
    ax.barh(counts.index, counts.values, color="#55A868")
    for i, v in enumerate(counts.values):
        ax.text(v + 0.8, i, str(v), va="center", fontsize=8)
    ax.set_xlabel("merger agreements containing task (n=152)")
    ax.set_title("MAUD task frequency across merger agreements")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "13_maud_task_frequency.png")
    plt.close(fig)


def fig_maud_answer_distribution(gt: pd.DataFrame) -> None:
    """14 — answer distribution per task (top 10 by coverage)."""
    import matplotlib.pyplot as plt

    mf = _maud_frame(gt)
    top = mf.groupby("task")["filename"].nunique().sort_values(ascending=False).head(10).index
    subset = mf[mf["task"].isin(top)]
    pivot = pd.crosstab(subset["task"], subset["answer"])

    fig, ax = plt.subplots(figsize=(12, 6.5))
    pivot.div(pivot.sum(axis=1), axis=0).plot.barh(ax=ax, stacked=True, cmap="tab20", width=0.85)
    ax.set_xlabel("share of agreements")
    ax.set_ylabel("")
    ax.set_title("MAUD answer distribution per task (top 10 by coverage)")
    ax.legend(fontsize=6, loc="lower right", ncol=2)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "14_maud_answer_distribution.png")
    plt.close(fig)


def fig_maud_category_coverage(gt: pd.DataFrame) -> None:
    """15 — tasks per MAUD category (grouped bars of task coverage)."""
    import matplotlib.pyplot as plt

    mf = _maud_frame(gt)
    cats = sorted(mf["category"].unique())
    task_cov = mf.groupby("task")["filename"].nunique() / 152
    data = {}
    for cat in cats:
        tasks = mf[mf["category"] == cat]["task"].unique()
        data[cat] = [task_cov[t] for t in sorted(tasks, key=lambda t: -task_cov[t])]

    fig, axes = plt.subplots(len(cats), 1, figsize=(9, 0.9 * len(cats) + 3), sharex=True)
    if len(cats) == 1:
        axes = [axes]
    for ax, cat in zip(axes, cats):
        ax.bar(range(len(data[cat])), data[cat], color="#DD8452", width=0.6)
        ax.set_ylabel(cat, fontsize=7, rotation=0, ha="right", va="center")
        ax.set_yticks([])
        ax.set_ylim(0, 1.05)
        ax.tick_params(labelsize=7)
    axes[0].set_title("MAUD task coverage by category (share of 152 agreements)")
    axes[-1].set_xlabel("tasks within category")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "15_maud_category_coverage.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.4 Insurance claim analysis
# ---------------------------------------------------------------------------

def _claim_frame(gt: pd.DataFrame) -> pd.DataFrame:
    return gt[gt["expected"] == "insurance_claim"].copy()


def fig_claim_amount_distribution(gt: pd.DataFrame) -> None:
    """16 — claimed amount distribution (log scale)."""
    import matplotlib.pyplot as plt

    claims = _claim_frame(gt)
    amt = pd.to_numeric(claims["claimed_amount"], errors="coerce").dropna()
    amt = amt[amt > 0]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    axes[0].hist(amt, bins=30, color="#8172B3", edgecolor="white", alpha=0.85)
    axes[0].set_title(f"Claimed amount ($) — n={len(amt)}")
    axes[0].set_xlabel("claimed amount")
    axes[0].axvline(amt.median(), color="red", ls="--", lw=1, label=f"median ${amt.median():,.0f}")
    axes[0].legend(fontsize=8)

    axes[1].hist(np.log10(amt), bins=30, color="#8172B3", edgecolor="white", alpha=0.85)
    axes[1].set_title("Claimed amount (log10)")
    axes[1].set_xlabel("log10(claimed amount)")
    axes[1].axvline(np.log10(amt.median()), color="red", ls="--", lw=1)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "16_claim_amount_distribution.png")
    plt.close(fig)


def fig_coverage_determination(gt: pd.DataFrame) -> None:
    """17 — coverage determination donut."""
    import matplotlib.pyplot as plt

    claims = _claim_frame(gt)
    counts = claims["coverage_determination"].fillna("(missing)").value_counts()
    fig, ax = plt.subplots(figsize=(7, 5.5))
    wedges, _, autotexts = ax.pie(
        counts.values, labels=counts.index, autopct="%1.1f%%", startangle=90,
        colors=plt.cm.Set2(np.linspace(0, 1, len(counts))), pctdistance=0.78,
    )
    for t in autotexts:
        t.set_fontsize(9)
    centre = plt.Circle((0, 0), 0.62, fc="white")
    ax.add_artist(centre)
    ax.set_title(f"Coverage determination — n={len(claims)}")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "17_coverage_determination.png")
    plt.close(fig)


def fig_claim_dates_timeline(gt: pd.DataFrame) -> None:
    """18 — date of loss vs date filed scatter with delay."""
    import matplotlib.pyplot as plt

    claims = _claim_frame(gt)
    dol = pd.to_datetime(claims["date_of_loss"], errors="coerce")
    dfi = pd.to_datetime(claims["date_filed"], errors="coerce")
    mask = dol.notna() & dfi.notna()
    delay_days = (dfi[mask] - dol[mask]).dt.days

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    axes[0].scatter(dol[mask], dfi[mask], s=8, alpha=0.5, color="#8172B3")
    axes[0].set_xlabel("date of loss")
    axes[0].set_ylabel("date filed")
    axes[0].set_title(f"Loss vs filing dates (n={mask.sum()})")
    lims = [min(dol[mask].min(), dfi[mask].min()), max(dol[mask].max(), dfi[mask].max())]
    axes[0].plot(lims, lims, "r--", lw=1)

    axes[1].hist(delay_days, bins=40, color="#C44E52", edgecolor="white", alpha=0.85)
    axes[1].axvline(delay_days.median(), color="black", ls="--", lw=1,
                    label=f"median {delay_days.median():.0f} days")
    axes[1].set_xlabel("days between loss and filing")
    axes[1].set_ylabel("claims")
    axes[1].set_title("Claim processing delay")
    axes[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "18_claim_dates_timeline.png")
    plt.close(fig)


def fig_claim_subtype_fields(gt: pd.DataFrame) -> None:
    """19 — claim field fill rate by subtype heatmap."""
    import matplotlib.pyplot as plt
    import seaborn as sns

    claims = _claim_frame(gt)
    fields = [
        "claim_number", "policy_number", "insurer", "insured_party", "claim_type",
        "date_of_loss", "date_filed", "claimed_amount", "adjuster",
        "damages_description", "coverage_determination", "denial_reasons",
        "supporting_documents",
    ]
    subtypes = claims["expected_subclass"].unique()
    fill = pd.DataFrame({
        st: [claims.loc[claims["expected_subclass"] == st, f].notna().mean() for f in fields]
        for st in subtypes
    }, index=fields)

    fig, ax = plt.subplots(figsize=(8, 6.5))
    sns.heatmap(fill, annot=True, fmt=".2f", cmap="viridis", vmin=0, vmax=1,
                cbar_kws={"label": "fill rate"}, ax=ax)
    ax.set_title(f"Claim field fill rate by subtype (n={len(claims)})")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "19_claim_subtype_fields.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.5 Correspondence analysis
# ---------------------------------------------------------------------------

def _corr_frame(gt: pd.DataFrame) -> pd.DataFrame:
    return gt[gt["expected"] == "correspondence"].copy()


def fig_corr_content_topic(gt: pd.DataFrame) -> None:
    """20 — content topic treemap for correspondence."""
    import matplotlib.pyplot as plt
    import squarify

    corr = _corr_frame(gt)
    counts = corr["content_topic"].fillna("(missing)").value_counts()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    squarify.plot(
        sizes=counts.values, label=[f"{k}\n{v} ({100*v/len(corr):.0f}%)" for k, v in counts.items()],
        color=plt.cm.Set3(np.linspace(0, 1, len(counts))), alpha=0.85, ax=ax,
        text_kwargs={"fontsize": 7},
    )
    ax.set_title(f"Correspondence content topic (n={len(corr)})")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "20_corr_content_topic.png")
    plt.close(fig)


def fig_corr_intent(gt: pd.DataFrame) -> None:
    """21 — intent distribution for correspondence."""
    import matplotlib.pyplot as plt

    corr = _corr_frame(gt)
    counts = corr["intent"].fillna("(missing)").value_counts()
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar(counts.index, counts.values, color="#C44E52")
    for i, v in enumerate(counts.values):
        ax.text(i, v + 0.3, str(v), ha="center", fontsize=9)
    ax.set_ylabel("documents")
    ax.set_title(f"Correspondence intent (n={len(corr)})")
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "21_corr_intent.png")
    plt.close(fig)


def fig_corr_sentiment(gt: pd.DataFrame) -> None:
    """22 — sentiment by content topic (stacked)."""
    import matplotlib.pyplot as plt

    corr = _corr_frame(gt).copy()
    topics = corr["content_topic"].fillna("(missing)").value_counts().head(8).index
    subset = corr[corr["content_topic"].isin(topics)].copy()
    sent_col = "sentiment_label"
    subset[sent_col] = subset[sent_col].fillna("(none)")
    pivot = pd.crosstab(subset["content_topic"], subset[sent_col])

    fig, ax = plt.subplots(figsize=(9, 5))
    pivot.div(pivot.sum(axis=1), axis=0).plot.bar(ax=ax, stacked=True, cmap="RdYlGn", width=0.8)
    ax.set_ylabel("share")
    ax.set_title("Sentiment by content topic (correspondence)")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=25)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "22_corr_sentiment.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.6 Class imbalance & stratification
# ---------------------------------------------------------------------------

def fig_imbalance_treemap(gt: pd.DataFrame) -> None:
    """23 — hierarchical treemap: type → subclass → count."""
    import matplotlib.pyplot as plt
    import squarify

    groups = gt.groupby(["expected", "expected_subclass"]).size()
    counts = groups.values
    labels = [f"{k[0]}\n{k[1]}\n{v}" for k, v in groups.items() if v >= 3]
    sizes = [v for k, v in groups.items() if v >= 3]
    colors = []
    for k in groups.keys():
        if groups[k] >= 3:
            colors.append(TYPE_COLORS[k[0]])

    fig, ax = plt.subplots(figsize=(12, 7))
    squarify.plot(sizes=sizes, label=labels, color=colors, alpha=0.85, ax=ax,
                  text_kwargs={"fontsize": 5.5})
    ax.set_title("Corpus composition — doc_type → subclass (strata with n≥3)")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "23_imbalance_treemap.png")
    plt.close(fig)


def fig_strata_imbalance_ratio(gt: pd.DataFrame) -> None:
    """24 — train/test ratio per stratum, flagging skewed strata."""
    import matplotlib.pyplot as plt

    strata = gt.groupby(["expected", "expected_subclass", "split"]).size().unstack(fill_value=0)
    strata = strata[(strata.sum(axis=1) > 0)]
    ratios = strata["test"] / strata.sum(axis=1).clip(lower=1)
    ratios = ratios.sort_values(ascending=False)
    labels = [f"{k[0][:4]}:{k[1]}" for k in ratios.index]

    fig, ax = plt.subplots(figsize=(10, 9))
    colors = ["#C44E52" if (v < 0.05 or v > 0.25) else "#4C72B0" for v in ratios.values]
    ax.barh(range(len(ratios)), 100 * ratios.values, color=colors)
    ax.axvline(10, color="black", ls="--", lw=1, label="expected 10% test share")
    ax.set_yticks(range(len(ratios)))
    ax.set_yticklabels(labels, fontsize=6.5)
    ax.set_xlabel("test share %")
    ax.set_title("Test share per stratum (red = deviation from 10% rule)")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "24_strata_imbalance_ratio.png")
    plt.close(fig)


def fig_minority_strata(gt: pd.DataFrame) -> None:
    """25 — minority strata (<10 samples) detail."""
    import matplotlib.pyplot as plt

    strata = gt.groupby(["expected", "expected_subclass"]).size().sort_values()
    minority = strata[strata < 10]
    if len(minority) == 0:
        return
    labels = [f"{k[0][:4]}:{k[1]}" for k in minority.index]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(range(len(minority)), minority.values, color="#DD8452")
    for i, v in enumerate(minority.values):
        ax.text(v + 0.15, i, str(v), va="center", fontsize=9)
    ax.set_yticks(range(len(minority)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("rows")
    ax.set_title(f"Minority strata (n < 10) — {len(minority)} strata, "
                 f"{minority.sum()} rows total")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "25_minority_strata.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.7 Temporal & provenance
# ---------------------------------------------------------------------------

def _temporal_frame(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, dt in enumerate(gt["expected"]):
        m = meta.iloc[i]
        year = None
        for col in ("date", "filing_date", "year"):
            if col in meta.columns:
                v = m.get(col)
                if v:
                    s = pd.to_numeric(v, errors="coerce")
                    y = pd.to_datetime(v, errors="coerce", format="mixed").year
                    year = y if pd.notna(y) else s
                    if pd.notna(year):
                        break
        if pd.notna(year):
            rows.append({"doc_type": dt, "year": int(year), "filename": gt.iloc[i]["filename"]})
    return pd.DataFrame(rows)


def fig_filing_date_timeline(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> None:
    """26 — document count by year, per doc_type."""
    import matplotlib.pyplot as plt

    tf = _temporal_frame(blind, gt, meta)
    if tf.empty:
        return
    years = sorted(tf["year"].unique())
    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.8 / max(len(DOC_TYPES), 1)
    for i, d in enumerate(DOC_TYPES):
        counts = [tf.loc[(tf["year"] == y) & (tf["doc_type"] == d)].shape[0] for y in years]
        ax.bar([y + (i - 2) * width for y in years], counts, width=width,
               label=d, color=TYPE_COLORS[d])
    ax.set_xticks(years)
    ax.set_xlabel("year")
    ax.set_ylabel("documents")
    ax.set_title("Document counts by year and doc_type")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "26_filing_date_timeline.png")
    plt.close(fig)


def fig_source_proportions(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> None:
    """27 — source dataset donut."""
    import matplotlib.pyplot as plt

    src = meta["source_dataset"].fillna("(unknown)")
    counts = src.value_counts()
    fig, ax = plt.subplots(figsize=(8, 5.5))
    wedges, _, autotexts = ax.pie(
        counts.values, labels=[f"{k}\n{v} ({100*v/len(src):.0f}%)" for k, v in counts.items()],
        autopct="", startangle=90, colors=plt.cm.tab10(np.linspace(0, 1, len(counts))),
    )
    ax.set_title(f"Source dataset proportions (n={len(src)})")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "27_source_proportions.png")
    plt.close(fig)


def fig_date_span_by_type(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> None:
    """28 — date range per doc_type."""
    import matplotlib.pyplot as plt

    tf = _temporal_frame(blind, gt, meta)
    if tf.empty:
        return
    ranges = []
    for d in DOC_TYPES:
        sub = tf[tf["doc_type"] == d]["year"]
        if len(sub):
            ranges.append({"doc_type": d, "min": sub.min(), "max": sub.max(),
                           "median": sub.median(), "n": len(sub)})
    rdf = pd.DataFrame(ranges)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    for i, row in rdf.iterrows():
        ax.plot([row["min"], row["max"]], [i, i], lw=3, color=TYPE_COLORS[row["doc_type"]])
        ax.scatter(row["min"], i, marker="|", s=80, color="black")
        ax.scatter(row["max"], i, marker="|", s=80, color="black")
        ax.scatter(row["median"], i, marker="o", s=40, color="white", edgecolor="black", zorder=5)
        ax.text(row["max"] + 0.15, i, f"n={row['n']}", va="center", fontsize=8)
    ax.set_yticks(range(len(rdf)))
    ax.set_yticklabels(rdf["doc_type"], fontsize=9)
    ax.set_xlabel("year")
    ax.set_title("Document year span by doc_type (dots = median)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "28_date_span_by_type.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# 3.8 Metadata correlation
# ---------------------------------------------------------------------------

def fig_metadata_correlation(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> None:
    """29 — phi correlation between binary metadata fields."""
    import matplotlib.pyplot as plt

    interesting = [c for c in meta.columns if meta[c].nunique(dropna=True) <= 20 and c not in
                   ("exhibit_url", "pdf_path", "source_dataset", "message_id", "record_id", "filer")]
    sub = meta[interesting].fillna("").apply(lambda s: s != "")
    n = len(sub)
    cols = sub.columns
    phi = pd.DataFrame(index=cols, columns=cols, dtype=float)
    for a in cols:
        for b in cols:
            n11 = ((sub[a]) & (sub[b])).sum()
            n1_ = sub[a].sum()
            n_1 = sub[b].sum()
            denom = np.sqrt(n1_ * (n - n1_) * n_1 * (n - n_1))
            phi.loc[a, b] = (n * n11 - n1_ * n_1) / denom if denom else 0

    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(phi.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(cols)))
    ax.set_yticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=90, fontsize=6)
    ax.set_yticklabels(cols, fontsize=6)
    ax.set_title("Metadata field co-presence (phi coefficient)")
    fig.colorbar(im, ax=ax, shrink=0.75, label="phi")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "29_metadata_correlation.png")
    plt.close(fig)


def fig_metadata_cardinality(meta: pd.DataFrame) -> None:
    """30 — unique value cardinality per metadata field."""
    import matplotlib.pyplot as plt

    card = meta.nunique(dropna=True).sort_values(ascending=True)
    fig, ax = plt.subplots(figsize=(9, 10))
    ax.barh(card.index, card.values, color="#55A868")
    ax.set_xscale("log")
    ax.set_xlabel("unique values (log)")
    ax.set_title("Metadata field cardinality (43 keys)")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "30_metadata_cardinality.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Tables & orchestration
# ---------------------------------------------------------------------------

def save_eda_tables(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> dict:
    """Save all P3 tables to CSV."""
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    df = _text_frame(blind, gt)

    # Text stats
    text_stats = compute_token_stats(
        [{"expected": r.doc_type, "doc_text": ""} for r in df.itertuples()], by_type=True
    )
    text_stats.to_csv(TABLE_DIR / "text_length_stats_by_type.csv", index=False)

    # Token budget coverage
    toks = df["tokens"].values
    budget_cov = budget_coverage(toks)
    budget_cov.to_csv(TABLE_DIR / "token_budget_coverage.csv", index=False)

    # CUAD stats
    if (gt["expected"] == "contract").any():
        mat = _cuad_matrix(gt)
        cuad_stats = pd.DataFrame({
            "clause": mat.columns,
            "contracts_present": (mat > 0).sum().values,
            "coverage_pct": 100 * (mat > 0).mean().values,
            "mean_spans": mat.apply(lambda s: s[s > 0].mean() if (s > 0).any() else 0).values,
            "max_spans": mat.max().values,
        })
        cuad_stats.to_csv(TABLE_DIR / "cuad_clause_stats.csv", index=False)

        presence = (mat > 0).astype(int)
        top = presence.sum().sort_values(ascending=False).head(15).index
        n = len(presence)
        cooc = pd.DataFrame(index=top, columns=top, dtype=float)
        for a in top:
            for b in top:
                n11 = ((presence[a] == 1) & (presence[b] == 1)).sum()
                n1_ = (presence[a] == 1).sum()
                n_1 = (presence[b] == 1).sum()
                denom = np.sqrt(n1_ * (n - n1_) * n_1 * (n - n_1))
                cooc.loc[a, b] = (n * n11 - n1_ * n_1) / denom if denom else 0
        cooc.to_csv(TABLE_DIR / "cuad_cooccurrence_matrix.csv")

    # MAUD stats
    if (gt["expected"] == "merger_agreement").any():
        mf = _maud_frame(gt)
        if len(mf):
            maud_stats = mf.groupby("task").agg(
                agreements=("filename", "nunique"),
                categories=("category", lambda s: s.mode()[0] if len(s) else ""),
                answers=("answer", lambda s: ",".join(sorted(set(s.astype(str))))),
            ).reset_index()
            maud_stats["coverage_pct"] = 100 * maud_stats["agreements"] / (gt["expected"] == "merger_agreement").sum()
            maud_stats.to_csv(TABLE_DIR / "maud_task_stats.csv", index=False)

    # Claim stats
    if (gt["expected"] == "insurance_claim").any():
        claims = _claim_frame(gt)
        amt = pd.to_numeric(claims["claimed_amount"], errors="coerce").dropna()
        amt = amt[amt > 0]
        pd.DataFrame({
            "stat": ["count", "mean", "median", "std", "min", "max"],
            "value": [len(amt), amt.mean(), amt.median(), amt.std(), amt.min(), amt.max()],
        }).to_csv(TABLE_DIR / "claim_amount_stats.csv", index=False)

        fields = [
            "claim_number", "policy_number", "insurer", "insured_party", "claim_type",
            "date_of_loss", "date_filed", "claimed_amount", "adjuster",
            "damages_description", "coverage_determination", "denial_reasons",
            "supporting_documents",
        ]
        subtypes = claims["expected_subclass"].unique()
        fill = pd.DataFrame({
            st: [claims.loc[claims["expected_subclass"] == st, f].notna().mean() for f in fields]
            for st in subtypes
        }, index=fields)
        fill.to_csv(TABLE_DIR / "claim_field_coverage.csv")

    # Correspondence stats
    if (gt["expected"] == "correspondence").any():
        corr = _corr_frame(gt)
        topic_intent = pd.crosstab(
            corr["content_topic"].fillna("(missing)"),
            corr["intent"].fillna("(missing)"),
        )
        topic_intent.to_csv(TABLE_DIR / "correspondence_topic_intent.csv")

    # Strata imbalance detail
    strata = gt.groupby(["expected", "expected_subclass", "split"]).size().unstack(fill_value=0)
    strata = strata[(strata.sum(axis=1) > 0)]
    strata["total"] = strata.sum(axis=1)
    strata["test_share_pct"] = 100 * strata.get("test", 0) / strata["total"]
    strata = strata.sort_values("total")
    strata.to_csv(TABLE_DIR / "strata_imbalance_detailed.csv")

    # Minority report
    totals = gt.groupby(["expected", "expected_subclass"]).size().sort_values()
    minority = totals[totals < 10]
    minority.to_frame("count").to_csv(TABLE_DIR / "minority_strata_report.csv")

    # Temporal summary
    tf = _temporal_frame(blind, gt, meta)
    if len(tf):
        tf.groupby("doc_type")["year"].agg(["min", "max", "median", "count"]).round(1).to_csv(
            TABLE_DIR / "temporal_summary.csv")

    # Provenance detail
    prov = meta["source_dataset"].fillna("(unknown)").value_counts()
    prov.to_frame("count").to_csv(TABLE_DIR / "provenance_detailed.csv")

    return {
        "text_stats": TABLE_DIR / "text_length_stats_by_type.csv",
        "budget": TABLE_DIR / "token_budget_coverage.csv",
        "cuad": TABLE_DIR / "cuad_clause_stats.csv",
        "maud": TABLE_DIR / "maud_task_stats.csv",
        "claims": TABLE_DIR / "claim_amount_stats.csv",
        "corr": TABLE_DIR / "correspondence_topic_intent.csv",
    }


def run(save: bool = True) -> dict:
    """Generate all P3 static visualizations + tables."""
    setup_matplotlib()
    blind, gt, meta = _load_all()

    # Text & token
    fig_text_length_violin(blind, gt)
    fig_token_budget_coverage(blind, gt)
    fig_text_length_ecdf(blind, gt)
    fig_length_by_subclass(blind, gt)

    # CUAD
    fig_cuad_clause_presence(gt)
    fig_cuad_span_counts(gt)
    fig_cuad_top_clauses(gt)
    fig_cuad_cooccurrence(gt)
    fig_cuad_spans_distribution(gt)

    # MAUD
    fig_maud_task_frequency(gt)
    fig_maud_answer_distribution(gt)
    fig_maud_category_coverage(gt)

    # Claims
    fig_claim_amount_distribution(gt)
    fig_coverage_determination(gt)
    fig_claim_dates_timeline(gt)
    fig_claim_subtype_fields(gt)

    # Correspondence
    fig_corr_content_topic(gt)
    fig_corr_intent(gt)
    fig_corr_sentiment(gt)

    # Imbalance
    fig_imbalance_treemap(gt)
    fig_strata_imbalance_ratio(gt)
    fig_minority_strata(gt)

    # Temporal & provenance
    fig_filing_date_timeline(blind, gt, meta)
    fig_source_proportions(blind, gt, meta)
    fig_date_span_by_type(blind, gt, meta)

    # Metadata
    fig_metadata_correlation(blind, gt, meta)
    fig_metadata_cardinality(meta)

    tables = save_eda_tables(blind, gt, meta) if save else {}
    return {"figures": 30, "tables": tables}


if __name__ == "__main__":
    run()