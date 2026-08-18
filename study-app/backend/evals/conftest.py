"""Eval-suite fixtures.

Mirrors tests/conftest.py: DB_PATH is set BEFORE any app import (the engine
in app.db reads settings.db_url at import time), background LLM features are
off, and each eval session gets a throwaway SQLite file. DeepEval telemetry
is opted out — everything stays local.

Unlike tests/, the eval suites intentionally make REAL LLM calls through the
app's OpenRouter client (generator + dedicated judge) — that is the point.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="study-app-evals-"))
os.environ["DB_PATH"] = str(_TMP / "evals.db")
os.environ["AUTO_GENERATE_FLASHCARDS"] = "false"
os.environ["AUTO_RENAME_FILES"] = "false"
os.environ["PROACTIVE_ENABLED"] = "false"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"
os.environ["DEEPEVAL_UPDATE_WARNING_OPT_IN"] = "0"

import pytest

from app.db import SessionLocal, init_db

from evals import report


@pytest.fixture(autouse=True)
async def _ready_db():
    """Ensure tables exist (no lifespan in eval runs)."""
    await init_db()
    yield


@pytest.fixture
async def db():
    """A direct DB session for seeding + asserting."""
    async with SessionLocal() as session:
        yield session


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Attribute each test's wall-clock to the suite(s) it recorded into —
    per-suite runtime lands in the report, so the slowest suite is always
    identifiable."""
    import time as _time

    start = _time.monotonic()
    n0 = len(report.RECORDS)
    yield
    elapsed = _time.monotonic() - start
    suites = {r["suite"] for r in report.RECORDS[n0:]}
    for s in suites:
        report.SUITE_SECONDS[s] = report.SUITE_SECONDS.get(s, 0.0) + elapsed


def pytest_sessionfinish(session, exitstatus):
    """Aggregate the run's recorded metrics into evals/reports/ + EVALS.md."""
    report.flush_reports()
