import json
import os
from contextlib import asynccontextmanager
from typing import Dict, Any, List
from fastapi import FastAPI, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.models.schemas import (
    PatientProfile,
    CreateShareRequest,
    CreateShareResponse,
    DoctorAccessRequest,
    DoctorSessionData,
    AddMedicationRequest,
    RxNormValidationResponse,
    PrescribeNewMedicationRequest,
    SafetyReportResponse,
    ClinicalFinding
)
from app.security.ephemeral_gateway import ephemeral_gateway
from app.security.audit_logger import audit_logger
from app.security.session_reaper import session_reaper
from app.services.safety_engine import safety_engine
from app.services.rxnorm_normalizer import rxnorm_normalizer
from app.services.knowledge_graph import knowledge_graph, graph_router
from app.routers import auth_router, tokens_router, patients_router, speech_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    session_reaper.start()
    yield
    session_reaper.stop()

app = FastAPI(
    title="Geosmin Clinical Safety Platform",
    description="A medication safety platform connecting patient history with algorithmic pharmacological verification.",
    version="2.0.0",
    lifespan=lifespan
)

# Mount Geosmin 2.0 modular production routers
app.include_router(auth_router)
app.include_router(tokens_router)
app.include_router(patients_router)
if speech_router:
    app.include_router(speech_router)
app.include_router(graph_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load patient clinical baseline from file
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_FILE = os.path.join(DATA_DIR, "clinical_db.json")

def load_patient_data(patient_id: str = None) -> Dict[str, Any]:
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    patients = data.get("patients", {})
    if patient_id and patient_id in patients:
        return patients[patient_id]
    active_id = data.get("active_patient_id", "GK-8821")
    if active_id in patients:
        return patients[active_id]
    return data.get("patient", {})

def save_patient_data(patient_data: Dict[str, Any]):
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    pid = patient_data.get("id", "GK-8821")
    if "patients" not in data:
        data["patients"] = {}
    data["patients"][pid] = patient_data
    if data.get("active_patient_id") == pid or not data.get("patient"):
        data["patient"] = patient_data
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# --- API ROUTES ---

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "Geosmin Medication Safety Platform",
        "knowledge_graph": knowledge_graph.get_summary_metrics()
    }

@app.get("/api/patients")
def get_all_patients():
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    patients = data.get("patients", {})
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "age": p["age"],
            "gender": p["gender"],
            "summary": f"{p['diagnoses'][0]['condition'] if p.get('diagnoses') else 'Clinical Record'}"
        }
        for p in patients.values()
    ]

@app.post("/api/patient/switch")
def switch_active_patient(payload: Dict[str, str]):
    pid = payload.get("id", "GK-8821")
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if pid not in data.get("patients", {}):
        raise HTTPException(status_code=404, detail="Patient profile not found")
    data["active_patient_id"] = pid
    data["patient"] = data["patients"][pid]
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"message": f"Active patient switched to {data['patient']['name']}", "patient": data["patient"]}

@app.get("/api/patient", response_model=PatientProfile)
def get_patient(id: str = None):
    return load_patient_data(id)

@app.post("/api/rxnorm/validate", response_model=RxNormValidationResponse)
def validate_rxnorm(payload: Dict[str, str]):
    query = payload.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="Query string is required")
    res = rxnorm_normalizer.normalize(query)
    return RxNormValidationResponse(**res)

