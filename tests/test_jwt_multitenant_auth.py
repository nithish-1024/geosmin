import os
import uuid
import tempfile
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, select

from app.db.base import Base
from app.db.session import set_sqlite_concurrency_pragmas, get_db
from app.models.db_models import User, Patient, RefreshTokenRecord
from app.routers import geosmin_v2_router
from app.security.hasher import argon2_hasher


@pytest_asyncio.fixture(scope="function")
async def auth_test_context():
    """
    Isolated SQLite WAL database for authentication and multi-tenancy tests.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, f"test_auth_{uuid.uuid4().hex[:8]}.db")
    db_url = f"sqlite+aiosqlite:///{db_path}"

    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 10.0},
        pool_pre_ping=True
    )
    event.listen(engine.sync_engine, "connect", set_sqlite_concurrency_pragmas)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False
    )

    app = FastAPI(title="Auth Test App")
    app.include_router(geosmin_v2_router)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    yield {
        "app": app,
        "session_factory": session_factory,
        "engine": engine,
        "db_path": db_path
    }

    await engine.dispose()
    for ext in ("", "-wal", "-shm"):
        f = db_path + ext
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass


def test_argon2id_rfc9106_parameters():
    """
    Verify Argon2id RFC 9106 standard compliance:
    - Modular crypt format contains m=65536, t=3, p=4
    - Secure verification and rehash checks
    """
    password = "SecureClinical123!"
    h = argon2_hasher.hash_password(password)

    # Format assertion
    assert h.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert argon2_hasher.verify_password(password, h) is True
    assert argon2_hasher.verify_password("WrongPassword123!", h) is False
    assert argon2_hasher.needs_rehash(h) is False


def test_password_complexity_validator():
    """
    Verify enterprise password complexity policy enforcement.
    """
    assert argon2_hasher.validate_password_complexity("short")[0] is False
    assert argon2_hasher.validate_password_complexity("nouppercase123!")[0] is False
    assert argon2_hasher.validate_password_complexity("NOLOWERCASE123!")[0] is False
    assert argon2_hasher.validate_password_complexity("NoDigitsHere!")[0] is False
    assert argon2_hasher.validate_password_complexity("NoSpecialChar123")[0] is False
    assert argon2_hasher.validate_password_complexity("ValidPass123!")[0] is True


@pytest.mark.asyncio
async def test_register_doctor_and_patient(auth_test_context):
    """
    Test registration of Doctor and Patient accounts with Argon2id and auto-generated MRN.
    """
    app = auth_test_context["app"]
    session_factory = auth_test_context["session_factory"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register Doctor
        doc_payload = {
            "email": "dr.vance@geosmin.health",
            "password": "ClinicalDoctor123!",
            "role": "doctor",
            "full_name": "Dr. Elena Vance, MD",
            "license_number": "MD-94021-CAL",
            "tenant_id": "hospital-mayo"
        }
        res_doc = await client.post("/api/auth/register", json=doc_payload)
        assert res_doc.status_code == 201
        doc_data = res_doc.json()
        assert "access_token" in doc_data
        assert "refresh_token" in doc_data
        assert doc_data["role"] == "doctor"
        assert doc_data["tenant_id"] == "hospital-mayo"

        # 2. Register Patient
        pat_payload = {
            "email": "ravi.kumar@geosmin.health",
            "password": "PatientRavi123!",
            "role": "patient",
            "full_name": "Ravi Kumar",
            "tenant_id": "hospital-mayo",
            "dob": "1968-05-12",
            "gender": "Male"
        }
        res_pat = await client.post("/api/auth/register", json=pat_payload)
        assert res_pat.status_code == 201
        pat_data = res_pat.json()
        assert pat_data["patient_id"] is not None
        assert pat_data["patient_id"].startswith("GK-")

    # Verify Patient in DB has initialized MRN and baseline
    async with session_factory() as session:
        stmt = select(Patient).where(Patient.id == pat_data["patient_id"])
        res = await session.execute(stmt)
        patient_row = res.scalar_one()
        assert patient_row.mrn.startswith("MRN-")
        assert patient_row.revision == 1


@pytest.mark.asyncio
async def test_multitenant_email_isolation(auth_test_context):
    """
    Verify multi-tenant isolation:
    - Same email in SAME tenant is rejected (409 Conflict).
    - Same email in DIFFERENT tenants is allowed.
    """
    app = auth_test_context["app"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        base_user = {
            "email": "clinician@sharedhealth.org",
            "password": "SecurePassword123!",
            "role": "doctor",
            "full_name": "Dr. Jordan Lee",
            "tenant_id": "tenant-alpha"
        }
        # First registration in tenant-alpha
        r1 = await client.post("/api/auth/register", json=base_user)
        assert r1.status_code == 201

        # Duplicate in tenant-alpha should fail
        r2 = await client.post("/api/auth/register", json=base_user)
        assert r2.status_code == 409

        # Same email in tenant-beta should SUCCEED
        user_beta = dict(base_user, tenant_id="tenant-beta")
        r3 = await client.post("/api/auth/register", json=user_beta)
        assert r3.status_code == 201
        assert r3.json()["tenant_id"] == "tenant-beta"


@pytest.mark.asyncio
async def test_refresh_token_rotation_and_replay_detection(auth_test_context):
    """
    Verify Refresh Token Rotation (RTR) and Replay Attack Mitigation:
    1. Login generates Token Pair (A1, R1).
    2. Refresh with R1 generates Token Pair (A2, R2). R1 is marked revoked.
    3. Replay attack: Attacker submits old R1 again!
       System detects reuse, revokes all tokens in R1's token family, and returns 401.
    """
    app = auth_test_context["app"]
    session_factory = auth_test_context["session_factory"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register user
        reg_payload = {
            "email": "doctor.rotation@geosmin.health",
            "password": "StrongPassword123!",
            "role": "doctor",
            "full_name": "Dr. Rotator",
            "tenant_id": "hospital-mayo"
        }
        reg_res = await client.post("/api/auth/register", json=reg_payload)
        tokens = reg_res.json()
        r1 = tokens["refresh_token"]

        # Legitimate rotation: Use R1 to get (A2, R2)
        rot_res = await client.post("/api/auth/refresh", json={"refresh_token": r1})
        assert rot_res.status_code == 200
        new_tokens = rot_res.json()
        r2 = new_tokens["refresh_token"]
        assert r2 != r1

        # REPLAY ATTACK: Submit already-revoked R1 again
        replay_res = await client.post("/api/auth/refresh", json={"refresh_token": r1})
        assert replay_res.status_code == 401
        assert "reuse detected" in replay_res.json()["detail"].lower()

        # Check DB: all tokens in that family should now be revoked
        async with session_factory() as session:
            stmt = select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == tokens["user_id"])
            res = await session.execute(stmt)
            all_records = res.scalars().all()
            assert len(all_records) >= 2
            assert all(r.is_revoked for r in all_records)


@pytest.mark.asyncio
async def test_get_me_profile_with_bearer_token(auth_test_context):
    """
    Test GET /api/auth/me with Bearer token authentication.
    """
    app = auth_test_context["app"]
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        reg_payload = {
            "email": "profile.check@geosmin.health",
            "password": "StrongPassword123!",
            "role": "doctor",
            "full_name": "Dr. Profile Check",
            "license_number": "MD-CHECK-11",
            "tenant_id": "hospital-mayo"
        }
        reg_res = await client.post("/api/auth/register", json=reg_payload)
        access_token = reg_res.json()["access_token"]

        # Call /me without token -> 401
        r_unauth = await client.get("/api/auth/me")
        assert r_unauth.status_code == 401

        # Call /me with Bearer token -> 200
        headers = {"Authorization": f"Bearer {access_token}"}
        r_auth = await client.get("/api/auth/me", headers=headers)
        assert r_auth.status_code == 200
        me = r_auth.json()
        assert me["email"] == "profile.check@geosmin.health"
        assert me["role"] == "doctor"
        assert me["license_number"] == "MD-CHECK-11"
        assert me["tenant_id"] == "hospital-mayo"
