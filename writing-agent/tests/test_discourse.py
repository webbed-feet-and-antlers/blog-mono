from writing_agent.config import ThresholdSettings
from writing_agent.scoring.discourse import (
    AI_CONTROLS,
    analyze_discourse,
    authorship_distance,
    human_score,
    judge_axes,
    project_narrative_space,
    rarity_percentiles,
    shape_axes,
    shape_metrics,
    trigram_novelty,
)
from writing_agent.segment import split_blocks

# Linear, over-explaining, tidy, repetitive — the AI shape (StoryScope).
AI_SHAPED = """## Why our platform wins

Our platform delivers value across the organization through seamless
integration. The platform provides a robust foundation for growth. Teams
can leverage the platform to unlock new potential. The platform ensures
seamless integration for every user across the industry. The takeaway is
that our platform provides the foundation for success.

## The roadmap ahead

The roadmap continues to build on the platform foundation. Furthermore,
the roadmap delivers new capabilities each quarter. The roadmap ensures
that customers can plan their platform strategy with confidence. In short,
the roadmap is the key to long-term platform value.

## Enterprise readiness

Enterprise customers expect a platform that scales. Our platform scales.
The platform provides governance controls that enterprises require, and
the platform supports the compliance standards that regulated industries
demand. Moreover, the platform enables teams to collaborate across the
organization, because the platform was designed for collaboration from
the very first day. The platform also delivers analytics that help teams
understand their platform usage, and the platform integrates with the
tools that teams already use every single day.

Deployment follows the same philosophy. The platform deploys quickly, the
platform upgrades seamlessly, and the platform rollbacks safely when the
platform needs to recover. Administrators can configure the platform
through policies, and the platform applies those policies consistently
across every environment where the platform runs. This means the platform
reduces operational overhead, which is the point of a platform.

Security receives the same attention. The platform encrypts data at rest,
the platform encrypts data in transit, and the platform audits every
action that the platform takes. Security teams can review the platform
logs, and the platform can export those logs to the systems that security
teams already trust. When auditors ask about the platform, the platform
provides the answers that auditors need, because the platform was built
for trust. Partners who build on the platform get these guarantees too,
since the platform extends every control to every integration that the
platform certifies.

Support rounds out the story. The platform offers support at every tier,
and the platform routes tickets to the engineers who know the platform
best. Customers who adopt the platform join a community of customers, and
the community shares patterns that make the platform stronger. Training
materials teach the platform, certification programs validate the
platform, and the feedback loop improves the platform for everyone who
relies on the platform each day.

## Conclusion

The platform is the foundation. The platform is the roadmap. The takeaway
is simple: choose the platform that delivers the platform.
"""

# Jumps, retro-explanations, concessions, unresolved ends — the human shape.
HUMAN_SHAPED = """## The night it broke

The indexer fell over at 2am. We had no runbook. Six months earlier we had
debated swapping the queue (nobody wrote that down, which turned out to
matter). I spent the outage writing timeline notes on a napkin. Why three
of us? Budget.

## What we kept

Had we chosen Kafka two years earlier the failure would have differed,
though maybe not better — the consumers would have lagged instead of
crashing. That napkin is why the runbook exists now. We never fixed the
alerting gap. I still don't know whether lag beats crashes. Your mileage
will vary. It usually does.
"""


def _no_judge(text, *a, **kw):
    raise RuntimeError("judge disabled for deterministic test")


def test_shape_metrics_ai_vs_human():
    ai = shape_metrics(split_blocks(AI_SHAPED))
    human = shape_metrics(split_blocks(HUMAN_SHAPED))
    assert len(ai["summary_hit_blocks"]) >= 1  # "The takeaway is", "In short"
    assert human["temporal_jumps_per_1k"] + human["retro_explanations_per_1k"] > 0
    assert human["unresolved_markers"] >= 2  # "never fixed", "still don't know"
    assert human["incident_anchors_per_1k"] > 0  # "I spent", "we had debated"
    assert ai["top5_content_share"] > human["top5_content_share"]  # platform/roadmap spam


async def test_ai_shaped_doc_fails_on_all_storyscope_axes():
    from writing_agent.segment import split_blocks

    report = await analyze_discourse(
        split_blocks(AI_SHAPED), ThresholdSettings(), judge=_no_judge
    )
    assert not report["passed"]
    reasons = " ".join(f["reason"] for f in report["failures"])
    assert "over-explains" in reasons
    assert "linear timeline" in reasons or "tidy resolution" in reasons
    assert report["judge"] is None  # graceful judge skip


