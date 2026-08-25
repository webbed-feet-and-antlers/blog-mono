"""Study plan routes — the module's adaptive plan.

GET returns the latest plan with computed staleness; POST generates or
force-regenerates (one LLM call, sync — the UI shows a planning state);
PATCH toggles a single item manually.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent import planner
from ..db import get_session
from ..models import StudyPlan
from ..schemas import PlanGenerateRequest, PlanItemPatch, StudyPlanOut

router = APIRouter(prefix="/api", tags=["plans"])
logger = logging.getLogger(__name__)


def _plan_out(plan: StudyPlan, staleness: dict) -> StudyPlanOut:
    return StudyPlanOut(
        id=plan.id,
        module_id=plan.module_id,
        version=plan.version,
        generated_at=plan.generated_at,
        stale_reasons=plan.stale_reasons or [],
        items=plan.items or [],
        meta=plan.meta or {},
        staleness=staleness,
    )


@router.get("/modules/{module_id}/plan", response_model=StudyPlanOut)
async def get_study_plan(
    module_id: str, session: AsyncSession = Depends(get_session)
):
    found = await planner.get_plan_with_staleness(session, module_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No plan for this module yet")
    plan, staleness = found
    return _plan_out(plan, staleness)


@router.post("/modules/{module_id}/plan", response_model=StudyPlanOut)
async def generate_plan(
    module_id: str,
    req: PlanGenerateRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Generate or regenerate the module's study plan now.

    Optionally sets the module's exam date in the same call (the pacer's
    one manual input), then runs the planner.
    """
    if req is not None and "exam_date" in (req.model_fields_set or set()):
        from datetime import date as _date

        from ..models import Module

        module = await session.get(Module, module_id)
        if module is None:
            raise HTTPException(status_code=404, detail="Module not found")
        module.exam_date = req.exam_date
        await session.commit()

    try:
        plan = await planner.generate_study_plan(session, module_id)
    except Exception as exc:
        logger.exception("[plans] generation failed for module %s", module_id)
        raise HTTPException(status_code=502, detail=f"Planner failed: {exc}")

    if plan is None:
        raise HTTPException(
            status_code=422,
            detail="Module not found or has no documents to plan over",
        )
    return _plan_out(plan, {"stale": False, "reasons": []})


@router.patch("/plans/{plan_id}/items/{item_id}")
async def patch_plan_item(
    plan_id: str,
    item_id: str,
    req: PlanItemPatch,
    session: AsyncSession = Depends(get_session),
):
    """Manual check-off (or undo) for items the agent can't auto-detect."""
    from datetime import datetime, timezone

    plan = await session.get(StudyPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    for item in plan.items or []:
        if item.get("id") != item_id:
            continue
        if req.status == "done":
            item["status"] = "done"
            item["done_at"] = datetime.now(timezone.utc).isoformat()
            item["done_kind"] = "manual"
            item["done_reason"] = "Marked done"
        else:
            item["status"] = "pending"
            item["done_at"] = None
            item["done_kind"] = None
            item["done_reason"] = None
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(plan, "items")  # JSON columns need explicit dirtying
        await session.commit()
        return {"status": "ok", "item": item}

    raise HTTPException(status_code=404, detail="Plan item not found")
