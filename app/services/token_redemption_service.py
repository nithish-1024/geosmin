import json
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, Tuple
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.db_models import TokenRecord, Patient, AuditTrailRecord


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TokenRedemptionService:
    """
    Enterprise Ephemeral Passcode Management Service.
    Enforces atomic Compare-And-Swap (CAS) with SQLite BEGIN IMMEDIATE semantics
    to guarantee zero double-spending of single-use passcodes under extreme concurrency.
    """

    @staticmethod
    def format_code(raw_code: str) -> str:
        """Format 6 digits into XXX-XXX."""
        clean = raw_code.replace("-", "").replace(" ", "").strip()
        return f"{clean[:3]}-{clean[3:]}" if len(clean) == 6 else clean

    @staticmethod
    def clean_code(input_code: str) -> str:
        """Strip hyphens and whitespace."""
        return input_code.replace("-", "").replace(" ", "").strip()

    async def create_share_token(
        self,
        db: AsyncSession,
        patient_id: str,
        scopes: Dict[str, bool],
        ttl_seconds: int = 300,
        custom_code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate and persist a single-use ephemeral passcode in the database.
        """
        # Verify patient exists
        stmt = select(Patient).where(Patient.id == patient_id)
        result = await db.execute(stmt)
        patient = result.scalar_one_or_none()
        if not patient:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patient with ID '{patient_id}' not found."
            )

        if custom_code:
            raw_digits = self.clean_code(custom_code)
        else:
            raw_digits = f"{secrets.randbelow(1000000):06d}"

        formatted = self.format_code(raw_digits)
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        token_id = f"tok-geo-{raw_digits}"
        session_id = f"SES-{secrets.randbelow(100000):05d}"

        token_record = TokenRecord(
            patient_id=patient_id,
            token_id=token_id,
            passcode=formatted,
            raw_code=raw_digits,
            scopes=json.dumps(scopes),
            is_redeemed=False,
            expires_at=expires_at,
            version=0,
            created_at=now
        )
        db.add(token_record)

        # HIPAA Audit Trail
        audit = AuditTrailRecord(
            patient_id=patient_id,
            actor="Patient / Clinical Portal",
            action="Ephemeral Passcode Generated",
            details=json.dumps({
                "token_id": token_id,
                "passcode": formatted,
                "expires_at": expires_at.isoformat(),
                "scopes": scopes
            }),
            tenant_id=patient.tenant_id
        )
        db.add(audit)
        await db.commit()
        await db.refresh(token_record)

        return {
            "token_id": token_id,
            "passcode": formatted,
            "raw_code": raw_digits,
            "qr_payload": f"geosmin://consult/{raw_digits}?session={session_id}",
            "expires_at_iso": expires_at.isoformat(),
            "expires_in_seconds": ttl_seconds,
            "scopes": scopes,
            "cipher_specs": "TLS 1.3 • AES-GCM 256"
        }

    async def redeem_token_atomic(
        self,
        db: AsyncSession,
        input_code: str,
        doctor_name: str = "Dr. Elena Vance, MD",
        department: str = "Cardiology Dept"
    ) -> Dict[str, Any]:
        """
        ATOMIC REDEMPTION WITH ZERO DOUBLE-SPENDING GUARANTEE:
        
        1. Explicitly initiates a BEGIN IMMEDIATE write lock on SQLite.
        2. Executes a single atomic CAS conditional update:
             UPDATE tokens SET is_redeemed = 1, redeemed_at = :now, ...
             WHERE raw_code = :clean_code AND is_redeemed = 0 AND expires_at > :now
        3. Evaluates rowcount:
             - Exactly 1: The calling coroutine won the race condition.
             - 0: Another concurrent coroutine already claimed the token, or token expired/invalid.
               Raises HTTP 409 Conflict immediately.
        """
        clean_code = self.clean_code(input_code)
        now = utc_now()
        session_id = f"SES-{secrets.randbelow(100000):05d}"

        # 1. Acquire SQLite write lock immediately (prevents upgrade deadlocks)
        conn = await db.connection()
        await conn.exec_driver_sql("BEGIN IMMEDIATE")

        # 2. Execute atomic Compare-And-Swap (CAS) update
        cas_stmt = (
            update(TokenRecord)
            .where(
                TokenRecord.raw_code == clean_code,
                TokenRecord.is_redeemed.is_(False),
                TokenRecord.expires_at > now
            )
            .values(
                is_redeemed=True,
                redeemed_at=now,
                redeemed_by=f"{doctor_name} ({department})",
                session_id=session_id,
                version=TokenRecord.version + 1
            )
        )
        result = await db.execute(cas_stmt)

        # 3. Handle Winner vs Conflict
        if result.rowcount == 1:
            # Winner: Commit atomic state change
            await db.commit()

            # Retrieve redeemed token details
            token_stmt = select(TokenRecord).where(TokenRecord.raw_code == clean_code)
            token_res = await db.execute(token_stmt)
            record = token_res.scalar_one()

            scopes_dict = json.loads(record.scopes)
            granted_scopes = [k for k, v in scopes_dict.items() if v]

            return {
                "session_id": session_id,
                "patient_id": record.patient_id,
                "doctor_name": doctor_name,
                "scopes_granted": granted_scopes,
                "redeemed_at": now.isoformat(),
                "expires_in_seconds": 300,
                "cipher_specs": "TLS 1.3 • AES-GCM 256"
            }

        # 4. Loser / Invalid path: Roll back immediate transaction
        await db.rollback()

        # Diagnose exact reason for informative clinical error response
        check_stmt = select(TokenRecord).where(TokenRecord.raw_code == clean_code)
        check_res = await db.execute(check_stmt)
        existing = check_res.scalar_one_or_none()

        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Passcode '{input_code}' not found or unrecognized."
            )

        if existing.is_redeemed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Passcode has already been redeemed. Replay/concurrent consumption rejected."
            )

        exp = existing.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Passcode has expired. Please request a new ephemeral share code."
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent redemption conflict detected (Race condition prevented)."
        )


token_service = TokenRedemptionService()
