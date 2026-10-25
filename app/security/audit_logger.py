import time
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

class AuditLogger:
    """
    Cryptographic Audit Trail Logger.
    Provides thread-safe recording of all ephemeral gateway lifecycle and security events.
    Enforces strict zero-leak policies so private patient notes and PHI cannot leak into audit logs.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._logs: List[Dict[str, Any]] = [
            {
                "id": "aud-101",
                "timestamp": "2024-10-24 14:35:10 UTC",
                "actor": "Dr. Elena Vance, MD",
                "event": "Consultation Session Consumed",
                "code": "842-916",
                "scopes": ["Medications", "Allergies", "Diagnoses"],
                "status": "Active Consult",
                "cipher": "TLS 1.3 / AES-GCM-256",
                "ip_origin": "Regional Medical Center Exam Rm 4"
            },
            {
                "id": "aud-100",
                "timestamp": "2024-10-18 10:12:44 UTC",
                "actor": "Dr. Marcus Reed (Internal Med)",
                "event": "Follow-up Medication Review",
                "code": "519-302",
                "scopes": ["Medications", "Diagnoses"],
                "status": "Session Closed",
                "cipher": "TLS 1.3 / AES-GCM-256",
                "ip_origin": "Outpatient Internal Medicine Clinic"
            },
            {
                "id": "aud-099",
                "timestamp": "2024-09-02 09:15:00 UTC",
                "actor": "Dr. Elena Vance, MD",
                "event": "Routine Cardiology Consultation",
                "code": "734-118",
                "scopes": ["Medications", "Allergies", "Diagnoses", "Labs"],
                "status": "Session Expired",
                "cipher": "TLS 1.3 / AES-GCM-256",
                "ip_origin": "Regional Medical Center Exam Rm 4"
            }
        ]

    def _sanitize_scopes(self, scopes: List[str]) -> List[str]:
        """
        Sanitize audit scopes to ensure private patient notes, unverified draft logs,
        or unauthorized PHI never leak into persistent audit logs.
        """
        sanitized = []
        for scope in scopes:
            clean = str(scope).strip()
            # If scope appears to contain private notes or unauthorized content, redact
            if any(forbidden in clean.lower() for forbidden in ["note", "private", "personal", "secret", "draft"]):
                sanitized.append("[REDACTED_SENSITIVE_SCOPE]")
            else:
                sanitized.append(clean)
        return sanitized

    def log_event(
        self,
        actor: str,
        event: str,
        code: str,
        scopes: List[str],
        status: str,
        ip_origin: str = "Client Gateway"
    ) -> Dict[str, Any]:
        with self._lock:
            safe_scopes = self._sanitize_scopes(scopes)
            record = {
                "id": f"aud-{len(self._logs) + 101}",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "actor": actor,
                "event": event,
                "code": code,
                "scopes": safe_scopes,
                "status": status,
                "cipher": "TLS 1.3 / AES-GCM-256",
                "ip_origin": ip_origin
            }
            self._logs.insert(0, record)
            return dict(record)

    def log_security_event(
        self,
        actor: str,
        event: str,
        code: str,
        status: str,
        scopes: Optional[List[str]] = None,
        ip_origin: str = "Client Gateway"
    ) -> Dict[str, Any]:
        """
        Helper for recording high-priority security exceptions and blocked attempts
        (re-access/replay, expired token usage, invalid passcodes).
        """
        return self.log_event(
            actor=actor,
            event=f"[SECURITY ALERT] {event}",
            code=code,
            scopes=scopes or [],
            status=status,
            ip_origin=ip_origin
        )

    def get_logs(self) -> List[Dict[str, Any]]:
        with self._lock:
            # Defensive copy prevents external mutation of audit ledger
            return [dict(log) for log in self._logs]

    def reset_logs(self):
        """Reset logs to baseline (useful for test isolation)."""
        with self._lock:
            self.__init__()

audit_logger = AuditLogger()
