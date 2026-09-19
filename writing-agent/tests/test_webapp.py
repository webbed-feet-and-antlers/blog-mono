import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from writing_agent import webapp
from writing_agent.scoring.discourse import shape_metrics
from writing_agent.webapp import app

SLOP = """## Overview

Furthermore, it is important to note that this approach is robust and it
delivers seamless integration for every user. Additionally, the framework
is a testament to good design across the board. Moreover, teams must
consider the implications carefully before adopting anything new here.
"""


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_draft_roundtrip(client):
    resp = await client.put(
        "/api/drafts/my-post", json={"markdown": "# Hi\n\nBody."}
    )
    assert resp.status_code == 200
    assert resp.json()["slug"] == "my-post"

    listing = await client.get("/api/drafts")
    assert [d["slug"] for d in listing.json()] == ["my-post"]

    got = await client.get("/api/drafts/my-post")
    assert got.status_code == 200
    assert got.json()["markdown"] == "# Hi\n\nBody."

    missing = await client.get("/api/drafts/nope")
    assert missing.status_code == 404


async def test_index_serves_spa_or_build_instructions(client):
    resp = await client.get("/")
    if resp.status_code == 200:
        assert "root" in resp.text  # built SPA shell
    else:
        # dist not built: actionable instructions, not a crash
        assert resp.status_code == 503
        assert "npm run build" in resp.text


async def test_lint_endpoint(client):
    resp = await client.post("/api/lint", json={"markdown": SLOP})
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["passed"] is False
    assert 1 in body["report"]["flagged_blocks"]
    assert "FAIL" in body["scorecard"]


async def test_fix_endpoint_rewrites_flagged(client, monkeypatch):
    async def fake_editor(state):
        blocks = list(state["draft_blocks"])
        for i in state["flagged_blocks"]:
            blocks[i] = "Fixed. (With an aside, for good measure.)"
        return {"draft_blocks": blocks, "revision_count": 2}

    monkeypatch.setattr(webapp, "editor_node", fake_editor)
    resp = await client.post("/api/fix", json={"markdown": SLOP})
    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] is True
    assert "Fixed." in body["markdown"]
    assert "1" in body["reasons"]


async def test_fix_endpoint_no_flags_changes_nothing(client):
    resp = await client.post("/api/fix", json={"markdown": "All good here."})
    assert resp.json()["changed"] is False


async def test_score_endpoint(client, monkeypatch):
    async def fake_score(blocks, **kw):
        return {"skipped": False, "warning": None, "blocks": [], "flagged_blocks": []}

    monkeypatch.setattr(webapp, "score_blocks", fake_score)
    resp = await client.post("/api/score", json={"markdown": "Some text."})
    assert resp.status_code == 200
    assert resp.json()["skipped"] is False


