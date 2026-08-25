"""Evaluation harness for the study app's AI features.

Suites live in `evals/suites/` and are pytest files marked `@pytest.mark.evals`.
They exercise the REAL production functions (app.agent.tools, planner,
reflection, fsrs_scheduler, recommend.engine) against public datasets
prepared by `python -m evals.data`. See evals/README.md.
"""
