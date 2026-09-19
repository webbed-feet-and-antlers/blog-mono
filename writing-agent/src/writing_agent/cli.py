"""wa — the anti-slop writing harness CLI."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

app = typer.Typer(help="Anti-slop writing harness", no_args_is_help=True)
console = Console()


@app.command()
def lint(file: Path) -> None:
    """Run deterministic linters on a markdown file and print the scorecard."""
    from .config import get_settings
    from .lint.report import run_all_linters
    from .segment import split_blocks

    report = run_all_linters(
        split_blocks(file.read_text()), get_settings().thresholds
    )
    console.print(report.scorecard())
    if not report.passed:
        raise typer.Exit(code=1)


@app.command()
def draft(
    topic: str,
    research: Path = typer.Option(
        None, help="Markdown file of raw research notes"
    ),
    persona: str = typer.Option(
        "default", help="Persona name under config/personas/"
    ),
    style_ref: list[Path] = typer.Option(
        [], "--style-ref", help="Gold-standard post to mirror (repeatable)"
    ),
) -> None:
    """Run the full pipeline; write the draft + scorecard into workspace/."""
    from .config import CONFIG_DIR, get_settings
    from .graph import run_pipeline
    from .segment import slugify

    persona_path = CONFIG_DIR / "personas" / f"{persona}.md"
    persona_text = persona_path.read_text() if persona_path.exists() else ""
    state = asyncio.run(
        run_pipeline(
            topic=topic,
            persona=persona_text,
            raw_research=research.read_text() if research else "",
            style_reference_paths=[str(p) for p in style_ref],
        )
    )
    if state.get("error"):
        console.print(f"[red]error:[/red] {state['error']}")
        raise typer.Exit(code=1)

    out_dir = get_settings().workspace_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic)
    (out_dir / f"{slug}.md").write_text(state["final_post"])
    (out_dir / f"{slug}.scorecard.md").write_text(state["scorecard"])
    console.print(state["scorecard"])
    if state.get("flagged_blocks"):
        console.print(
            f"[yellow]still flagged after max revisions: "
            f"{state['flagged_blocks']} — human review advised[/yellow]"
        )
    console.print(f"[green]wrote[/green] {out_dir / f'{slug}.md'}")


@app.command()
def score(
    file: Path,
    calibrate_dir: Path = typer.Option(
        None, help="Score all reference posts in this dir and suggest thresholds"
    ),
) -> None:
    """Surprisal audit via OpenRouter logprobs."""
    from .config import get_settings
    from .scoring.surprisal import calibrate, score_blocks
    from .segment import split_blocks

    t = get_settings().thresholds

    async def _run():
        if calibrate_dir:
            files = sorted(calibrate_dir.glob("*.md"))
            return await calibrate(files)
        return await score_blocks(
            split_blocks(file.read_text()),
            overlap_max=t.continuation_overlap_max,
            bits_max=t.continuation_bits_max,
            convergence_max=t.continuation_convergence_max,
        )

    report = asyncio.run(_run())
    console.print(report)


@app.command()
def shape(file: Path) -> None:
    """StoryScope-style discourse-shape audit (markers + LLM judge)."""
    from .config import CONFIG_DIR, get_settings
    from .scoring.discourse import (
        AI_CONTROLS,
        analyze_discourse,
        authorship_distance,
        shape_axes,
        shape_metrics,
        trigram_novelty,
    )
    from .scoring.semantic import semantic_report
    from .segment import split_blocks

    blocks = split_blocks(file.read_text())

    # One event loop for both async reports — the OpenAI client's httpx
    # pool binds to the first loop and dies with it ("Event loop is closed").
    async def _run_reports():
        report = await analyze_discourse(blocks, get_settings().thresholds)
        sem = await semantic_report(
            blocks, step_min=get_settings().thresholds.semantic_step_min
        )
        return report, sem

    report, sem = asyncio.run(_run_reports())
    console.print(report["metrics"])
    if report["judge"]:
        console.print("judge:", report["judge"])

    # Genie-style novelty vs references + AI controls.
    corpus: list[str] = [b for c in AI_CONTROLS for b in split_blocks(c)]
    refs_dir = CONFIG_DIR.parent / "references"
    human_axes = []
    if refs_dir.exists():
        for f in sorted(refs_dir.glob("*.md")):
            if f.name == "README.md":
                continue
            ref_blocks = split_blocks(f.read_text())
            corpus.extend(ref_blocks)
            if shape_metrics(ref_blocks)["words"] >= 50:
                human_axes.append(shape_axes(shape_metrics(ref_blocks)))
    novelty = trigram_novelty(blocks, corpus)
    console.print(f"novelty: {novelty:.0%} of 3-grams absent from references+controls")

    if not sem.get("skipped") and sem.get("step_max") is not None:
        console.print(
            f"semantic glide: largest step {sem['step_max']:.2f} "
            f"(needs one ≥ {get_settings().thresholds.semantic_step_min})"
            + ("  [red]FLAGGED[/red]" if sem["failed"] else "")
        )

    authorship = authorship_distance(
        shape_axes(report["metrics"]),
        human_axes,
        [shape_axes(shape_metrics(split_blocks(c))) for c in AI_CONTROLS],
    )
    if authorship:
        console.print(
            f"authorship: [bold]{authorship['verdict']}[/bold] "
            f"(dist-to-you {authorship['human_dist']} vs dist-to-AI "
            f"{authorship['ai_dist']}, ratio {authorship['ratio']})"
        )
    else:
        console.print(
            "[dim]authorship: add posts to references/ to enable[/dim]"
        )

    for f in report["failures"]:
        console.print(f"[red]flag[/red] block {f['block_index']}: {f['reason']}")
    if report["passed"]:
        console.print("[green]SHAPE PASS[/green]")


@app.command()
def ui(port: int = typer.Option(8765, help="Port for the local web editor")) -> None:
    """Launch the collaborative web editor (http://127.0.0.1:<port>)."""
    import uvicorn

    console.print(f"[green]writing-agent UI[/green] → http://127.0.0.1:{port}")
    uvicorn.run("writing_agent.webapp:app", host="127.0.0.1", port=port)
