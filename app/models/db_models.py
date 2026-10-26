import uuid
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy import (
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """
    User entity supporting multi-tenant clinical roles (patient, doctor, admin).
    Argon2id password hashes adhere strictly to RFC 9106 standards.
    """
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # 'patient' | 'doctor'
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    license_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, default="tenant-default", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("email", "tenant_id", name="uq_user_email_tenant"),
    )

    # Relationships
    patient_profile: Mapped[Optional["Patient"]] = relationship("Patient", back_populates="user", uselist=False)
    refresh_tokens: Mapped[List["RefreshTokenRecord"]] = relationship("RefreshTokenRecord", back_populates="user", cascade="all, delete-orphan")


class Patient(Base):
    """
    Patient Clinical Baseline entity.
    Includes Optimistic Concurrency Control (revision) and Master Patient Index
    merge reconciliation tracking (merged_into_id).
    """
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: f"GK-{uuid.uuid4().hex[:4].upper()}")
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    mrn: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    dob: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    gender: Mapped[str] = mapped_column(String(20), nullable=False)
    blood_pressure: Mapped[str] = mapped_column(String(20), default="120/80", nullable=False)
    egfr: Mapped[float] = mapped_column(Float, default=90.0, nullable=False)
    potassium: Mapped[float] = mapped_column(Float, default=4.2, nullable=False)
    inr: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # Monotonic revision counter for OCC
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # 'active' | 'merged' | 'archived'
    merged_into_id: Mapped[Optional[str]] = mapped_column(String(50), ForeignKey("patients.id", ondelete="SET NULL"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(50), default="tenant-default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    user: Mapped[Optional["User"]] = relationship("User", back_populates="patient_profile")
    medications: Mapped[List["Medication"]] = relationship("Medication", back_populates="patient", cascade="all, delete-orphan")
    allergies: Mapped[List["Allergy"]] = relationship("Allergy", back_populates="patient", cascade="all, delete-orphan")
    diagnoses: Mapped[List["Diagnosis"]] = relationship("Diagnosis", back_populates="patient", cascade="all, delete-orphan")
    tokens: Mapped[List["TokenRecord"]] = relationship("TokenRecord", back_populates="patient", cascade="all, delete-orphan")
    merged_records: Mapped[List["Patient"]] = relationship("Patient", remote_side=[merged_into_id])


class Medication(Base):
    """
    Patient Medication record with RxNorm mapping and prescription metadata.
    """
    __tablename__ = "medications"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: f"med-{uuid.uuid4().hex[:6]}")
    patient_id: Mapped[str] = mapped_column(String(50), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    rx_number: Mapped[str] = mapped_column(String(50), nullable=False)
    generic_rxcui: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(150), nullable=False)
    dosage: Mapped[str] = mapped_column(String(50), nullable=False)
    cadence: Mapped[str] = mapped_column(String(100), nullable=False)
    route: Mapped[str] = mapped_column(String(50), default="Oral", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # 'active' | 'attention' | 'archived'
    prescriber: Mapped[str] = mapped_column(String(150), nullable=False)
    indication: Mapped[str] = mapped_column(String(150), nullable=False)
    start_date: Mapped[str] = mapped_column(String(30), nullable=False)
    end_date: Mapped[Optional[str]] = mapped_column(String(30), default="Ongoing", nullable=True)
    refills: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="medications")


class Allergy(Base):
    """
    Patient Hypersensitivity and Allergy record.
    """
    __tablename__ = "allergies"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: f"alg-{uuid.uuid4().hex[:6]}")
    patient_id: Mapped[str] = mapped_column(String(50), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    allergen: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)  # 'High' | 'Moderate' | 'Mild'
    reaction: Mapped[str] = mapped_column(String(150), nullable=False)
    year: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="allergies")


class Diagnosis(Base):
    """
    Patient Clinical Diagnosis with ICD-10 codification.
    """
    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, default=lambda: f"dx-{uuid.uuid4().hex[:6]}")
    patient_id: Mapped[str] = mapped_column(String(50), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    condition: Mapped[str] = mapped_column(String(150), nullable=False)
    icd10: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="Active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="diagnoses")


class TokenRecord(Base):
    """
    Single-Use Ephemeral Passcode record for Zero-Knowledge Ledger Sharing.
    Designed with CAS state machine semantics (version counter, atomic UPDATE)
    to guarantee zero double-spending under massive concurrency.
    """
    __tablename__ = "tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id: Mapped[str] = mapped_column(String(50), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    token_id: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    passcode: Mapped[str] = mapped_column(String(20), nullable=False)  # Formatted e.g. "842-916"
    raw_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # Clean e.g. "842916"
    scopes: Mapped[str] = mapped_column(Text, nullable=False)  # JSON encoded scopes dictionary
    is_redeemed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    redeemed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_by: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    patient: Mapped["Patient"] = relationship("Patient", back_populates="tokens")

    __table_args__ = (
        Index("ix_tokens_redemption_lookup", "raw_code", "is_redeemed", "expires_at"),
    )


class RefreshTokenRecord(Base):
    """
    Cryptographic Refresh Token Tracker for RFC 6749 / OAuth2 Token Rotation.
    Detects replay attacks and invalidates entire token family upon reuse.
    """
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_family: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")


class AuditTrailRecord(Base):
    """
    Immutable HIPAA / 21 CFR Part 11 compliant audit log record.
    """
    __tablename__ = "audit_trails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(150), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(50), default="tenant-default", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
