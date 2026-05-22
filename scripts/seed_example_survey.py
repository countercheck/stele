"""Seed a 2-question single-select survey and a handful of responses.

Exercises the real write path (api.survey_engine.service) — including the
definition-snapshot embedding — so `dbt build` downstream has data spanning all
three routing states (answered / shown-skipped / routed-past). Used by the M1
end-to-end verification runbook (docs/verification/m1-slice.md) and by CI.

Run:  uv run python scripts/seed_example_survey.py
Honors STELE_DATABASE_URL (CI/prod point it at a least-privileged role).

Expected marts after `dbt build` (printed at the end for the runbook):
  dim_respondent: 4 · dim_survey_version: 1 · dim_question: 2
  dim_question_version: 2 · dim_option: 5
  fact_response_item: 8 (6 with an option_key) · fact_response: 8
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
            ],
        }
    ]
}

# (shown_questions, payload) per respondent — covers every routing state:
#   R1, R2: both questions shown and answered
#   R3:     q2 shown but skipped (in shown-set, absent from payload)
#   R4:     q2 routed past (absent from shown-set and payload)
SUBMISSIONS: list[tuple[list[str], dict[str, str]]] = [
    (["q1", "q2"], {"q1": "a", "q2": "x"}),
    (["q1", "q2"], {"q1": "b", "q2": "y"}),
    (["q1", "q2"], {"q1": "a"}),
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
