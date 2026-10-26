from typing import Optional, Dict, Any, List, Literal
from datetime import datetime
from pydantic import BaseModel, Field


class UserRegisterRequest(BaseModel):
    email: str = Field(..., examples=["doctor@geosmin.health"])
    password: str = Field(..., min_length=8, examples=["ClinicalPass123!"])
    role: Literal["patient", "doctor"] = Field(..., examples=["doctor"])
    full_name: str = Field(..., examples=["Dr. Elena Vance, MD"])
    license_number: Optional[str] = Field(None, examples=["MD-94021-CAL"])
    tenant_id: str = Field("tenant-default", examples=["hospital-mayo"])
    # Patient specific baseline if registering a patient
    dob: Optional[str] = Field("1972-04-12", examples=["1972-04-12"])
    gender: Optional[str] = Field("Female", examples=["Female"])


class UserLoginRequest(BaseModel):
    email: str = Field(..., examples=["doctor@geosmin.health"])
    password: str = Field(..., examples=["ClinicalPass123!"])
    tenant_id: str = Field("tenant-default", examples=["hospital-mayo"])


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    email: str
    role: str
    tenant_id: str
    patient_id: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    full_name: str
    license_number: Optional[str] = None
    tenant_id: str
    patient_id: Optional[str] = None
    created_at: datetime


class PasscodeCreateRequest(BaseModel):
    patient_id: str = Field(..., examples=["GK-8821"])
    scopes: Dict[str, bool] = Field(
        default_factory=lambda: {
            "medications": True,
            "allergies": True,
            "diagnoses": True,
            "biometrics": True,
        }
    )
    ttl_seconds: int = Field(300, ge=30, le=3600)


class PasscodeCreateResponse(BaseModel):
    token_id: str
    passcode: str
    raw_code: str
    qr_payload: str
    expires_at_iso: str
    expires_in_seconds: int
    scopes: Dict[str, bool]


class PasscodeRedeemRequest(BaseModel):
    passcode: str = Field(..., examples=["842-916"])
    doctor_name: str = Field("Dr. Elena Vance, MD", examples=["Dr. Elena Vance, MD"])
    department: Optional[str] = Field("Cardiology Dept", examples=["Cardiology Dept"])


class PasscodeRedeemResponse(BaseModel):
    session_id: str
    patient_id: str
    doctor_name: str
    scopes_granted: List[str]
    redeemed_at: str
    expires_in_seconds: int
    cipher_specs: str = "TLS 1.3 • AES-GCM 256"


class PatientUpdateOCCRequest(BaseModel):
    expected_revision: int = Field(..., description="Optimistic Concurrency Control revision counter")
    blood_pressure: Optional[str] = None
    egfr: Optional[float] = None
    potassium: Optional[float] = None
    inr: Optional[float] = None


class PatientMergeRequest(BaseModel):
    survivor_patient_id: str = Field(..., description="The Golden Record patient ID that will survive")
    source_patient_id: str = Field(..., description="The duplicate patient ID to be merged and tombstoned")
    reason: str = Field(..., examples=["Duplicate MRN created during emergency clinic intake"])
    actor: str = Field("Dr. Elena Vance, MD", examples=["Dr. Elena Vance, MD"])


class PatientMergeResponse(BaseModel):
    survivor_id: str
    merged_id: str
    status: str
    medications_reconciled: int
    allergies_reconciled: int
    diagnoses_reconciled: int
    audit_id: str
    message: str