@app.post("/api/patient/medication")
def add_patient_medication(req: AddMedicationRequest):
    patient = load_patient_data()
    norm = rxnorm_normalizer.normalize(req.name)
    
    new_id = f"med-{len(patient['medications']) + 1:03d}"
    new_med = {
        "id": new_id,
        "rx_number": f"Rx #{90000 + len(patient['medications'])}-N",
        "name": req.name,
        "generic_name": norm.get("concept_name", req.name),
        "dosage": f"{req.dosage} {req.cadence}",
        "strength": req.dosage,
        "route": "Oral Tablet",
        "cadence": req.cadence,
        "status": "active",
        "prescriber": req.prescribing_clinician,
        "start_date": "Oct 25, 2024",
        "end_date": "Ongoing",
        "refills": 3,
        "indication": req.therapeutic_indication,
        "safety_target": norm.get("dietary_guideline", "Routine pharmacotherapy monitoring."),
        "safety_status": "Reconciled",
        "atc_class": norm.get("atc_code", "A10BA02"),
        "category": norm.get("therapeutic_class", "Prescription Medication")
    }
    
    patient["medications"].insert(0, new_med)
    save_patient_data(patient)
    return {"message": "Medication added successfully to timeline", "medication": new_med}

@app.post("/api/share/create", response_model=CreateShareResponse)
def create_one_time_share(req: CreateShareRequest):
    scopes_dict = req.scopes.model_dump()
    share = ephemeral_gateway.generate_code(patient_id=req.patient_id, scopes=scopes_dict)
    return CreateShareResponse(**share)

@app.post("/api/share/revoke")
def revoke_share(payload: Dict[str, str]):
    code = payload.get("passcode", "")
    success = ephemeral_gateway.revoke_code(code)
    if not success:
        raise HTTPException(status_code=404, detail="Code not found or already invalid")
    return {"message": "Access code successfully revoked"}

@app.get("/api/share/latest")
def get_latest_share():
    share = ephemeral_gateway.get_latest_share()
    if not share:
        # Generate default
        scopes = {"medications": True, "allergies": True, "diagnoses": True, "biometrics": True}
        return ephemeral_gateway.generate_code("GK-8821", scopes)
    return share

@app.post("/api/doctor/access", response_model=DoctorSessionData)
def doctor_access_ledger(req: DoctorAccessRequest):
    try:
        record = ephemeral_gateway.consume_code(
            input_code=req.passcode,
            doctor_name=req.doctor_name
        )
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    patient = load_patient_data()
    scopes = record["scopes"]

    # Filter strictly according to granted scopes (Whitelist, not blacklist)
    allergies = patient["allergies"] if scopes.get("allergies") else []
    diagnoses = patient["diagnoses"] if scopes.get("diagnoses") else []
    biometrics = patient["biometrics"] if scopes.get("biometrics") else None
    medications = [m for m in patient["medications"] if m["status"] in ["active", "attention"]] if scopes.get("medications") else []

    granted_list = [k.capitalize() for k, v in scopes.items() if v]

    # Assemble canonical payload for cryptographic signing
    payload_dict = {
        "session_id": record["session_id"],
        "patient_name": patient["name"],
        "patient_age": patient["age"],
        "dob": patient["dob"],
        "mrn": patient["mrn"],
        "expires_in_seconds": 300,
        "scopes_granted": granted_list,
        "allergies": allergies,
        "diagnoses": diagnoses,
        "biometrics": biometrics,
        "active_regimen": medications,
        "banner_warning": "This view cannot be reopened. Session will permanently expire once closed or submitted."
    }

    # Cryptographic HMAC-SHA256 signature using forward-secret ephemeral session key
    signature_meta = ephemeral_gateway.sign_session_payload(
        session_id=record["session_id"],
        payload_data=payload_dict
    )

    return DoctorSessionData(
        **payload_dict,
        signature_metadata=signature_meta
    )

