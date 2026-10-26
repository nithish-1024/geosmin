import os
import uuid
import asyncio
import tempfile
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, text, select

from app.db.base import Base
from app.db.session import set_sqlite_concurrency_pragmas
from app.models.db_models import Patient, TokenRecord, User, utc_now
from app.routers import geosmin_v2_router
from app.services.token_redemption_service import token_service


@pytest_asyncio.fixture(scope="function")
async def test_app_and_db():
    """
    Creates an isolated SQLite WAL database and a FastAPI test app with geosmin_v2_router mounted.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, f"test_concurrency_{uuid.uuid4().hex[:8]}.db")
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 10.0},
        pool_pre_ping=True
    )

    # Register the SQLite WAL and concurrency pragmas on engine
    event.listen(engine.sync_engine, "connect", set_sqlite_concurrency_pragmas)

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )

    # Seed baseline patient and demo token "842-916"
    async with session_factory() as session:
        patient = Patient(
            id="GK-8821",
            mrn="RK-48810",
            dob="1968-05-12",
            gender="Male",
            blood_pressure="132/84",
            egfr=78.0,
            potassium=4.2,
            inr=2.3,
            revision=1,
            status="active",
            tenant_id="tenant-default"
        )
        session.add(patient)

        token = TokenRecord(
            patient_id="GK-8821",
            token_id="tok-geo-842916",
            passcode="842-916",
            raw_code="842916",
            scopes='{"medications": true, "allergies": true, "diagnoses": true, "biometrics": true}',
            is_redeemed=False,
            expires_at=utc_now() + __import__("datetime").timedelta(seconds=600),
            version=0,
            created_at=utc_now()
        )
        session.add(token)
        await session.commit()

    # Build FastAPI test app
    app = FastAPI(title="Concurrency Test Suite")
    app.include_router(geosmin_v2_router)

    # Override get_db dependency
    from app.db.session import get_db

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    yield {
        "app": app,
        "engine": engine,
        "session_factory": session_factory,
        "db_path": db_path
    }

    # Teardown
    await engine.dispose()
    for ext in ("", "-wal", "-shm"):
        f = db_path + ext
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_50_concurrent_redemptions_exact_one_succeeds(test_app_and_db):
    """
    CRITICAL SPECIFICATION 8:
    Fire 50 concurrent access attempts using pytest and asyncio.gather against POST /api/tokens/redeem.
    Verifies that EXACTLY 1 succeeds (200 OK) and EXACTLY 49 receive 409 Conflict.
    Guarantees absolute zero double-spending of single-use passcodes.
    """
    app = test_app_and_db["app"]
    session_factory = test_app_and_db["session_factory"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "passcode": "842-916",
            "doctor_name": "Dr. Elena Vance, MD",
            "department": "Cardiology Dept"
        }

        # Coroutine to fire redemption request
        async def make_redemption_request(task_id: int):
            resp = await client.post("/api/tokens/redeem", json=payload)
            return task_id, resp.status_code, resp.json()

        # Fire 50 concurrent tasks simultaneously
        tasks = [make_redemption_request(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

    # Classify outcomes
    success_results = [r for r in results if r[1] == 200]
    conflict_results = [r for r in results if r[1] == 409]
    other_results = [r for r in results if r[1] not in (200, 409)]

    print(f"\n[CONCURRENCY RESULTS] Total Requests: 50 | 200 OK: {len(success_results)} | 409 Conflict: {len(conflict_results)}")

    # Verification assertions
    assert len(other_results) == 0, f"Unexpected status codes returned: {other_results}"
    assert len(success_results) == 1, f"Expected exactly 1 success, but got {len(success_results)}"
    assert len(conflict_results) == 49, f"Expected exactly 49 conflicts, but got {len(conflict_results)}"

    # Check winner response structure
    winner = success_results[0][2]
    assert winner["patient_id"] == "GK-8821"
    assert "session_id" in winner
    assert "medications" in winner["scopes_granted"]
    assert winner["cipher_specs"] == "TLS 1.3 • AES-GCM 256"

    # Check conflict message on losers
    loser = conflict_results[0][2]
    assert "detail" in loser
    assert "redeemed" in loser["detail"].lower() or "conflict" in loser["detail"].lower()

    # Verify final database state: is_redeemed MUST be True, version MUST be 1
    async with session_factory() as session:
        stmt = select(TokenRecord).where(TokenRecord.raw_code == "842916")
        res = await session.execute(stmt)
        record = res.scalar_one()

        assert record.is_redeemed is True
        assert record.version == 1
        assert record.redeemed_at is not None
        assert "Elena Vance" in record.redeemed_by


@pytest.mark.asyncio
async def test_subsequent_replay_attempt_after_redemption(test_app_and_db):
    """
    Verify that after a token has been redeemed, any subsequent single request
    is immediately rejected with 409 Conflict.
    """
    app = test_app_and_db["app"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {"passcode": "842-916"}

        # First request succeeds
        resp1 = await client.post("/api/tokens/redeem", json=payload)
        assert resp1.status_code == 200

        # Second sequential request fails with 409 Conflict
        resp2 = await client.post("/api/tokens/redeem", json=payload)
        assert resp2.status_code == 409
        assert "already been redeemed" in resp2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_expired_passcode_rejection(test_app_and_db):
    """
    Verify that an expired passcode is rejected with 409 Conflict.
    """
    app = test_app_and_db["app"]
    session_factory = test_app_and_db["session_factory"]

    # Seed an already expired token
    async with session_factory() as session:
        expired_token = TokenRecord(
            patient_id="GK-8821",
            token_id="tok-geo-expired",
            passcode="111-222",
            raw_code="111222",
            scopes='{"medications": true}',
            is_redeemed=False,
            expires_at=utc_now() - __import__("datetime").timedelta(seconds=10),
            version=0,
            created_at=utc_now()
        )
        session.add(expired_token)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/tokens/redeem", json={"passcode": "111-222"})
        assert resp.status_code == 409
        assert "expired" in resp.json()["detail"].lower()
