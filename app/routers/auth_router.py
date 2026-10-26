import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
import jwt

from app.db.session import get_db, immediate_transaction
from app.models.db_models import User, Patient, RefreshTokenRecord, AuditTrailRecord, utc_now
from app.models.auth_schemas import (
    UserRegisterRequest,
    UserLoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    UserResponse
)
from app.security.hasher import argon2_hasher
from app.security.jwt_handler import (
    create_access_token,
    create_refresh_token,
    decode_token,
    REFRESH_TOKEN_EXPIRE_DAYS
)
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: UserRegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Register a new user (Doctor or Patient) with Argon2id RFC 9106 password hashing.
    Enforces multi-tenant isolation and generates initial patient profile if role='patient'.
    """
    # 1. Enforce password complexity
    valid, err_msg = argon2_hasher.validate_password_complexity(payload.password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg
        )

    # 2. Check for duplicate email in tenant
    stmt = select(User).where(
        User.email == payload.email.lower().strip(),
        User.tenant_id == payload.tenant_id
    )
    result = await db.execute(stmt)
    existing_user = result.scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{payload.email}' already exists in tenant '{payload.tenant_id}'."
        )

    # 3. Hash password via Argon2id RFC 9106
    password_hash = argon2_hasher.hash_password(payload.password)

    # 4. Create User Record
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email=payload.email.lower().strip(),
        password_hash=password_hash,
        role=payload.role,
        full_name=payload.full_name,
        license_number=payload.license_number if payload.role == "doctor" else None,
        tenant_id=payload.tenant_id,
        is_active=True
    )
    db.add(user)

    patient_id = None
    # 5. If registering as patient, create initial patient baseline
    if payload.role == "patient":
        patient_id = f"GK-{uuid.uuid4().hex[:4].upper()}"
        mrn = f"MRN-{uuid.uuid4().hex[:6].upper()}"
        patient = Patient(
            id=patient_id,
            user_id=user_id,
            mrn=mrn,
            dob=payload.dob or "1980-01-01",
            gender=payload.gender or "Unknown",
            blood_pressure="120/80",
            egfr=90.0,
            potassium=4.2,
            inr=1.0,
            revision=1,
            status="active",
            tenant_id=payload.tenant_id
        )
        db.add(patient)

    # 6. Generate Initial Token Pair with Refresh Token Rotation Tracker
    access_token = create_access_token(
        user_id=user_id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )
    refresh_token, jti, family = create_refresh_token(
        user_id=user_id,
        tenant_id=user.tenant_id
    )

    refresh_record = RefreshTokenRecord(
        user_id=user_id,
        jti=jti,
        token_family=family,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    db.add(refresh_record)

    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=1800,
        user_id=user_id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Authenticate user via Argon2id RFC 9106 verifier.
    Issues JWT access token and fresh refresh token in a new rotation family.
    """
    clean_email = payload.email.lower().strip()
    stmt = (
        select(User)
        .where(User.email == clean_email, User.tenant_id == payload.tenant_id)
        .options(selectinload(User.patient_profile))
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Constant-time verify failure mitigation
    if not user or not argon2_hasher.verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password."
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled."
        )

    patient_id = user.patient_profile.id if user.patient_profile else None

    # Generate fresh tokens
    access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )
    refresh_token, jti, family = create_refresh_token(
        user_id=user.id,
        tenant_id=user.tenant_id
    )

    refresh_record = RefreshTokenRecord(
        user_id=user.id,
        jti=jti,
        token_family=family,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    db.add(refresh_record)
    await db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=1800,
        user_id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    """
    Refresh Token Rotation (RFC 6749 Section 6):
    - Validates refresh token signature and claims.
    - If presented token was ALREADY revoked -> REPLAY ATTACK DETECTED:
      immediately invalidates all tokens in the token family and rejects request.
    - Otherwise marks old token revoked and issues new access + refresh pair.
    """
    try:
        decoded = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired. Please re-authenticate."
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token."
        )

    if decoded.get("token_type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type (expected refresh token)."
        )

    jti = decoded.get("jti")
    family = decoded.get("token_family")
    user_id = decoded.get("sub")
    tenant_id = decoded.get("tenant_id")

    # Look up tracker record
    stmt = select(RefreshTokenRecord).where(RefreshTokenRecord.jti == jti)
    res = await db.execute(stmt)
    token_record = res.scalar_one_or_none()

    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unrecognized refresh token."
        )

    # REPLAY ATTACK DETECTION
    if token_record.is_revoked:
        # Revoke all tokens in this family immediately!
        revoke_family_stmt = (
            update(RefreshTokenRecord)
            .where(RefreshTokenRecord.token_family == family)
            .values(is_revoked=True)
        )
        await db.execute(revoke_family_stmt)
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security violation: Refresh token reuse detected. All sessions in this token family have been terminated."
        )

    # Invalidate current refresh token
    token_record.is_revoked = True

    # Retrieve user
    user_stmt = select(User).where(User.id == user_id).options(selectinload(User.patient_profile))
    user_res = await db.execute(user_stmt)
    user = user_res.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not active.")

    patient_id = user.patient_profile.id if user.patient_profile else None

    # Issue rotated token pair
    new_access_token = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )
    new_refresh_token, new_jti, _ = create_refresh_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        token_family=family  # Keep in same family
    )

    new_refresh_record = RefreshTokenRecord(
        user_id=user.id,
        jti=new_jti,
        token_family=family,
        is_revoked=False,
        expires_at=utc_now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    db.add(new_refresh_record)
    await db.commit()

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
        expires_in=1800,
        user_id=user.id,
        email=user.email,
        role=user.role,
        tenant_id=user.tenant_id,
        patient_id=patient_id
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get profile information for the currently authenticated user.
    """
    patient_id = current_user.patient_profile.id if current_user.patient_profile else None
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
        full_name=current_user.full_name,
        license_number=current_user.license_number,
        tenant_id=current_user.tenant_id,
        patient_id=patient_id,
        created_at=current_user.created_at
    )
