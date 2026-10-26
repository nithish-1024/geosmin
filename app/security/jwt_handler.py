import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, Tuple
import jwt

# 256-bit cryptographically secure key for HS256 (RFC 7518 Section 3.2 compliant)
JWT_SECRET_KEY = os.environ.get(
    "GEOSMIN_JWT_SECRET",
    "9f8a3d7b6c5e4a1f2e8d9c0b1a2f3e4d5c6b7a8f9e0d1c2b3a4f5e6d7c8b9a0f"  # 64-char hex string = 32 bytes
)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    tenant_id: str,
    patient_id: Optional[str] = None,
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Generate short-lived JWT access token with tenant isolation claims.
    """
    now = utc_now()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "patient_id": patient_id,
        "token_type": "access",
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(
    user_id: str,
    tenant_id: str,
    token_family: Optional[str] = None,
    expires_delta: Optional[timedelta] = None
) -> Tuple[str, str, str]:
    """
    Generate long-lived refresh token with cryptographic family tracking for rotation.
    Returns: (refresh_jwt, jti, token_family)
    """
    now = utc_now()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    jti = str(uuid.uuid4())
    family = token_family or str(uuid.uuid4())

    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "token_family": family,
        "token_type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token, jti, family


def decode_token(token: str) -> Dict[str, Any]:
    """
    Decode and validate JWT signature, expiration, and format.
    Raises jwt.PyJWTError subclasses on failure.
    """
    return jwt.decode(
        token,
        JWT_SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "iat", "sub", "token_type"]}
    )
