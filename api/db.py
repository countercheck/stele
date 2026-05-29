"""Async database engine and session wiring."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Local dev defaults to the dev-container superuser; CI and prod set this to the
# least-privileged stele_api role so missing grants fail loudly (see CLAUDE.md).
DEFAULT_DATABASE_URL = "postgresql+psycopg://stele_dev:dev@localhost:5432/stele"


def get_database_url() -> str:
    return os.environ.get("STELE_DATABASE_URL", DEFAULT_DATABASE_URL)


def get_analyst_database_url() -> str:
    """Connection string for read-only marts access (the analyst surface).

    stele_api has no marts grant by design (CLAUDE.md schema table): the
    operational role can't read the warehouse. Analytical reads — the survey
    export — go over a separate connection as stele_analyst, which reads marts
    only. Falls back to the main URL so the dev-container superuser (which can
    read everything) just works; CI and prod set this to stele_analyst.
    """
    return os.environ.get("STELE_ANALYST_DATABASE_URL", get_database_url())


engine = create_async_engine(get_database_url(), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

analyst_engine = create_async_engine(get_analyst_database_url(), pool_pre_ping=True)
AnalystSessionLocal = async_sessionmaker(analyst_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session."""
    async with SessionLocal() as session:
        yield session


async def get_analyst_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a read-only marts session (stele_analyst).

    Kept distinct from ``get_session`` so the only code path with marts access is
    the one that needs it; the app role still can't reach the warehouse.
    """
    async with AnalystSessionLocal() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
AnalystSessionDep = Annotated[AsyncSession, Depends(get_analyst_session)]
