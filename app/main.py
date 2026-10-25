import json
import os
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
    SafetyReportResponse
)
from app.security.ephemeral_gateway import ephemeral_gateway
from app.security.audit_logger import audit_logger
from app.services.safety_engine import safety_engine
from app.services.rxnorm_normalizer import rxnorm_normalizer
from app.services.knowledge_graph import knowledge_graph

app = FastAPI(
    title="Geosmin Clinical Safety Platform",
    description="A medication safety platform connecting patient history with algorithmic pharmacological verification.",
    version="1.0.0"
)

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

    return DoctorSessionData(
        session_id=record["session_id"],
        patient_name=patient["name"],
        patient_age=patient["age"],
        dob=patient["dob"],
        mrn=patient["mrn"],
        expires_in_seconds=300,
        scopes_granted=granted_list,
        allergies=allergies,
        diagnoses=diagnoses,
        biometrics=biometrics,
        active_regimen=medications
    )

@app.post("/api/doctor/prescribe", response_model=SafetyReportResponse)
def prescribe_and_screen(req: PrescribeNewMedicationRequest):
    patient = load_patient_data()
    report = safety_engine.evaluate_prescription(
        new_drug_name=req.medication_name,
        dosage=req.dosage,
        frequency=req.frequency,
        duration=req.duration,
        patient_data=patient,
        session_id=req.session_id
    )
    
    # Audit log prescription safety execution
    audit_logger.log_event(
        actor="Dr. Elena Vance, MD",
        event=f"Algorithmic Safety Evaluation Ran: {req.medication_name} {req.dosage}",
        code=req.session_id,
        scopes=["Interactions", "Duplicates", "Allergies", "Disease-Conflict"],
        status="Report Formulated"
    )
    
    return report

@app.get("/api/audit/logs")
def get_audit_logs():
    return audit_logger.get_logs()

# --- STATIC FILES & SPA SERVING ---

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_spa():
    index_path = os.path.join(STATIC_DIR, "index.html")
    return FileResponse(index_path)
