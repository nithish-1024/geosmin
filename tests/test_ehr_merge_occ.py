import os
import uuid
import tempfile
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, select
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.session import set_sqlite_concurrency_pragmas, get_db
from app.models.db_models import Patient, Medication, Allergy, Diagnosis, AuditTrailRecord, utc_now
from app.routers import geosmin_v2_router


@pytest_asyncio.fixture(scope="function")
async def ehr_test_context():
    """
    Isolated SQLite WAL database for OCC and EHR merge tests.
    """
    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, f"test_ehr_{uuid.uuid4().hex[:8]}.db")
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

    # Build FastAPI test app
    app = FastAPI(title="EHR Test App")
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


@pytest.mark.asyncio
async def test_optimistic_concurrency_control_revision_conflict(ehr_test_context):
    """
    Verify Optimistic Concurrency Control (OCC):
    - Sequential or concurrent writes with stale revision counter are rejected with 409 Conflict.
    - Prevents silent clinical telemetry clobbering.
    """
    app = ehr_test_context["app"]
    session_factory = ehr_test_context["session_factory"]

    # Seed initial patient with revision = 1
    async with session_factory() as session:
        p = Patient(
            id="GK-8821",
            mrn="RK-48810",
            dob="1968-05-12",
            gender="Male",
            blood_pressure="120/80",
            egfr=78.0,
            potassium=4.2,
            inr=2.3,
            revision=1,
            status="active"
        )
        session.add(p)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Clinician 1 updates blood pressure with valid expected_revision=1
        update_1 = {
            "expected_revision": 1,
            "blood_pressure": "136/88"
        }
        res1 = await client.put("/api/patients/GK-8821", json=update_1)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["blood_pressure"] == "136/88"
        assert data1["revision"] == 2

        # Clinician 2 attempts concurrent update using stale expected_revision=1
        update_2_stale = {
            "expected_revision": 1,
            "blood_pressure": "140/90"
        }
        res2 = await client.put("/api/patients/GK-8821", json=update_2_stale)
        assert res2.status_code == 409
        err = res2.json()
        assert "Optimistic Concurrency Conflict" in err["detail"]
        assert "Current revision is 2" in err["detail"]

        # Clinician 2 reloads, fetches revision=2, and retries with expected_revision=2
        update_2_fresh = {
            "expected_revision": 2,
            "blood_pressure": "138/86"
        }
        res3 = await client.put("/api/patients/GK-8821", json=update_2_fresh)
        assert res3.status_code == 200
        assert res3.json()["revision"] == 3
        assert res3.json()["blood_pressure"] == "138/86"


@pytest.mark.asyncio
async def test_real_ehr_patient_merge_and_aliasing(ehr_test_context):
    """
    Verify real-world Master Patient Index (MPI) reconciliation:
    1. Golden Record (GK-8821) has Lisinopril and Penicillin allergy.
    2. Duplicate Emergency Record (GK-9999) has Lisinopril, Warfarin, and Sulfa allergy.
    3. Merge GK-9999 into GK-8821.
    4. Verify aliasing: GET /api/patients/GK-9999 automatically resolves to GK-8821.
    5. Verify duplicate RxCUI is archived, Warfarin is transferred, and allergies merged.
    """
    app = ehr_test_context["app"]
    session_factory = ehr_test_context["session_factory"]

    # Seed Golden Record (GK-8821) and Duplicate Record (GK-9999)
    async with session_factory() as session:
        # Golden Record
        survivor = Patient(
            id="GK-8821",
            mrn="RK-48810",
            dob="1968-05-12",
            gender="Male",
            revision=1,
            status="active"
        )
        session.add(survivor)

        med1 = Medication(
            id="med-lisinopril",
            patient_id="GK-8821",
            rx_number="RX-101",
            generic_rxcui="29046",  # Lisinopril RxCUI
            drug_name="Lisinopril 20mg",
            dosage="20mg",
            cadence="Daily",
            route="Oral",
            status="active",
            prescriber="Dr. Elena Vance, MD",
            indication="Hypertension",
            start_date="2024-01-01"
        )
        session.add(med1)

        alg1 = Allergy(
            id="alg-penicillin",
            patient_id="GK-8821",
            allergen="Penicillin",
            severity="High",
            reaction="Anaphylaxis"
        )
        session.add(alg1)

        # Duplicate Record
        source = Patient(
            id="GK-9999",
            mrn="RK-EMERGENCY-99",
            dob="1968-05-12",
            gender="Male",
            revision=1,
            status="active"
        )
        session.add(source)

        med2_duplicate = Medication(
            id="med-lisinopril-dup",
            patient_id="GK-9999",
            rx_number="RX-999",
            generic_rxcui="29046",  # Same Lisinopril RxCUI
            drug_name="Lisinopril 10mg",
            dosage="10mg",
            cadence="Daily",
            route="Oral",
            status="active",
            prescriber="ED Triage Physician",
            indication="Emergency BP Spike",
            start_date="2024-05-01"
        )
        session.add(med2_duplicate)

        med3_new = Medication(
            id="med-warfarin",
            patient_id="GK-9999",
            rx_number="RX-500",
            generic_rxcui="11289",  # Warfarin RxCUI
            drug_name="Warfarin 5mg",
            dosage="5mg",
            cadence="Evening",
            route="Oral",
            status="active",
            prescriber="ED Triage Physician",
            indication="Atrial Fibrillation Anticoagulation",
            start_date="2024-05-01"
        )
        session.add(med3_new)

        alg2 = Allergy(
            id="alg-sulfa",
            patient_id="GK-9999",
            allergen="Sulfa",
            severity="Moderate",
            reaction="Rash"
        )
        session.add(alg2)

        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Perform EHR Merge
        merge_payload = {
            "survivor_patient_id": "GK-8821",
            "source_patient_id": "GK-9999",
            "reason": "Duplicate MRN created during emergency clinic intake",
            "actor": "Dr. Elena Vance, MD"
        }
        res = await client.post("/api/patients/merge", json=merge_payload)
        assert res.status_code == 200
        merge_info = res.json()
        assert merge_info["status"] == "completed"
        assert merge_info["medications_reconciled"] == 2
        assert merge_info["allergies_reconciled"] == 1

        # Test Transparent Aliasing: Querying merged source GK-9999 resolves to GK-8821!
        alias_res = await client.get("/api/patients/GK-9999")
        assert alias_res.status_code == 200
        resolved_patient = alias_res.json()
        assert resolved_patient["id"] == "GK-8821"
        assert resolved_patient["mrn"] == "RK-48810"

        # Check medications on resolved patient
        med_names = [m["drug_name"] for m in resolved_patient["medications"]]
        assert any("Lisinopril" in name for name in med_names)
        assert any("Warfarin" in name for name in med_names)

        # Check allergies
        allergens = [a["allergen"] for a in resolved_patient["allergies"]]
        assert "Penicillin" in allergens
        assert "Sulfa" in allergens

    # Verify Database state
    async with session_factory() as session:
        # Source must be tombstoned
        src_row = await session.get(Patient, "GK-9999")
        assert src_row.status == "merged"
        assert src_row.merged_into_id == "GK-8821"

        # Survivor revision must be incremented
        surv_row = await session.get(Patient, "GK-8821")
        assert surv_row.revision == 2

        # Audit trail must exist
        audit_stmt = select(AuditTrailRecord).where(AuditTrailRecord.patient_id == "GK-8821")
        audit_res = await session.execute(audit_stmt)
        audits = audit_res.scalars().all()
        assert len(audits) >= 1
        assert "MPI Merge" in audits[0].action
