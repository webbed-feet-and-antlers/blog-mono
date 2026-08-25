"""LLM-as-judge plumbing.

The judge is a DEDICATED, stronger model than the generator (settings.
evals_judge_model) running at temperature 0 through the same OpenRouter
client/retry path as everything else — so a model never grades its own
failure modes, and judge verdicts are as reproducible as the API allows.
"""

from __future__ import annotations

import asyncio

from deepeval.metrics import GEval
from deepeval.models import DeepEvalBaseLLM

from app import llm as app_llm
from app.config import settings


class JudgeLLM(DeepEvalBaseLLM):
    """Routes DeepEval judge prompts through the app's OpenRouter client."""

    def load_model(self):  # pragma: no cover - required by the interface
        return None  # the real client is app.llm's singleton

    async def a_generate(self, prompt: str) -> str:
        return await app_llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            model=settings.evals_judge_model,
        )

    def generate(self, prompt: str) -> str:  # sync path, used rarely
        return asyncio.run(self.a_generate(prompt))

    def get_model_name(self) -> str:
        return settings.evals_judge_model


_judge: JudgeLLM | None = None


def judge() -> JudgeLLM:
    global _judge
    if _judge is None:
        _judge = JudgeLLM()
    return _judge


def rubric(
    name: str,
    criteria: str,
    evaluation_params: list,
    threshold: float = 0.7,
    evaluation_steps: list[str] | None = None,
) -> GEval:
    """Build a GEval LLM-judge metric on the dedicated judge model.

    `evaluation_params` selects which LLMTestCase fields the judge sees,
    e.g. [SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT].
    """
    return GEval(
        name=name,
        criteria=criteria,
        evaluation_steps=evaluation_steps,
        evaluation_params=evaluation_params,
        model=judge(),
        threshold=threshold,
        strict_mode=False,  # a 0 score shouldn't crash the suite
        async_mode=True,
    )
