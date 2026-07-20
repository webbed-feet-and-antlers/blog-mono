"""Streamlit leaderboard viewer for MTEB per-model JSON results.

Reads ``embeddings/mteb/results/<provider>_<model>.json`` files and renders
an interactive table: models × tasks (primary metric).

Run with::

    task leaderboard:run
    # or: streamlit run mteb/leaderboard/app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from loader import (
    DEFAULT_TASK_ORDER,
    PRIMARY_METRICS,
    build_detail_frame,
    build_leaderboard_frame,
    load_results,
)

# ----- Page setup -----------------------------------------------------------


st.set_page_config(
    page_title="MTEB Leaderboard",
    page_icon=":bar_chart:",
    layout="wide",
)

st.title("MTEB Embedding Leaderboard")
st.caption(
    "Interactive view over per-model JSON results written by "
    "`python -m evaluate`. Raw metrics, no composites."
)

# Resolve results dir relative to this file:
#   mteb/leaderboard/app.py  →  mteb/results/
RESULTS_DIR: Path = Path(__file__).resolve().parent.parent / "results"


# ----- Sidebar --------------------------------------------------------------


with st.sidebar:
    st.header("Filters")
    refresh = st.button("Refresh", help="Re-scan the results directory on disk.")

    # session_state holds the cached scan so the Refresh button forces a re-glob
    # without restarting Streamlit. Clicking Refresh clears the cache and the
    # widgets below re-read whatever is now on disk.
    if refresh or "_scan" not in st.session_state:
        st.session_state["_scan"] = load_results(RESULTS_DIR)
    results = st.session_state["_scan"]

    provider_options = sorted({m.provider for m in results}) if results else []
    selected_providers = st.multiselect(
        "Providers",
        options=provider_options,
        default=provider_options,
        help="Filter models by provider.",
    )

    # Task options: only the ones we know a primary metric for.
    task_options = [t for t in DEFAULT_TASK_ORDER if t in PRIMARY_METRICS]
    selected_tasks = st.multiselect(
        "Tasks",
        options=task_options,
        default=task_options,
        help="Which task families to include as columns.",
    )

    st.divider()
    st.caption(f"Results dir:\n`{RESULTS_DIR}`")


# ----- Main view ------------------------------------------------------------


if not results:
    st.info(
        "No results yet. Run `task mteb:evaluate:<model>` to populate "
        f"`{RESULTS_DIR}`."
    )
    st.stop()


# Apply provider filter.
filtered = [m for m in results if m.provider in selected_providers]
if not filtered:
    st.warning("No models match the selected provider filter.")
    st.stop()


frame = build_leaderboard_frame(filtered, tasks=selected_tasks)


def _format_score(v: object) -> str:
    """NaN → em-dash; floats → 3-decimal string."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    try:
        return f"{float(v):.3f}"
    except (TypeError, ValueError):
        return str(v)


# Highlight the top (max) score per metric column. NaN cells are ignored.
metric_cols = [
    c for c in frame.columns
    if c not in ("Model", "Provider")
]
highlighted = frame.style.highlight_max(
    subset=metric_cols,
    axis=0,
    props="background-color: #005f73; color: white; font-weight: bold;",
) if metric_cols else frame.style

# Format metric columns to 3-decimal / em-dash.
formatted = highlighted.format(formatter={c: _format_score for c in metric_cols})

st.dataframe(
    formatted,
    use_container_width=True,
    hide_index=True,
)

st.caption(
    "Highest score per column is highlighted. Cells with no data show `—`."
)


# ----- Per-model detail -----------------------------------------------------


st.divider()
st.subheader("Model detail")

model_names = [m.model for m in filtered]
chosen = st.selectbox("Model", options=model_names)
model = next(m for m in filtered if m.model == chosen)

st.caption(
    f"Run at: `{model.run_at or 'unknown'}` — Git SHA: "
    f"`{model.git_sha or 'unknown'}` — Provider: `{model.provider}`"
)

detail = build_detail_frame(model)
if detail.empty:
    st.info("No task_results recorded for this model.")
else:
    # Pretty-format scores and counts for display only.
    display = detail.copy()
    display["score"] = display["score"].map(
        lambda v: "—" if pd.isna(v) else f"{v:.4f}"
    )
    display["n_examples"] = display["n_examples"].map(
        lambda v: "—" if v is None or pd.isna(v) else f"{int(v):,}"
    )
    display["runtime_seconds"] = display["runtime_seconds"].map(
        lambda v: "—" if v is None or pd.isna(v) else f"{float(v):.2f}s"
    )
    display = display.rename(
        columns={
            "task": "Task",
            "metric": "Metric",
            "score": "Score",
            "n_examples": "Examples",
            "runtime_seconds": "Runtime",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)


# ----- Footer ---------------------------------------------------------------


st.divider()
st.caption(
    f"{len(results)} model{'s' if len(results) != 1 else ''} loaded "
    f"({len(filtered)} shown after filters) from `{RESULTS_DIR}`."
)
