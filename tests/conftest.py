import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db.async_session import get_db
from app.main import app


@pytest_asyncio.fixture
async def db_session():
    """Joined to an external transaction rolled back on teardown — never creates or
    drops tables, so the real jobs/listening_history schema (owned by migrations/init.sql)
    and any pre-existing rows are left exactly as found."""
    engine = create_async_engine(settings.async_database_url, echo=False)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session_factory = async_sessionmaker(
                bind=conn,
                class_=AsyncSession,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            session = session_factory()
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def sync_db_session():
    """Sync counterpart of db_session for worker-layer tests — same join-transaction/
    rollback isolation, same rule: never creates or drops tables."""
    engine = create_engine(settings.sync_database_url)
    try:
        with engine.connect() as conn:
            trans = conn.begin()
            session_factory = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")
            session = session_factory()
            try:
                yield session
            finally:
                session.close()
                trans.rollback()
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
