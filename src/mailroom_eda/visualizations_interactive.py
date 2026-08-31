"""P4 — interactive Plotly HTML visualizations for the docclass corpus."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import (
    CUAD_CLAUSES,
    DOC_TYPES,
    INTERACTIVE_FIG_DIR,
    TOKEN_BUDGETS,
    TYPE_COLORS,
)
from .download import load_default, load_ground_truth
from .integrity import _meta_series
from .visualizations import (
    _corr_frame,
    _cuad_matrix,
    _maud_frame,
    _parse_labels,
    _text_frame,
    _temporal_frame,
)

try:
    import plotly.express as px
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False


def _save(fig, name: str) -> str:
    path = INTERACTIVE_FIG_DIR / f"{name}.html"
    fig.write_html(str(path))
    return str(path)


def html_text_length_violin(blind: pd.DataFrame, gt: pd.DataFrame) -> str:
    df = _text_frame(blind, gt)
    fig = px.violin(
        df, x="doc_type", y="chars", color="doc_type",
        color_discrete_map=TYPE_COLORS, log_y=True, points="outliers",
        box=True, title="Document length by doc_type (log scale)",
    )
    fig.update_layout(xaxis_title="doc_type", yaxis_title="characters (log)")
    return _save(fig, "04_text_length_violin")


def html_token_budget_coverage(blind: pd.DataFrame, gt: pd.DataFrame) -> str:
    df = _text_frame(blind, gt)
    rows = []
    for d in DOC_TYPES:
        if d not in df["doc_type"].values:
            continue
        toks = df.loc[df["doc_type"] == d, "tokens"].values
        for b in TOKEN_BUDGETS:
            rows.append({"doc_type": d, "budget": f"{b//1024}k", "pct": 100 * (toks <= b).mean()})
    cov = pd.DataFrame(rows)
    fig = px.bar(cov, x="budget", y="pct", color="doc_type", barmode="group",
                 color_discrete_map=TYPE_COLORS,
                 title="Token budget coverage by doc_type (heuristic chars/4)",
                 labels={"pct": "cumulative % of documents", "budget": "token budget"})
    return _save(fig, "05_token_budget_coverage")


def html_text_length_ecdf(blind: pd.DataFrame, gt: pd.DataFrame) -> str:
    df = _text_frame(blind, gt)
    fig = go.Figure()
    for d in DOC_TYPES:
        toks = np.sort(df.loc[df["doc_type"] == d, "tokens"].values)
        y = np.arange(1, len(toks) + 1) / len(toks)
        fig.add_trace(go.Scatter(x=toks, y=y, mode="lines", name=f"{d} (n={len(toks)})",
                                 line=dict(color=TYPE_COLORS[d], width=2)))
    for b in (4096, 8192, 16384, 32768):
        fig.add_vline(b, line_dash="dot", line_color="grey")
    fig.update_xaxes(type="log", title="estimated tokens (log)")
    fig.update_yaxes(title="empirical CDF")
    fig.update_layout(title="Token-length ECDF by doc_type", hovermode="x unified")
    return _save(fig, "06_text_length_ecdf")


def html_cuad_clause_presence(gt: pd.DataFrame) -> str:
    mat = _cuad_matrix(gt)
    presence = (mat > 0).astype(int)
    fig = px.imshow(
        presence.values.T, aspect="auto",
        y=presence.columns, x=presence.index,
        color_continuous_scale=[[0, "#f0f0f0"], [1, "#4C72B0"]],
        title=f"CUAD clause presence — {presence.shape[0]} contracts × {presence.shape[1]} clauses",
    )
    fig.update_layout(xaxis_title="contracts", yaxis_title="clause")
    return _save(fig, "08_cuad_clause_presence")


def html_cuad_span_counts(gt: pd.DataFrame) -> str:
    mat = _cuad_matrix(gt)
    stats = mat.apply(lambda s: pd.Series({
        "mean": s[s > 0].mean() if (s > 0).any() else 0,
        "n": (s > 0).sum(),
    })).T.sort_values("mean")
    fig = px.bar(
        stats.reset_index().rename(columns={"index": "clause"}),
        x="mean", y="clause", orientation="h", color="n",
        color_continuous_scale="Blues",
        title="CUAD mean spans per contract by clause",
        labels={"mean": "mean spans/contract", "n": "contracts with clause"},
    )
    return _save(fig, "09_cuad_span_counts")


def html_cuad_top_clauses(gt: pd.DataFrame) -> str:
    mat = _cuad_matrix(gt)
    coverage = (mat > 0).mean().sort_values().tail(20)
    fig = px.bar(
        x=100 * coverage.values, y=coverage.index, orientation="h",
        color=100 * coverage.values, color_continuous_scale="Oranges",
        title="Top 20 CUAD clauses by contract coverage",
        labels={"x": "% of contracts", "y": ""},
    )
    fig.update_layout(coloraxis_showscale=False)
    return _save(fig, "10_cuad_top_clauses")


def html_cuad_cooccurrence(gt: pd.DataFrame) -> str:
    mat = _cuad_matrix(gt)
    presence = (mat > 0).astype(int)
    top = presence.sum().sort_values(ascending=False).head(15).index
    sub = presence[top]
    n = len(sub)
    phi = pd.DataFrame(index=top, columns=top, dtype=float)
    for a in top:
        for b in top:
            n11 = ((sub[a] == 1) & (sub[b] == 1)).sum()
            n1_ = (sub[a] == 1).sum()
            n_1 = (sub[b] == 1).sum()
            denom = np.sqrt(n1_ * (n - n1_) * n_1 * (n - n_1))
            phi.loc[a, b] = (n * n11 - n1_ * n_1) / denom if denom else 0
    fig = px.imshow(phi.values, x=top, y=top, color_continuous_scale="RdBu_r",
                    range_color=[-0.5, 1.0],
                    title="CUAD clause co-occurrence (phi)")
    fig.update_layout(xaxis_title="", yaxis_title="")
    return _save(fig, "11_cuad_cooccurrence")


def html_maud_task_frequency(gt: pd.DataFrame) -> str:
    mf = _maud_frame(gt)
    counts = mf.groupby("task")["filename"].nunique().sort_values()
    fig = px.bar(
        x=counts.values, y=counts.index, orientation="h",
        color=counts.values, color_continuous_scale="Greens",
        title="MAUD task frequency (n=152 merger agreements)",
        labels={"x": "agreements", "y": ""},
    )
    fig.update_layout(coloraxis_showscale=False)
    return _save(fig, "13_maud_task_frequency")


def html_maud_answer_distribution(gt: pd.DataFrame) -> str:
    mf = _maud_frame(gt)
    top = mf.groupby("task")["filename"].nunique().sort_values(ascending=False).head(10).index
    subset = mf[mf["task"].isin(top)]
    pivot = pd.crosstab(subset["task"], subset["answer"])
    pivot_norm = pivot.div(pivot.sum(axis=1), axis=0)
    fig = go.Figure()
    for ans in pivot_norm.columns:
        fig.add_trace(go.Bar(name=ans, y=pivot_norm.index, x=pivot_norm[ans], orientation="h"))
    fig.update_layout(barmode="stack", title="MAUD answer distribution (top 10 tasks)",
                      xaxis_title="share of agreements")
    return _save(fig, "14_maud_answer_distribution")


def html_claim_amount(gt: pd.DataFrame) -> str:
    claims = gt[gt["expected"] == "insurance_claim"].copy()
    amt = pd.to_numeric(claims["claimed_amount"], errors="coerce").dropna()
    amt = amt[amt > 0]
    fig = px.histogram(
        amt, log_x=True, nbins=40, color_discrete_sequence=["#8172B3"],
        title=f"Claimed amount distribution (log scale, n={len(amt)})",
        labels={"value": "claimed amount ($)", "count": "claims"},
    )
    fig.add_vline(x=amt.median(), line_dash="dash", line_color="red",
                  annotation_text=f"median ${amt.median():,.0f}")
    return _save(fig, "16_claim_amount")


def html_coverage_determination(gt: pd.DataFrame) -> str:
    claims = gt[gt["expected"] == "insurance_claim"]
    counts = claims["coverage_determination"].fillna("(missing)").value_counts()
    fig = px.pie(values=counts.values, names=counts.index, hole=0.4,
                 title="Coverage determination", color_discrete_sequence=px.colors.qualitative.Pastel1)
    return _save(fig, "17_coverage_determination")


def html_claim_dates(gt: pd.DataFrame) -> str:
    claims = gt[gt["expected"] == "insurance_claim"]
    dol = pd.to_datetime(claims["date_of_loss"], errors="coerce")
    dfi = pd.to_datetime(claims["date_filed"], errors="coerce")
    mask = dol.notna() & dfi.notna()
    delay = (dfi[mask] - dol[mask]).dt.days
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dol[mask], y=dfi[mask], mode="markers",
                             marker=dict(size=6, opacity=0.5, color="#8172B3"),
                             customdata=delay.values,
                             hovertemplate="loss: %{x}<br>filed: %{y}<br>delay: %{customdata}d<extra></extra>",
                             name="claims"))
    fig.update_layout(title="Date of loss vs date filed", xaxis_title="date of loss",
                      yaxis_title="date filed")
    return _save(fig, "18_claim_dates")


def html_corr_topic_intent(gt: pd.DataFrame) -> str:
    corr = gt[gt["expected"] == "correspondence"]
    pivot = pd.crosstab(corr["content_topic"].fillna("(missing)"),
                        corr["intent"].fillna("(missing)"))
    fig = px.imshow(pivot.values, x=pivot.columns, y=pivot.index,
                    color_continuous_scale="Viridis", text_auto=True,
                    title="Correspondence content topic × intent")
    return _save(fig, "20_corr_topic_intent")


def html_imbalance_treemap(gt: pd.DataFrame) -> str:
    groups = gt.groupby(["expected", "expected_subclass"]).size().reset_index(name="count")
    fig = px.treemap(
        groups, path=["expected", "expected_subclass"], values="count",
        color="expected", color_discrete_map=TYPE_COLORS,
        title="Corpus composition treemap",
    )
    fig.update_traces(textinfo="label+value")
    return _save(fig, "23_imbalance_treemap")


def html_strata_ratio(gt: pd.DataFrame) -> str:
    strata = gt.groupby(["expected", "expected_subclass", "split"]).size().unstack(fill_value=0)
    strata = strata[(strata.sum(axis=1) > 0)]
    ratios = 100 * strata.get("test", 0) / strata.sum(axis=1).clip(lower=1)
    labels = [f"{k[0]} / {k[1]}" for k in ratios.index]
    colors = ["#C44E52" if (v < 5 or v > 25) else "#4C72B0" for v in ratios.values]
    fig = px.bar(x=ratios.values, y=labels, orientation="h", color=colors,
                 color_discrete_map="identity",
                 title="Test share per stratum",
                 labels={"x": "test share %", "y": ""})
    fig.add_vline(x=10, line_dash="dash", line_color="black",
                  annotation_text="expected 10%")
    return _save(fig, "24_strata_ratio")


def html_filing_timeline(blind: pd.DataFrame, gt: pd.DataFrame, meta: pd.DataFrame) -> str:
    tf = _temporal_frame(blind, gt, meta)
    if tf.empty:
        return ""
    counts = tf.groupby(["year", "doc_type"]).size().reset_index(name="count")
    fig = px.bar(counts, x="year", y="count", color="doc_type",
                 color_discrete_map=TYPE_COLORS, barmode="group",
                 title="Documents by year and doc_type")
    return _save(fig, "26_filing_timeline")


def html_source_proportions(meta: pd.DataFrame) -> str:
    counts = meta["source_dataset"].fillna("(unknown)").value_counts()
    fig = px.pie(values=counts.values, names=counts.index, hole=0.4,
                 title="Source dataset proportions",
                 color_discrete_sequence=px.colors.qualitative.D3)
    return _save(fig, "27_source_proportions")


def html_metadata_heatmap(blind: pd.DataFrame, gt: pd.DataFrame) -> str:
    from .integrity import metadata_coverage
    cov = metadata_coverage(blind, gt).reindex(DOC_TYPES)
    fig = px.imshow(cov.values, x=cov.columns, y=cov.index,
                    color_continuous_scale="Viridis", zmin=0, zmax=1,
                    title="Metadata fill rate by doc_type")
    return _save(fig, "30_metadata_heatmap")


def run(save: bool = True) -> dict:
    """Generate all interactive HTML figures."""
    if not HAS_PLOTLY:
        return {"status": "plotly not installed"}
    INTERACTIVE_FIG_DIR.mkdir(parents=True, exist_ok=True)
    blind = load_default()
    gt = load_ground_truth()
    meta = _meta_series(blind)

    out = {}
    out["04_text_length_violin"] = html_text_length_violin(blind, gt)
    out["05_token_budget_coverage"] = html_token_budget_coverage(blind, gt)
    out["06_text_length_ecdf"] = html_text_length_ecdf(blind, gt)
    out["08_cuad_presence"] = html_cuad_clause_presence(gt)
    out["09_cuad_span_counts"] = html_cuad_span_counts(gt)
    out["10_cuad_top_clauses"] = html_cuad_top_clauses(gt)
    out["11_cuad_cooccurrence"] = html_cuad_cooccurrence(gt)
    out["13_maud_frequency"] = html_maud_task_frequency(gt)
    out["14_maud_answers"] = html_maud_answer_distribution(gt)
    out["16_claim_amount"] = html_claim_amount(gt)
    out["17_coverage"] = html_coverage_determination(gt)
    out["18_claim_dates"] = html_claim_dates(gt)
    out["20_corr_topic_intent"] = html_corr_topic_intent(gt)
    out["23_treemap"] = html_imbalance_treemap(gt)
    out["24_strata_ratio"] = html_strata_ratio(gt)
    out["26_filing_timeline"] = html_filing_timeline(blind, gt, meta)
    out["27_source_proportions"] = html_source_proportions(meta)
    out["30_metadata_heatmap"] = html_metadata_heatmap(blind, gt)
    return {"figures": {k: v for k, v in out.items() if v}}


if __name__ == "__main__":
    run()