async def test_revise_whole_document(client, monkeypatch):
    async def fake_chat(messages, **kw):
        assert "punchier" in messages[1]["content"]
        return "# T\n\nPunchier. Much punchier (with an aside)."

    monkeypatch.setattr(webapp, "chat", fake_chat)
    resp = await client.post(
        "/api/revise",
        json={"markdown": "# T\n\nOld text.", "instruction": "make it punchier"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "document"
    assert "Punchier." in body["markdown"]


async def test_revise_selection_only(client, monkeypatch):
    async def fake_chat(messages, **kw):
        assert "REWRITE ONLY THIS PASSAGE" in messages[1]["content"]
        return "Crisp replacement."

    monkeypatch.setattr(webapp, "chat", fake_chat)
    resp = await client.post(
        "/api/revise",
        json={
            "markdown": "# T\n\nKeep me. Replace this rambling old passage. Keep me too.",
            "instruction": "tighten",
            "selection": "Replace this rambling old passage.",
        },
    )
    body = resp.json()
    assert body["scope"] == "selection"
    assert body["markdown"] == "# T\n\nKeep me. Crisp replacement. Keep me too."


async def test_revise_block_scope(client, monkeypatch):
    async def fake_chat(messages, **kw):
        assert "EDIT ONLY THIS BLOCK" in messages[1]["content"]
        return "Rewritten block."

    monkeypatch.setattr(webapp, "chat", fake_chat)
    resp = await client.post(
        "/api/revise",
        json={"markdown": "# T\n\nOld body.", "instruction": "fix", "block_index": 1},
    )
    body = resp.json()
    assert body["scope"] == "block"
    assert body["markdown"] == "# T\n\nRewritten block."


async def test_revise_requires_instruction(client):
    resp = await client.post(
        "/api/revise", json={"markdown": "# T", "instruction": "  "}
    )
    assert resp.status_code == 422


async def test_shape_compare_endpoint(client, monkeypatch):
    async def fake_analyze(blocks, thresholds):
        return {"metrics": shape_metrics(blocks), "judge": None,
                "failures": [], "flagged_blocks": [], "passed": True}

    async def fake_semantic(blocks, *, step_min=0.12):
        return {"skipped": True, "warning": "disabled in test", "steps": []}

    monkeypatch.setattr(webapp, "analyze_discourse", fake_analyze)
    monkeypatch.setattr(webapp, "semantic_report", fake_semantic)
    resp = await client.post(
        "/api/shape/compare",
        json={"markdown": "# T\n\n" + "Word. " * 80},
    )
    assert resp.status_code == 200
    body = resp.json()
    groups = {d["group"] for d in body["docs"]}
    assert "current" in groups and "ai" in groups
    ai_docs = [d for d in body["docs"] if d["group"] == "ai"]
    assert len(ai_docs) >= 4  # full control set gives the AI group a spread
    for d in body["docs"]:
        assert d["x"] == d["x"] and d["y"] == d["y"]  # finite coords
        assert 0.0 <= d["rarity"] <= 1.0 and 0.0 <= d["human_score"] <= 1.0
    assert body["axes_names"] and "Temporal texture" in body["axes_names"]
    assert "Novelty" in body["axes_names"]  # 9th axis, Genie-style
    assert all("Novelty" in d["axes"] for d in body["docs"])
    assert "authorship" in body  # None without references, dict with them
    # The AI controls should not out-rank the current text.
    current = next(d for d in body["docs"] if d["group"] == "current")
    assert current["rarity"] > max(d["rarity"] for d in ai_docs)


async def test_versions_snapshot_list_and_revert(client, tmp_path):
    await client.put("/api/drafts/v-doc", json={"markdown": "version one"})
    await client.put("/api/drafts/v-doc", json={"markdown": "version two"})
    versions = (await client.get("/api/drafts/v-doc/versions")).json()["versions"]
    assert len(versions) == 1 and versions[0]["source"] == "human"

    current = (await client.get("/api/drafts/v-doc")).json()["markdown"]
    assert current == "version two"

    file = versions[0]["file"]
    snapshot = (await client.get(f"/api/drafts/v-doc/versions/{file}")).json()
    assert snapshot["markdown"] == "version one"

    resp = await client.post(
        "/api/drafts/v-doc/revert", json={"file": file}
    )
    assert resp.status_code == 200
    assert (await client.get("/api/drafts/v-doc")).json()["markdown"] == "version one"
    # the revert itself snapshots the overwritten content
    versions = (await client.get("/api/drafts/v-doc/versions")).json()["versions"]
    assert len(versions) == 2
    assert any(v["source"] == "revert" for v in versions)

    # no traversal
    bad = await client.get("/api/drafts/v-doc/versions/..%2F..%2Fx.md")
    assert bad.status_code == 404


class StubGraph:
    async def astream(self, initial, stream_mode="updates"):
        yield {"architect": {"outline": {"scqa": {}, "pillars": []}}}
        yield {
            "stylist": {"draft_blocks": ["# Job test", "Body."], "revision_count": 1}
        }
        yield {"audit": {"flagged_blocks": [], "lint_report": {"passed": True}}}
        yield {"finalize": {"final_post": "# Job test\n\nBody.", "scorecard": "FINAL\nlint passed: True"}}


async def test_full_draft_job_writes_workspace_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "GRAPH", StubGraph())
    resp = await client.post(
        "/api/drafts", json={"topic": "job test", "research": "", "persona": "default"}
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    for _ in range(20):  # let the background task run to completion
        await asyncio.sleep(0)

    job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["done"] is True
    assert job["error"] is None
    assert job["stage"] == "Assembling the post…"
    assert job["elapsed"] >= 0
    assert job["trace"] and job["trace"][-1]["event"].startswith("finished")
    assert job["result"]["slug"] == "job-test"
    assert (tmp_path / "job-test.md").read_text() == "# Job test\n\nBody."
    assert "FINAL" in (tmp_path / "job-test.scorecard.md").read_text()


async def test_sse_events_stream(client, monkeypatch, tmp_path):
    import asyncio as _aio

    monkeypatch.setattr(webapp, "GRAPH", StubGraph())
    resp = await client.post(
        "/api/drafts", json={"topic": "sse test", "research": "", "persona": "default"}
    )
    job_id = resp.json()["job_id"]
    for _ in range(20):
        await _aio.sleep(0)

    collected = []
    async with client.stream(
        "GET", f"/api/jobs/{job_id}/events"
    ) as stream:
        async for line in stream.aiter_lines():
            if line.startswith("data: "):
                collected.append(json.loads(line[6:]))
    kinds = [e["kind"] for e in collected]
    assert kinds[0] == "stage"
    assert "trace" in kinds and kinds[-1] == "done"
    done = collected[-1]
    assert done["result"]["slug"] == "sse-test"
