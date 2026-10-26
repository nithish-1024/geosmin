from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

# --- Patient Models ---
class AllergyItem(BaseModel):
    allergen: str
    severity: str
    reaction: str
    year: Optional[str] = None

class DiagnosisItem(BaseModel):
    condition: str
    icd10: str
    status: str

class BiometricTelemetry(BaseModel):
    egfr: float = 78.0
    egfr_unit: str = "mL/min/1.73m²"
    egfr_stage: str = "Stage 2 (Mild decrease)"
    blood_pressure: str = "132/84"
    bp_status: str = "7-day mean"
    serum_potassium: float = 4.2
    potassium_unit: str = "mEq/L"
    potassium_status: str = "Within Target Range"
    last_inr: float = 2.3
    inr_target: str = "2.0-3.0"
    inr_date: str = "Oct 12, 2024"

class MedicationItem(BaseModel):
    id: str
    rx_number: str
    name: str
    generic_name: str
    dosage: str
    strength: str
    route: str = "Oral"
    cadence: str
    status: str  # active, attention, archived, unsure
    prescriber: str
    start_date: str
    end_date: Optional[str] = "Ongoing"
    refills: int = 0
    indication: str
    safety_target: Optional[str] = None
    safety_status: str
    atc_class: Optional[str] = None
    category: Optional[str] = None

class PatientProfile(BaseModel):
    id: str
    mrn: str
    name: str
    age: int
    dob: str
    gender: str
    portal_status: str
    ehr_sync: str
    next_review: str
    allergies: List[AllergyItem]
    diagnoses: List[DiagnosisItem]
    biometrics: BiometricTelemetry
    medications: List[MedicationItem]

# --- Sharing & Security Models ---
class ShareScopes(BaseModel):
    medications: bool = True
    allergies: bool = True
    diagnoses: bool = True
    biometrics: bool = False

class CreateShareRequest(BaseModel):
    patient_id: str = "GK-8821"
    scopes: ShareScopes

class CreateShareResponse(BaseModel):
    share_token_id: str
    passcode: str  # e.g. "842-916"
    raw_code: str  # "842916"
    qr_payload: str
    expires_at_iso: str
    expires_in_seconds: int = 300
    cipher_specs: str = "TLS 1.3 • AES-GCM 256"

class DoctorAccessRequest(BaseModel):
    passcode: str
    doctor_name: str = "Dr. Elena Vance, MD"
    department: str = "Cardiology Dept"

class DoctorSessionData(BaseModel):
    session_id: str
    patient_name: str
    patient_age: int
    dob: str
    mrn: str
    expires_in_seconds: int
    scopes_granted: List[str]
    allergies: List[AllergyItem]
    diagnoses: List[DiagnosisItem]
    biometrics: Optional[BiometricTelemetry] = None
    active_regimen: List[MedicationItem]
    banner_warning: str = "This view cannot be reopened. Session will permanently expire once closed or submitted."
    signature_metadata: Optional[Dict[str, Any]] = None

# --- Medication Ingestion Models ---
class AddMedicationRequest(BaseModel):
    name: str
    dosage: str
    cadence: str
    prescribing_clinician: str
    therapeutic_indication: str
    formulation: Optional[str] = "Oral film-coated tablet"
    ndc: Optional[str] = "0071-0156-23"

class RxNormValidationResponse(BaseModel):
    concept_name: str
    rxnorm_cui: str
    match_confidence: float
    brand_equivalent: str
    atc_code: str
    therapeutic_class: str
    dietary_guideline: Optional[str] = None
    verification_summary: str
    status: Optional[str] = "resolved"
    generic_name: Optional[str] = None
    brand_aliases: Optional[List[str]] = None
    disambiguation_options: Optional[List[Dict[str, Any]]] = None
    lasa_warning: Optional[str] = None

# --- Prescribing & Safety Analysis Models ---
class PrescribeNewMedicationRequest(BaseModel):
    session_id: str
    patient_id: str = "GK-8821"
    medication_name: str
    dosage: str
    frequency: str
    duration: str
    route: str = "Oral (PO)"
    clinical_rationale: str

class ClinicalFinding(BaseModel):
    id: str
    severity: str  # HIGH_RISK, AMBER_REVIEW, UNABLE_TO_ASSESS
    severity_label: str  # [HIGH RISK], [AMBER - REVIEW], [UNABLE TO ASSESS]
    category: str
    class_level: str
    headline: str
    title: str
    description: str
    evidence_source: str
    clinical_uncertainty: str
    graph_path: Optional[List[str]] = None

class SafetyReportResponse(BaseModel):
    report_id: str
    execution_timestamp: str
    session_id: str
    patient_name: str
    patient_age: int
    patient_dob: str
    patient_mrn: str
    regimen_under_evaluation: Dict[str, Any]
    rules_evaluated_count: int = 48
    summary_counts: Dict[str, int]
    findings: List[ClinicalFinding]
    baseline_evaluated: Dict[str, Any]
    disclaimer: str
    clinician_signature: str
