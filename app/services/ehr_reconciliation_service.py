import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.db_models import (
    Patient,
    Medication,
    Allergy,
    Diagnosis,
    AuditTrailRecord,
    utc_now
)
from app.db.session import immediate_transaction


class EHRReconciliationService:
    """
    Master Patient Index (MPI) and Optimistic Concurrency Control (OCC) Service.
    Implements real-world healthcare reconciliation standards (Golden Record pattern,
    Tombstoning, Non-destructive Aliasing, and monotonic revision validation).
    """

    async def update_patient_occ(
        self,
        db: AsyncSession,
        patient_id: str,
        expected_revision: int,
        updates: Dict[str, Any],
        actor: str = "Clinical Attending"
    ) -> Patient:
        """
        Optimistic Concurrency Control (OCC) update.
        Rejects stale writes with HTTP 409 Conflict if another clinician
        or automated telemetry process bumped the revision counter.
        """
        now = utc_now()
        values_to_update = {
            "revision": Patient.revision + 1,
            "updated_at": now
        }

        for field in ("blood_pressure", "egfr", "potassium", "inr"):
            if field in updates and updates[field] is not None:
                values_to_update[field] = updates[field]

        # Single atomic CAS update verifying revision
        stmt = (
            update(Patient)
            .where(Patient.id == patient_id, Patient.revision == expected_revision)
            .values(**values_to_update)
        )
        result = await db.execute(stmt)

        if result.rowcount == 0:
            # Check if patient exists
            check_stmt = select(Patient).where(Patient.id == patient_id)
            check_res = await db.execute(check_stmt)
            patient = check_res.scalar_one_or_none()
            if not patient:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Patient '{patient_id}' not found."
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Optimistic Concurrency Conflict: Patient '{patient_id}' was concurrently "
                    f"modified by another session. Current revision is {patient.revision}, but "
                    f"payload expected {expected_revision}. Please reload and reconcile."
                )
            )

        # Audit log the OCC update
        audit = AuditTrailRecord(
            patient_id=patient_id,
            actor=actor,
            action="Patient Telemetry Updated (OCC)",
            details=json.dumps({"updates": updates, "from_revision": expected_revision, "to_revision": expected_revision + 1}),
            tenant_id="tenant-default"
        )
        db.add(audit)
        await db.commit()

        # Fetch updated record
        updated_patient = await self.get_patient_resolved(db, patient_id)
        return updated_patient

    async def merge_patient_records(
        self,
        db: AsyncSession,
        survivor_id: str,
        source_id: str,
        reason: str,
        actor: str = "System Admin"
    ) -> Dict[str, Any]:
        """
        Reconcile and merge two fragmented EHR patient records:
        - survivor_id: The "Golden Record" that remains active.
        - source_id: The duplicate record that is tombstoned and redirected.
        - Reconciles medications, allergies, and diagnoses non-destructively.
        """
        if survivor_id == source_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Survivor patient and source patient cannot be identical."
            )

        async with immediate_transaction(db):
            # Load survivor
            surv_stmt = (
                select(Patient)
                .where(Patient.id == survivor_id)
                .options(
                    selectinload(Patient.medications),
                    selectinload(Patient.allergies),
                    selectinload(Patient.diagnoses)
                )
            )
            surv_res = await db.execute(surv_stmt)
            survivor = surv_res.scalar_one_or_none()

            # Load source
            src_stmt = (
                select(Patient)
                .where(Patient.id == source_id)
                .options(
                    selectinload(Patient.medications),
                    selectinload(Patient.allergies),
                    selectinload(Patient.diagnoses)
                )
            )
            src_res = await db.execute(src_stmt)
            source = src_res.scalar_one_or_none()

            if not survivor or not source:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"One or both patient records not found (Survivor: {survivor_id}, Source: {source_id})."
                )

            if source.status == "merged":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Source patient '{source_id}' is already tombstoned and merged into '{source.merged_into_id}'."
                )

            # 1. Reconcile Medications
            surv_rxcuis = {m.generic_rxcui for m in survivor.medications}
            meds_reconciled = 0
            for med in source.medications:
                if med.generic_rxcui not in surv_rxcuis:
                    # Move medication over to golden record
                    med.patient_id = survivor.id
                    meds_reconciled += 1
                else:
                    # Duplicate RxCUI: Archive duplicate with provenance note
                    med.status = "archived"
                    med.patient_id = survivor.id
                    med.indication = f"[Merged Duplicate from {source.mrn}] {med.indication}"
                    meds_reconciled += 1

            # 2. Reconcile Allergies
            surv_allergens = {a.allergen.strip().lower() for a in survivor.allergies}
            allergies_reconciled = 0
            for alg in source.allergies:
                if alg.allergen.strip().lower() not in surv_allergens:
                    alg.patient_id = survivor.id
                    allergies_reconciled += 1
                else:
                    # Duplicate allergy: point to survivor or leave intact
                    alg.patient_id = survivor.id
                    allergies_reconciled += 1

            # 3. Reconcile Diagnoses
            surv_icd10 = {d.icd10.strip().upper() for d in survivor.diagnoses}
            dx_reconciled = 0
            for dx in source.diagnoses:
                if dx.icd10.strip().upper() not in surv_icd10:
                    dx.patient_id = survivor.id
                    dx_reconciled += 1
                else:
                    dx.patient_id = survivor.id
                    dx_reconciled += 1

            # 4. Tombstone Source Patient
            source.status = "merged"
            source.merged_into_id = survivor.id
            source.updated_at = utc_now()

            # 5. Bump Survivor OCC Revision
            survivor.revision += 1
            survivor.updated_at = utc_now()

            # 6. Immutable Regulatory Audit Trail
            audit = AuditTrailRecord(
                patient_id=survivor.id,
                actor=actor,
                action="Patient Records Reconciled (MPI Merge)",
                details=json.dumps({
                    "survivor_id": survivor.id,
                    "source_id": source.id,
                    "source_mrn": source.mrn,
                    "survivor_mrn": survivor.mrn,
                    "reason": reason,
                    "medications_reconciled": meds_reconciled,
                    "allergies_reconciled": allergies_reconciled,
                    "diagnoses_reconciled": dx_reconciled
                }),
                tenant_id=survivor.tenant_id
            )
            db.add(audit)

        return {
            "survivor_id": survivor.id,
            "merged_id": source.id,
            "status": "completed",
            "medications_reconciled": meds_reconciled,
            "allergies_reconciled": allergies_reconciled,
            "diagnoses_reconciled": dx_reconciled,
            "audit_id": audit.id,
            "message": f"Successfully merged {source.id} into Golden Record {survivor.id}. Aliasing active."
        }

    async def get_patient_resolved(self, db: AsyncSession, patient_id: str) -> Patient:
        """
        Retrieve patient record, automatically following tombstone redirect
        pointers to the Golden Record if the patient has been merged.
        """
        stmt = (
            select(Patient)
            .where(Patient.id == patient_id)
            .options(
                selectinload(Patient.medications),
                selectinload(Patient.allergies),
                selectinload(Patient.diagnoses)
            )
        )
        res = await db.execute(stmt)
        patient = res.scalar_one_or_none()

        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient '{patient_id}' not found."
            )

        # Non-destructive aliasing resolution: follow pointer to survivor
        if patient.status == "merged" and patient.merged_into_id:
            return await self.get_patient_resolved(db, patient.merged_into_id)

        return patient


ehr_service = EHRReconciliationService()
