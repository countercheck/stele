"""SQLAlchemy ORM models for the operational (app) schema.

Tables are created and altered via Alembic migrations, not from this metadata;
these classes exist so the API can read and write rows with typed attributes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP


class Base(DeclarativeBase):
    pass


class SurveyDefinition(Base):
    __tablename__ = "survey_definitions"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    definition_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    definition_hash: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class RawResponse(Base):
    __tablename__ = "raw_responses"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    respondent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_version: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    shown_questions: Mapped[list[Any] | None] = mapped_column(JSONB, default=None)
    client_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    # Frozen copy of the published definition (+ its hash and published_at) the
    # response was answered against. Lets dbt build dimensions from raw_responses
    # alone — keeping it the sole, reproducible ETL source (invariant 1/4, NFR-1)
    # — without reading app.survey_definitions. Nullable like the other content
    # columns so the M2 tombstone workflow can null it on withdrawal.
    definition_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class Response(Base):
    __tablename__ = "responses"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_response_id: Mapped[int] = mapped_column(BigInteger)
    respondent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    survey_version: Mapped[int] = mapped_column(Integer)
    submitted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class ResponseItem(Base):
    __tablename__ = "response_items"
    __table_args__ = {"schema": "app"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    response_id: Mapped[int] = mapped_column(BigInteger)
    question_name: Mapped[str] = mapped_column(Text)
    value: Mapped[Any | None] = mapped_column(JSONB, default=None)
