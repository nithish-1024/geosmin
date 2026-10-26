from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.db_models import Patient, Medication, Allergy, Diagnosis
from app.models.auth_schemas import (
    PatientUpdateOCCRequest,
    PatientMergeRequest,
    PatientMergeResponse
)
from app.services.ehr_reconciliation_service import ehr_service
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/patients", tags=["Patients & EHR"])


@router.get("")
async def list_patients(db: AsyncSession = Depends(get_db)):
    """
    List all active patients in the system.
    """
    stmt = (
        select(Patient)
        .where(Patient.status == "active")
        .options(selectinload(Patient.diagnoses))
    )
    res = await db.execute(stmt)
    patients = res.scalars().all()

    return [
        {
            "id": p.id,
            "mrn": p.mrn,
            "dob": p.dob,
            "gender": p.gender,
            "blood_pressure": p.blood_pressure,
            "egfr": p.egfr,
            "potassium": p.potassium,
            "inr": p.inr,
            "revision": p.revision,
            "status": p.status,
            "diagnoses_count": len(p.diagnoses)
        }
        for p in patients
    ]


@router.get("/{patient_id}")
async def get_patient_detail(patient_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retrieve clinical patient profile.
    Automatically resolves tombstoned records to the Golden Record if merged.
    """
    patient = await ehr_service.get_patient_resolved(db, patient_id)

    return {
        "id": patient.id,
        "mrn": patient.mrn,
        "dob": patient.dob,
        "gender": patient.gender,
        "blood_pressure": patient.blood_pressure,
        "egfr": patient.egfr,
        "potassium": patient.potassium,
        "inr": patient.inr,
        "revision": patient.revision,
        "status": patient.status,
        "merged_into_id": patient.merged_into_id,
        "medications": [
            {
                "id": m.id,
                "rx_number": m.rx_number,
                "generic_rxcui": m.generic_rxcui,
                "drug_name": m.drug_name,
                "dosage": m.dosage,
                "cadence": m.cadence,
                "route": m.route,
                "status": m.status,
                "prescriber": m.prescriber,
                "indication": m.indication,
                "start_date": m.start_date,
                "end_date": m.end_date,
                "refills": m.refills
            }
            for m in patient.medications
        ],
        "allergies": [
            {
                "id": a.id,
                "allergen": a.allergen,
                "severity": a.severity,
                "reaction": a.reaction,
                "year": a.year
            }
            for a in patient.allergies
        ],
        "diagnoses": [
            {
                "id": d.id,
                "condition": d.condition,
                "icd10": d.icd10,
                "status": d.status
            }
            for d in patient.diagnoses
        ]
    }


@router.put("/{patient_id}")
async def update_patient_telemetry(
    patient_id: str,
    payload: PatientUpdateOCCRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Update patient telemetry with Optimistic Concurrency Control (OCC).
    Rejects concurrent conflicting writes with HTTP 409 Conflict.
    """
    updated = await ehr_service.update_patient_occ(
        db=db,
        patient_id=patient_id,
        expected_revision=payload.expected_revision,
        updates=payload.model_dump(exclude_unset=True),
        actor="Attending Physician"
    )

    return {
        "id": updated.id,
        "blood_pressure": updated.blood_pressure,
        "egfr": updated.egfr,
        "potassium": updated.potassium,
        "inr": updated.inr,
        "revision": updated.revision,
        "message": "Telemetry updated successfully under OCC validation."
    }


@router.post("/merge", response_model=PatientMergeResponse)
async def merge_patients(
    payload: PatientMergeRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Execute Master Patient Index (MPI) reconciliation between two fragmented patient records:
    - Retains survivor record as Golden Record.
    - Tombstones source record with redirect pointer.
    - Deduplicates medications, allergies, and diagnoses.
    """
    result = await ehr_service.merge_patient_records(
        db=db,
        survivor_id=payload.survivor_patient_id,
        source_id=payload.source_patient_id,
        reason=payload.reason,
        actor=payload.actor
    )
    return PatientMergeResponse(**result)
