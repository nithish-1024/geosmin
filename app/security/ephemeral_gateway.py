import time
import secrets
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from app.security.audit_logger import audit_logger

class EphemeralGateway:
    """
    Ephemeral Security Gateway for zero-knowledge patient ledger redemption.
    Manages single-use passcode lifecycle, enforces atomic consumption,
    guarantees session expiration after 300 seconds, and provides server-side allowlist filtering.
    """
    SESSION_TTL_SECONDS = 300  # Strict 5-minute (300s) token & session expiration window

    def __init__(self):
        self._lock = threading.Lock()
        # In-memory transient share repository
        self._shares: Dict[str, Dict[str, Any]] = {}
        # Pre-seed the demo pass code "842-916" from Figma/Claude screenshots
        self._seed_demo_code()

    def _seed_demo_code(self):
        demo_code = "842916"
        now = time.time()
        self._shares[demo_code] = {
            "token_id": "tok-geo-842916",
            "patient_id": "GK-8821",
            "passcode": "842-916",
            "raw_code": demo_code,
            "created_at": now,
            "expires_at": now + self.SESSION_TTL_SECONDS,
            "scopes": {"medications": True, "allergies": True, "diagnoses": True, "biometrics": True},
            "status": "pending",  # pending -> consumed -> expired / revoked
            "consumed_at": None,
            "session_id": "SES-98214",
            "session_expires_at": None
        }

    def generate_code(self, patient_id: str, scopes: Dict[str, bool]) -> Dict[str, Any]:
        raw_digits = f"{secrets.randbelow(1000000):06d}"
        formatted = f"{raw_digits[:3]}-{raw_digits[3:]}"
        now = time.time()
        expires_at = now + self.SESSION_TTL_SECONDS
        token_id = f"tok-geo-{raw_digits}"
        session_id = f"SES-{secrets.randbelow(100000):05d}"

        with self._lock:
            share_record = {
                "token_id": token_id,
                "patient_id": patient_id,
                "passcode": formatted,
                "raw_code": raw_digits,
                "created_at": now,
                "expires_at": expires_at,
                "scopes": dict(scopes),
                "status": "pending",
                "consumed_at": None,
                "session_id": session_id,
                "session_expires_at": None,
                "cipher_specs": "TLS 1.3 • AES-GCM 256"
            }
            self._shares[raw_digits] = share_record

        # Audit log creation
        scope_labels = [k.capitalize() for k, v in scopes.items() if v]
        audit_logger.log_event(
            actor="Patient (Ravi Kumar)",
            event="Ephemeral Token Generated",
            code=formatted,
            scopes=scope_labels,
            status="Pending Redemption"
        )

        return {
            "share_token_id": token_id,
            "passcode": formatted,
            "raw_code": raw_digits,
            "qr_payload": f"geosmin://consult/{raw_digits}?session={session_id}",
            "expires_at_iso": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
            "expires_in_seconds": self.SESSION_TTL_SECONDS,
            "cipher_specs": "TLS 1.3 • AES-GCM 256"
        }

    def consume_code(self, input_code: str, doctor_name: str = "Dr. Elena Vance, MD") -> Dict[str, Any]:
        """
        Enforce atomic consumption of a 6-digit access code.
        Once verified, marks it 'consumed'.
        Any re-access, replay, or page reload strictly raises ValueError
        (which translates to HTTP 403 Forbidden).
        Expired or revoked codes are rejected.
        """
        clean_code = input_code.replace("-", "").replace(" ", "").strip()
        now = time.time()

        with self._lock:
            record = self._shares.get(clean_code)

            if not record:
                audit_logger.log_security_event(
                    actor=doctor_name,
                    event="Invalid Access Passcode Attempt",
                    code=input_code,
                    status="Rejected (Code Not Found)"
                )
                raise ValueError("Invalid access passcode. Please request a valid code from the patient.")

            if record["status"] == "revoked":
                audit_logger.log_security_event(
                    actor=doctor_name,
                    event="Revoked Code Access Attempt Blocked",
                    code=record["passcode"],
                    status="Blocked (403 - Code Revoked)"
                )
                raise ValueError("This access code has been revoked by the patient.")

            # Requirement 1: Once verified by a clinician, mark 'consumed' so re-access or page reloads strictly fail
            if record["status"] == "consumed":
                audit_logger.log_security_event(
                    actor=doctor_name,
                    event="Unauthorized Replay / Re-access Attempt Blocked",
                    code=record["passcode"],
                    status="Blocked (403 - Already Consumed)"
                )
                raise ValueError("This access code has already been consumed. Re-access or page reloads are strictly forbidden.")

            # Requirement 3: Validate expiration after 300 seconds
            if now > record["expires_at"] or record["status"] == "expired":
                record["status"] = "expired"
                audit_logger.log_security_event(
                    actor=doctor_name,
                    event="Expired Code Access Attempt Blocked",
                    code=record["passcode"],
                    status="Blocked (403 - Code Expired)"
                )
                raise ValueError("This access code has expired (5-minute window passed). Please generate a new code.")

            # Atomically mark consumed
            record["status"] = "consumed"
            record["consumed_at"] = now
            record["session_expires_at"] = now + self.SESSION_TTL_SECONDS
            record_snapshot = dict(record)

        scope_labels = [k.capitalize() for k, v in record_snapshot["scopes"].items() if v]
        audit_logger.log_event(
            actor=doctor_name,
            event="One-Time Patient Ledger Viewed",
            code=record_snapshot["passcode"],
            scopes=scope_labels,
            status="Session Consumed & Active"
        )

        return record_snapshot

    def validate_session(self, session_id: str, current_time: Optional[float] = None) -> bool:
        """
        Requirement 3: Validate session expiration after 300 seconds.
        A session is valid ONLY if it has been consumed and current_time <= session_expires_at.
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            for record in self._shares.values():
                if record.get("session_id") == session_id:
                    if record.get("status") != "consumed":
                        return False
                    consumed_at = record.get("consumed_at", record.get("created_at", 0))
                    session_expires_at = record.get("session_expires_at", consumed_at + self.SESSION_TTL_SECONDS)
                    if now > session_expires_at:
                        record["status"] = "expired"
                        return False
                    return True
            return False

    def is_session_expired(self, session_id: str, current_time: Optional[float] = None) -> bool:
        """Helper to verify if a session is expired or invalid."""
        return not self.validate_session(session_id, current_time=current_time)

    def is_code_expired(self, input_code: str, current_time: Optional[float] = None) -> bool:
        """Requirement 3: Check if a passcode has exceeded its 300-second validity window."""
        clean_code = input_code.replace("-", "").replace(" ", "").strip()
        now = current_time if current_time is not None else time.time()
        with self._lock:
            record = self._shares.get(clean_code)
            if not record:
                return True
            return now > record.get("expires_at", 0) or record.get("status") == "expired"

    def get_session(self, session_id: str, current_time: Optional[float] = None) -> Dict[str, Any]:
        """
        Retrieve active session record.
        Raises ValueError if session is invalid, unconsumed, or expired past 300 seconds.
        """
        now = current_time if current_time is not None else time.time()
        with self._lock:
            for record in self._shares.values():
                if record.get("session_id") == session_id:
                    if record.get("status") != "consumed":
                        raise ValueError(f"Session {session_id} is not active (status: {record.get('status')}).")
                    consumed_at = record.get("consumed_at", record.get("created_at", 0))
                    session_expires_at = record.get("session_expires_at", consumed_at + self.SESSION_TTL_SECONDS)
                    if now > session_expires_at:
                        record["status"] = "expired"
                        raise ValueError(f"Session {session_id} has expired (300-second window elapsed).")
                    return dict(record)
            raise ValueError(f"Session {session_id} not found.")

    def revoke_code(self, input_code: str) -> bool:
        clean_code = input_code.replace("-", "").replace(" ", "").strip()
        with self._lock:
            if clean_code in self._shares:
                self._shares[clean_code]["status"] = "revoked"
                passcode = self._shares[clean_code]["passcode"]
            else:
                return False

        audit_logger.log_event(
            actor="Patient (Ravi Kumar)",
            event="Access Code Revoked Manually",
            code=passcode,
            scopes=[],
            status="Revoked"
        )
        return True

    def get_latest_share(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if "842916" in self._shares:
                return dict(self._shares["842916"])
            if self._shares:
                return dict(list(self._shares.values())[-1])
            return None

    @staticmethod
    def filter_patient_payload(
        patient_data: Dict[str, Any],
        scopes: Dict[str, bool],
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Requirement 2: Server-side allowlist filtering.
        Verifies that unselected scopes or private patient notes can NEVER leak into the doctor payload.

        Allowed clinical scopes:
        - medications: Active/attention medication regimen
        - allergies: Documented clinical allergies
        - diagnoses: Documented ICD-10 diagnoses
        - biometrics: Biometric telemetry

        Strictly excluded:
        - private_notes, notes, personal_notes, clinician_notes, draft_logs, messages
        - any unselected scope data (mapped strictly to empty list or None)
        """
        payload: Dict[str, Any] = {
            "session_id": session_id or "",
            "patient_name": patient_data.get("name", ""),
            "patient_age": patient_data.get("age", 0),
            "dob": patient_data.get("dob", ""),
            "mrn": patient_data.get("mrn", ""),
            "expires_in_seconds": EphemeralGateway.SESSION_TTL_SECONDS,
            "scopes_granted": [k.capitalize() for k, v in scopes.items() if v],
        }

        # 1. Allergies scope allowlisting
        if scopes.get("allergies", False):
            payload["allergies"] = [
                {
                    "allergen": a.get("allergen"),
                    "severity": a.get("severity"),
                    "reaction": a.get("reaction"),
                    "year": a.get("year")
                }
                for a in patient_data.get("allergies", [])
                if isinstance(a, dict)
            ]
        else:
            payload["allergies"] = []

        # 2. Diagnoses scope allowlisting
        if scopes.get("diagnoses", False):
            payload["diagnoses"] = [
                {
                    "condition": d.get("condition"),
                    "icd10": d.get("icd10"),
                    "status": d.get("status")
                }
                for d in patient_data.get("diagnoses", [])
                if isinstance(d, dict)
            ]
        else:
            payload["diagnoses"] = []

        # 3. Biometrics scope allowlisting
        if scopes.get("biometrics", False) and patient_data.get("biometrics"):
            bio = patient_data["biometrics"]
            payload["biometrics"] = {
                "egfr": bio.get("egfr"),
                "egfr_unit": bio.get("egfr_unit"),
                "egfr_stage": bio.get("egfr_stage"),
                "blood_pressure": bio.get("blood_pressure"),
                "bp_status": bio.get("bp_status"),
                "serum_potassium": bio.get("serum_potassium"),
                "potassium_unit": bio.get("potassium_unit"),
                "potassium_status": bio.get("potassium_status"),
                "last_inr": bio.get("last_inr"),
                "inr_target": bio.get("inr_target"),
                "inr_date": bio.get("inr_date")
            }
        else:
            payload["biometrics"] = None

        # 4. Medications scope allowlisting
        if scopes.get("medications", False):
            allowed_med_keys = {
                "id", "rx_number", "name", "generic_name", "dosage", "strength",
                "route", "cadence", "status", "prescriber", "start_date",
                "end_date", "refills", "indication", "safety_target",
                "safety_status", "atc_class", "category"
            }
            active_meds = []
            for m in patient_data.get("medications", []):
                if isinstance(m, dict) and m.get("status") in ["active", "attention"]:
                    clean_med = {k: v for k, v in m.items() if k in allowed_med_keys}
                    active_meds.append(clean_med)
            payload["active_regimen"] = active_meds
        else:
            payload["active_regimen"] = []

        payload["banner_warning"] = "This view cannot be reopened. Session will permanently expire once closed or submitted."

        # Strict defense-in-depth: explicitly ensure forbidden/private fields are purged
        FORBIDDEN_FIELDS = [
            "notes", "private_notes", "personal_notes", "clinician_notes",
            "unverified_draft_logs", "internal_records", "messages", "ehr_sync",
            "portal_status", "next_review"
        ]
        for f in FORBIDDEN_FIELDS:
            payload.pop(f, None)

        return payload

ephemeral_gateway = EphemeralGateway()
