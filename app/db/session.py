import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine
)
from app.db.base import Base

# Database path resolution
DEFAULT_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DEFAULT_DB_DIR, exist_ok=True)
DEFAULT_DB_FILE = os.path.join(DEFAULT_DB_DIR, "geosmin_v2.db")

DATABASE_URL = os.environ.get(
    "GEOSMIN_DATABASE_URL",
    f"sqlite+aiosqlite:///{DEFAULT_DB_FILE}"
)

# Async engine configured for high-concurrency SQLite operations
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 5.0,  # 5000ms busy timeout at driver layer
    },
    pool_pre_ping=True,
    echo=False,
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_concurrency_pragmas(dbapi_connection, connection_record):
    """
    Configure SQLite for maximum write concurrency and safety:
    1. WAL (Write-Ahead Logging): Allows non-blocking concurrent readers while writing.
    2. synchronous=NORMAL: Safe in WAL mode, eliminates disk sync on every single commit.
    3. busy_timeout=5000ms: Queues competing writers for up to 5 seconds before failing.
    4. foreign_keys=ON: Guarantees referential integrity.
    5. cache_size=-64000: 64MB in-memory page cache.
    6. temp_store=MEMORY: Temporary tables and indexes kept in RAM.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA cache_size=-64000;")
    cursor.execute("PRAGMA temp_store=MEMORY;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency yielding an async session.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def immediate_transaction(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager that acquires an immediate write lock (BEGIN IMMEDIATE).
    This eliminates upgrade deadlocks (SQLITE_BUSY) in SQLite when multiple
    coroutines perform transactional read-check-write patterns.
    """
    conn = await session.connection()
    await conn.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise


async def init_db(target_engine: AsyncEngine = engine):
    """
    Initialize all tables in the SQLite database.
    """
    # Import models here to ensure metadata registration
    import app.models.db_models  # noqa: F401

    async with target_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db(target_engine: AsyncEngine = engine):
    """
    Gracefully close and dispose the connection pool.
    """
    await target_engine.dispose()
