from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.models.db_models import TokenRecord, utc_now
from app.models.auth_schemas import (
    PasscodeCreateRequest,
    PasscodeCreateResponse,
    PasscodeRedeemRequest,
    PasscodeRedeemResponse
)
from app.services.token_redemption_service import token_service

router = APIRouter(prefix="/api/tokens", tags=["Ephemeral Passcodes"])


@router.post("/generate", response_model=PasscodeCreateResponse, status_code=status.HTTP_201_CREATED)
async def generate_passcode(payload: PasscodeCreateRequest, db: AsyncSession = Depends(get_db)):
    """
    Generate a new single-use 6-digit access passcode for doctor ledger access.
    """
    record = await token_service.create_share_token(
        db=db,
        patient_id=payload.patient_id,
        scopes=payload.scopes,
        ttl_seconds=payload.ttl_seconds
    )
    return PasscodeCreateResponse(**record)


@router.post("/redeem", response_model=PasscodeRedeemResponse)
async def redeem_passcode(payload: PasscodeRedeemRequest, db: AsyncSession = Depends(get_db)):
    """
    ATOMIC SINGLE-USE PASSCODE REDEMPTION ROUTE:
    
    Uses SQLite BEGIN IMMEDIATE transaction semantics and atomic CAS conditional update.
    Under high concurrency, exactly 1 request succeeds (HTTP 200) and all conflicting
    double-spend attempts are rejected with HTTP 409 Conflict.
    """
    session_data = await token_service.redeem_token_atomic(
        db=db,
        input_code=payload.passcode,
        doctor_name=payload.doctor_name,
        department=payload.department or "Cardiology Dept"
    )
    return PasscodeRedeemResponse(**session_data)


@router.get("/{code}/status")
async def get_passcode_status(code: str, db: AsyncSession = Depends(get_db)):
    """
    Query current lifecycle status of an access passcode.
    """
    clean = token_service.clean_code(code)
    stmt = select(TokenRecord).where(TokenRecord.raw_code == clean)
    res = await db.execute(stmt)
    record = res.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Passcode '{code}' not found."
        )

    now = utc_now()
    if record.is_redeemed:
        status_str = "consumed"
    elif record.expires_at <= now:
        status_str = "expired"
    else:
        status_str = "pending"

    return {
        "passcode": record.passcode,
        "status": status_str,
        "is_redeemed": record.is_redeemed,
        "expires_at": record.expires_at.isoformat(),
        "redeemed_at": record.redeemed_at.isoformat() if record.redeemed_at else None,
        "redeemed_by": record.redeemed_by
    }