@app.post("/api/doctor/prescribe", response_model=SafetyReportResponse)
def prescribe_and_screen(req: PrescribeNewMedicationRequest):
    patient = load_patient_data()
    
    # 1. NLM RxNav / openFDA Entity Resolution & LASA Screening
    norm_result = None
    resolved_drug_name = req.medication_name
    try:
        norm_result = rxnorm_normalizer.normalize(req.medication_name)
        if norm_result and norm_result.get("generic_name"):
            resolved_drug_name = norm_result["generic_name"]
    except HTTPException as e:
        # Re-raise strict 422 if similarity is below cutoff 0.70
        raise e
    except Exception as e:
        # Fallback to provided medication name on network error
        pass

    report = safety_engine.evaluate_prescription(
        new_drug_name=resolved_drug_name,
        dosage=req.dosage,
        frequency=req.frequency,
        duration=req.duration,
        patient_data=patient,
        session_id=req.session_id
    )

    # 2. Append FDA / ISMP LASA Alert if phonetic risk detected
    if norm_result and norm_result.get("lasa_warning"):
        lasa_finding = ClinicalFinding(
            id="fnd-lasa-01",
            severity="AMBER_REVIEW",
            severity_label="[AMBER REVIEW] Look-Alike Sound-Alike (LASA) Hazard",
            category="LASA Phonetic Conflict",
            class_level="FDA / ISMP TALL MAN SAFETY",
            headline=f"Tall Man Phonetic Conflict: {req.medication_name}",
            title=f"Phonetic Disambiguation: {req.medication_name} vs {resolved_drug_name}",
            description=norm_result["lasa_warning"],
            evidence_source="FDA / ISMP List of Error-Prone Drug Names & Tall Man Lettering",
            clinical_uncertainty="Phonetic edit-distance collision detected in pharmaceutical ontology",
            graph_path=[f"{req.medication_name} --[phonetic_lasa_collision]--> {resolved_drug_name}"]
        )
        report.findings.insert(0, lasa_finding)
        report.summary_counts["amber_review"] = report.summary_counts.get("amber_review", 0) + 1
    
    # Audit log prescription safety execution
    audit_logger.log_event(
        actor="Dr. Elena Vance, MD",
        event=f"Algorithmic Safety Evaluation Ran: {resolved_drug_name} ({req.medication_name}) {req.dosage}",
        code=req.session_id,
        scopes=["Interactions", "Duplicates", "Allergies", "Disease-Conflict", "RxNorm-LASA"],
        status="Report Formulated"
    )
    
    return report

@app.get("/api/audit/logs")
def get_audit_logs():
    return audit_logger.get_logs()

@app.get("/api/audit/verify")
def verify_audit_ledger():
    """
    Cryptographic audit trail integrity verification (HIPAA 45 CFR § 164.312(c)(1)).
    Traverses the rolling SHA-256 hash chain and confirms no tampering, deletion,
    or unauthorized insertion of records has occurred.
    """
    return audit_logger.verify_ledger_integrity()

@app.post("/api/session/verify-signature")
def verify_session_signature(req: Dict[str, Any]):
    """
    Verify the HMAC-SHA256 signature of a consultation session ledger payload
    using constant-time comparison to prevent tampering and timing attacks.
    """
    session_id = req.get("session_id", "")
    payload_data = req.get("payload", {})
    signature = req.get("signature", "")
    if not session_id or not signature:
        raise HTTPException(status_code=400, detail="session_id and signature are required")

    valid = ephemeral_gateway.verify_session_payload_signature(session_id, payload_data, signature)
    return {
        "session_id": session_id,
        "is_valid": valid,
        "signature_status": "Valid - Tamper-free payload" if valid else "INVALID - Payload signature mismatch"
    }

@app.post("/api/reaper/sweep")
def trigger_reaper_sweep():
    """
    Manually trigger consultation session reaper sweep.
    Terminates consultations exceeding the 300-second TTL and purges memory buffers.
    """
    purged = session_reaper.reap_now()
    return {
        "status": "sweep_completed",
        "terminated_sessions": purged,
        "total_reaped_lifetime": session_reaper.total_reaped
    }

@app.get("/api/reaper/status")
def get_reaper_status():
    """
    Telemetry for background consultation session reaper daemon.
    """
    return {
        "is_running": session_reaper.is_running,
        "interval_seconds": session_reaper.interval,
        "total_reaped": session_reaper.total_reaped
    }

# --- STATIC FILES & SPA SERVING ---

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_spa():
    index_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_path)