async def test_human_shaped_doc_passes_deterministic_gates():
    from writing_agent.segment import split_blocks

    report = await analyze_discourse(
        split_blocks(HUMAN_SHAPED), ThresholdSettings(), judge=_no_judge
    )
    assert report["passed"], report["failures"]


async def test_judge_high_axis_flags():
    from writing_agent.segment import split_blocks

    async def harsh_judge(text):
        return {
            "over_explains": 0.9, "linear_timeline": 0.2,
            "tidy_resolution": 0.1, "generic_abstraction": 0.3,
            "reads_like_ai": 0.8, "notes": "sermonizing",
        }

    report = await analyze_discourse(
        split_blocks(HUMAN_SHAPED), ThresholdSettings(), judge=harsh_judge
    )
    assert not report["passed"]
    reasons = " ".join(f["reason"] for f in report["failures"])
    assert "over_explains" in reasons and "reads_like_ai" in reasons


async def test_judge_axes_parses_and_clips_notes():
    async def fake_llm(messages, **kw):
        return {
            "over_explains": "0.8", "linear_timeline": 0.1,
            "tidy_resolution": 0.2, "generic_abstraction": 0.3,
            "reads_like_ai": 0.4, "notes": "x" * 500,
        }

    axes = await judge_axes("some text", llm=fake_llm)
    assert axes["over_explains"] == 0.8
    assert len(axes["notes"]) == 300


def test_shape_axes_point_toward_human():
    ai = shape_axes(shape_metrics(split_blocks(AI_SHAPED)))
    human = shape_axes(shape_metrics(split_blocks(HUMAN_SHAPED)))
    assert ai["Temporal texture"] == 0.0
    assert ai["Summary-free"] == 0.0
    assert human["Temporal texture"] > 0.2
    assert human["Unresolved ends"] == 1.0
    assert human["Incident anchors"] > ai["Incident anchors"]
    assert human["Lexical diversity"] > ai["Lexical diversity"]


def test_project_narrative_space_returns_finite_coords():
    vectors = [
        shape_axes(shape_metrics(split_blocks(AI_SHAPED))),
        shape_axes(shape_metrics(split_blocks(HUMAN_SHAPED))),
        shape_axes(shape_metrics(split_blocks(HUMAN_SHAPED.replace("2am", "3am")))),
        shape_axes(shape_metrics(split_blocks(AI_SHAPED.replace("platform", "service")))),
    ]
    coords = project_narrative_space(vectors)
    assert len(coords) == 4
    assert all(x == x and y == y for x, y in coords)  # finite (no NaN)


def test_rarity_percentiles_rank_correctly():
    assert rarity_percentiles([0.1]) == [1.0]  # single doc → trivially rarest
    assert rarity_percentiles([0.9, 0.1, 0.5]) == [1.0, 0.0, 0.5]
    ties = rarity_percentiles([0.5, 0.5])
    assert ties == [0.0, 0.0]  # ties share the lower rank


def test_ai_controls_score_below_human_fixture():
    human = human_score(shape_axes(shape_metrics(split_blocks(HUMAN_SHAPED))))
    for i, control in enumerate(AI_CONTROLS):
        control_score = human_score(shape_axes(shape_metrics(split_blocks(control))))
        assert control_score < human, f"AI control {i + 1} not machine-shaped enough"


def test_authorship_distance_human_side_and_machine_side():
    humans = [{"a": 0.75, "b": 0.85}]
    controls = [{"a": 0.10, "b": 0.10}]
    near_human = authorship_distance({"a": 0.8, "b": 0.8}, humans, controls)
    assert near_human["verdict"] == "human-side"
    assert near_human["ratio"] < 1.0
    near_ai = authorship_distance({"a": 0.1, "b": 0.15}, humans, controls)
    assert near_ai["verdict"] == "machine-side"
    assert near_ai["ratio"] > 1.0


def test_authorship_distance_requires_human_references():
    assert authorship_distance({"a": 0.5}, [], [{"a": 0.1}]) is None


def test_trigram_novelty_genie_style():
    # The human fixture recombines nothing from the controls; a text built
    # from control phrasing scores near zero.
    controls_flat = [b for c in AI_CONTROLS for b in split_blocks(c)]
    human_novelty = trigram_novelty(split_blocks(HUMAN_SHAPED), controls_flat)
    assert human_novelty > 0.9
    recombined = (
        "Our platform delivers value through seamless integration. "
        "The platform provides a robust foundation for growth, and the "
        "roadmap continues to build on the platform foundation. "
    )
    assert trigram_novelty(split_blocks(recombined), controls_flat) < 0.5
    # Self-comparison: a document is never novel against itself.
    assert trigram_novelty(split_blocks(HUMAN_SHAPED), split_blocks(HUMAN_SHAPED)) == 0.0
