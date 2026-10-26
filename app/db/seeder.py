import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine, init_db, AsyncSessionLocal
from app.models.db_models import (
    User,
    Patient,
    Medication,
    Allergy,
    Diagnosis,
    TokenRecord,
    utc_now
)
from app.security.hasher import argon2_hasher

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
CLINICAL_JSON = os.path.join(DATA_DIR, "clinical_db.json")


async def seed_database(db: AsyncSession):
    """
    Seed initial clinical baseline, doctor account, patient account,
    and single-use passcode (842-916) from clinical_db.json.
    """
    # 1. Check if already seeded
    res = await db.execute(select(Patient).where(Patient.id == "GK-8821"))
    if res.scalar_one_or_none():
        return

    # 2. Seed Doctor User
    doctor_hash = argon2_hasher.hash_password("DoctorElena123!")
    doctor = User(
        id=str(uuid.uuid4()),
        email="dr.vance@geosmin.health",
        password_hash=doctor_hash,
        role="doctor",
        full_name="Dr. Elena Vance, MD",
        license_number="MD-94021-CAL",
        tenant_id="tenant-default",
        is_active=True
    )
    db.add(doctor)

    # 3. Seed Patient User
    patient_user_hash = argon2_hasher.hash_password("PatientRavi123!")
    patient_user = User(
        id=str(uuid.uuid4()),
        email="ravi.kumar@geosmin.health",
        password_hash=patient_user_hash,
        role="patient",
        full_name="Ravi Kumar",
        tenant_id="tenant-default",
        is_active=True
    )
    db.add(patient_user)

    # 4. Load from clinical_db.json if available
    patient_data = {}
    if os.path.exists(CLINICAL_JSON):
        with open(CLINICAL_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
            patient_data = raw.get("patients", {}).get("GK-8821", raw.get("patient", {}))

    biometrics = patient_data.get("biometrics", {})

    patient = Patient(
        id="GK-8821",
        user_id=patient_user.id,
        mrn=patient_data.get("mrn", "RK-48810"),
        dob=patient_data.get("dob", "1968-05-12"),
        gender=patient_data.get("gender", "Male"),
        blood_pressure=str(biometrics.get("blood_pressure", "132/84")),
        egfr=float(biometrics.get("egfr", 78.0)),
        potassium=float(biometrics.get("serum_potassium", 4.2)),
        inr=float(biometrics.get("last_inr", 2.3)),
        revision=1,
        status="active",
        tenant_id="tenant-default"
    )
    db.add(patient)

    # Seed Medications
    for med in patient_data.get("medications", []):
        m = Medication(
            id=med.get("id", f"med-{uuid.uuid4().hex[:6]}"),
            patient_id=patient.id,
            rx_number=med.get("rx_number", "RX-999999"),
            generic_rxcui=med.get("generic_rxcui", "29046"),
            drug_name=med.get("name", med.get("drug_name", "Unknown Drug")),
            dosage=med.get("dosage", "10mg"),
            cadence=med.get("cadence", "Daily"),
            route=med.get("route", "Oral"),
            status=med.get("status", "active"),
            prescriber=med.get("prescriber", "Dr. Elena Vance, MD"),
            indication=med.get("indication", "Clinical Condition"),
            start_date=med.get("start_date", "2024-01-01"),
            end_date=med.get("end_date", "Ongoing"),
            refills=med.get("refills", 0)
        )
        db.add(m)

    # Seed Allergies
    for alg in patient_data.get("allergies", []):
        a = Allergy(
            id=f"alg-{uuid.uuid4().hex[:6]}",
            patient_id=patient.id,
            allergen=alg.get("allergen", "Unknown"),
            severity=alg.get("severity", "Moderate"),
            reaction=alg.get("reaction", "Adverse reaction"),
            year=str(alg.get("year", "2020"))
        )
        db.add(a)

    # Seed Diagnoses
    for dx in patient_data.get("diagnoses", []):
        d = Diagnosis(
            id=f"dx-{uuid.uuid4().hex[:6]}",
            patient_id=patient.id,
            condition=dx.get("condition", "Hypertension"),
            icd10=dx.get("icd10", "I10"),
            status=dx.get("status", "Active")
        )
        db.add(d)

    # 5. Pre-seed demo passcode "842-916" (842916)
    demo_code = "842916"
    now = utc_now()
    demo_token = TokenRecord(
        patient_id=patient.id,
        token_id="tok-geo-842916",
        passcode="842-916",
        raw_code=demo_code,
        scopes=json.dumps({"medications": True, "allergies": True, "diagnoses": True, "biometrics": True}),
        is_redeemed=False,
        expires_at=now + timedelta(seconds=3600),  # 1 hour for demo
        version=0,
        created_at=now
    )
    db.add(demo_token)

    await db.commit()


async def run_seeder():
    await init_db()
    async with AsyncSessionLocal() as session:
        await seed_database(session)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_seeder())
