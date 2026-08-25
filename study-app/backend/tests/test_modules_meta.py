"""Module metadata tests — semester organization + partial-update semantics."""

from __future__ import annotations


async def test_module_semester_round_trip(client, db):
    resp = await client.post(
        "/api/modules",
        json={
            "title": "Linear Algebra",
            "academic_year": "2026/27",
            "term": "Autumn",
            "exam_date": "2026-12-15",
        },
    )
    assert resp.status_code == 201
    mod = resp.json()
    assert mod["academic_year"] == "2026/27"
    assert mod["term"] == "Autumn"
    assert mod["exam_date"] == "2026-12-15"

    # The tree endpoint carries the fields to the frontend.
    tree = (await client.get("/api/modules")).json()
    mine = next(m for m in tree["modules"] if m["id"] == mod["id"])
    assert mine["academic_year"] == "2026/27"
    assert mine["term"] == "Autumn"


async def test_partial_patch_preserves_other_fields(client, db):
    resp = await client.post(
        "/api/modules",
        json={"title": "Thermo", "academic_year": "2025/26", "term": "Spring"},
    )
    mod_id = resp.json()["id"]

    # Rename only — must NOT wipe the semester.
    resp = await client.patch(
        f"/api/modules/{mod_id}", json={"title": "Thermodynamics"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Thermodynamics"
    assert body["academic_year"] == "2025/26"
    assert body["term"] == "Spring"

    # Re-semester only — must NOT wipe the title.
    resp = await client.patch(
        f"/api/modules/{mod_id}",
        json={"academic_year": "2026/27", "term": "Autumn"},
    )
    body = resp.json()
    assert body["title"] == "Thermodynamics"
    assert body["academic_year"] == "2026/27"
    assert body["term"] == "Autumn"

    # Explicit null clears.
    resp = await client.patch(f"/api/modules/{mod_id}", json={"term": None})
    assert resp.json()["term"] is None
    assert resp.json()["academic_year"] == "2026/27"
