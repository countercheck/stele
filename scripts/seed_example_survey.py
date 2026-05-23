"""Seed the example survey (single-select + free-text) and a handful of responses.

Exercises the real write path (api.survey_engine.service) — including the
definition-snapshot embedding and the PII-risk routing of free-text answers — so
`dbt build` downstream has data spanning all three routing states (answered /
shown-skipped / routed-past) and both PII-risk levels. Used by the verification
runbooks (docs/verification/) and by CI.

Run:  uv run python scripts/seed_example_survey.py
Honors STELE_DATABASE_URL (CI/prod point it at a least-privileged role).

Questions:
  q1, q2   single-select (radiogroup)
  ft_low   free-text, pii_risk='low'  → value reaches marts.value_text
  ft_high  free-text, pii_risk='high' → redacted in marts; copied to
           pii.free_text_responses for the reviewer

Expected marts after `dbt build` (printed at the end for the runbook):
  dim_respondent: 4 · dim_survey_version: 1 · dim_question: 4
  dim_question_version: 4 · dim_option: 5
  fact_response_item: 16 (6 with an option_key · 3 with value_text · 4 redacted)
  fact_response: 16 · pii.free_text_responses: 2
  q1 selections — a:2  b:1  c:1     q2 selections — x:1  y:1
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from api.db import SessionLocal
from api.survey_engine import service
from api.survey_engine.schemas import ResponseSubmit

DEFINITION: dict[str, Any] = {
    "pages": [
        {
            "name": "p1",
            "elements": [
                {
                    "type": "radiogroup",
                    "name": "q1",
                    "title": "Pick one",
                    "choices": ["a", "b", "c"],
                },
                {
                    "type": "radiogroup",
                    "name": "q2",
                    "title": "Pick another",
                    "choices": ["x", "y"],
                },
                {
                    "type": "comment",
                    "name": "ft_low",
                    "title": "Anything to add? (non-identifying)",
                    "pii_risk": "low",
                    "pii_risk_rationale": "open feedback, screened as non-identifying",
                },
                {
                    "type": "comment",
                    "name": "ft_high",
                    "title": "Describe your role in your own words",
                    # pii_risk defaults to 'high' when absent; stated here for clarity.
                    "pii_risk": "high",
                },
            ],
        }
    ]
}

# (shown_questions, payload) per respondent — covers every routing state across
# both closed-ended and free-text questions:
#   R1, R2: all four shown and answered
#   R3:     q2 + ft_high shown but skipped; q1 + ft_low answered
#   R4:     only q1 shown/answered; q2, ft_low, ft_high routed past
SUBMISSIONS: list[tuple[list[str], dict[str, str]]] = [
    (
        ["q1", "q2", "ft_low", "ft_high"],
        {"q1": "a", "q2": "x", "ft_low": "great", "ft_high": "I lead the platform team"},
    ),
    (
        ["q1", "q2", "ft_low", "ft_high"],
        {"q1": "b", "q2": "y", "ft_low": "good", "ft_high": "Senior engineer at Acme"},
    ),
    (["q1", "q2", "ft_low", "ft_high"], {"q1": "a", "ft_low": "ok"}),
    (["q1"], {"q1": "c"}),
]


async def seed() -> None:
    async with SessionLocal() as session:
        survey = await service.create_draft(session, DEFINITION)
        published = await service.publish(session, survey.survey_id, survey.version)
        assert published.definition_hash is not None

        for shown_questions, payload in SUBMISSIONS:
            await service.submit_response(
                session,
                published.survey_id,
                published.version,
                ResponseSubmit(
                    definition_hash=published.definition_hash,
                    payload=payload,
                    shown_questions=shown_questions,
                    respondent_id=uuid.uuid4(),
                    client_metadata={"source": "seed_example_survey"},
                ),
            )

    print(
        f"Seeded survey {published.survey_id} v{published.version} with {len(SUBMISSIONS)} responses."
    )


if __name__ == "__main__":
    asyncio.run(seed())
