import time
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from app.main import app
from app.security.ephemeral_gateway import EphemeralGateway
from app.security.audit_logger import audit_logger

class TestEphemeralSecurityGateway(unittest.TestCase):
    def setUp(self):
        self.gateway = EphemeralGateway()
        self.client = TestClient(app)
        audit_logger.reset_logs()

    # --- REQUIREMENT 1: ATOMIC CONSUMPTION & RE-ACCESS PREVENTION ---

    def test_token_format_and_generation(self):
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True, "diagnoses": True, "biometrics": False}
        )
        self.assertRegex(token["passcode"], r"^\d{3}-\d{3}$")
        self.assertEqual(len(token["raw_code"]), 6)
        self.assertEqual(token["expires_in_seconds"], 300)

    def test_atomic_consumption(self):
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        code = token["raw_code"]

        # 1. First consumption must succeed
        record = self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertEqual(record["status"], "consumed")
        self.assertIsNotNone(record["consumed_at"])

        # 2. Immediate re-consumption / page reload must strictly fail
        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("already been consumed", str(ctx.exception).lower())

        # 3. Third attempt must also strictly fail
        with self.assertRaises(ValueError) as ctx2:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("already been consumed", str(ctx2.exception).lower())

    def test_concurrent_atomic_consumption_race_condition(self):
        """
        Concurrency test: 10 parallel threads attempt to consume the same code at the same instant.
        Exactly one thread must succeed, and the remaining 9 must fail.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        code = token["raw_code"]

        successes = []
        failures = []

        def worker():
            try:
                rec = self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
                successes.append(rec)
            except ValueError as ex:
                failures.append(str(ex))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(successes), 1, "Exactly one thread must succeed in consuming the token")
        self.assertEqual(len(failures), 9, "9 threads must be rejected due to atomic consumption lock")
        self.assertTrue(all("already been consumed" in f.lower() for f in failures))

    def test_revocation_blocks_access(self):
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        # Patient revokes
        revoked = self.gateway.revoke_code(code)
        self.assertTrue(revoked)

        # Doctor attempt must be rejected
        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("revoked", str(ctx.exception).lower())

    def test_doctor_access_api_endpoint_403_on_reaccess(self):
        """
        Verify HTTP 403 Forbidden is returned when a clinician attempts to re-access
        or reload the page after consuming a single-use code.
        """
        # Create share via API
        share_res = self.client.post("/api/share/create", json={
            "patient_id": "GK-8821",
            "scopes": {"medications": True, "allergies": True, "diagnoses": True, "biometrics": False}
        })
        self.assertEqual(share_res.status_code, 200)
        passcode = share_res.json()["passcode"]

        # First access -> 200 OK
        access_res_1 = self.client.post("/api/doctor/access", json={
            "passcode": passcode,
            "doctor_name": "Dr. Elena Vance, MD"
        })
        self.assertEqual(access_res_1.status_code, 200)
        session_data = access_res_1.json()
        self.assertIn("session_id", session_data)

        # Subsequent access / page reload -> strictly 403 Forbidden
        access_res_2 = self.client.post("/api/doctor/access", json={
            "passcode": passcode,
            "doctor_name": "Dr. Elena Vance, MD"
        })
        self.assertEqual(access_res_2.status_code, 403)
        self.assertIn("already been consumed", access_res_2.json()["detail"].lower())

    # --- REQUIREMENT 2: SERVER-SIDE ALLOWLIST FILTERING & PRIVATE NOTE PROTECTION ---

    def test_server_side_allowlist_filtering_blocks_private_notes(self):
        """
        Verify that private patient notes, personal comments, and unverified draft logs
        can NEVER leak into the doctor payload.
        """
        untrusted_patient_payload = {
            "name": "Ravi Kumar",
            "age": 54,
            "dob": "12-May-1968",
            "mrn": "RK-48810",
            # Sensitive private fields that must never leak
            "private_notes": "Patient expressed high anxiety regarding surgery; confidential psychiatry referral requested.",
            "notes": "Internal clinic note: Non-compliant with evening warfarin cadence last month.",
            "personal_notes": "Patient prefers not to disclose marital status.",
            "clinician_notes": "Doctor draft: Review possible early Parkinsonism signs.",
            "unverified_draft_logs": "Draft unverified lab result: potential high troponin spike.",
            "messages": ["Private patient message to nurse"],
            "allergies": [
                {"allergen": "Penicillin", "severity": "Critical", "reaction": "Anaphylaxis", "year": "2015"}
            ],
            "diagnoses": [
                {"condition": "Atrial Fibrillation", "icd10": "I48.20", "status": "Active"}
            ],
            "biometrics": {
                "egfr": 78,
                "blood_pressure": "132/84"
            },
            "medications": [
                {"id": "med-001", "name": "Warfarin Sodium", "status": "active", "dosage": "5 mg"}
            ]
        }

        scopes = {"medications": True, "allergies": True, "diagnoses": True, "biometrics": True}
        doctor_payload = self.gateway.filter_patient_payload(untrusted_patient_payload, scopes, session_id="SES-001")

        # Explicitly verify private notes were excluded
        self.assertNotIn("private_notes", doctor_payload)
        self.assertNotIn("notes", doctor_payload)
        self.assertNotIn("personal_notes", doctor_payload)
        self.assertNotIn("clinician_notes", doctor_payload)
        self.assertNotIn("unverified_draft_logs", doctor_payload)
        self.assertNotIn("messages", doctor_payload)

        # Verify allowed scopes are present
        self.assertEqual(len(doctor_payload["allergies"]), 1)
        self.assertEqual(len(doctor_payload["diagnoses"]), 1)
        self.assertIsNotNone(doctor_payload["biometrics"])
        self.assertEqual(len(doctor_payload["active_regimen"]), 1)

    def test_server_side_allowlist_filtering_unselected_scopes(self):
        """
        Verify that unselected scopes are strictly omitted (set to empty list / None)
        from the returned clinician payload.
        """
        full_patient_data = {
            "name": "Ravi Kumar",
            "age": 54,
            "dob": "12-May-1968",
            "mrn": "RK-48810",
            "allergies": [
                {"allergen": "Penicillin", "severity": "Critical", "reaction": "Anaphylaxis"}
            ],
            "diagnoses": [
                {"condition": "Atrial Fibrillation", "icd10": "I48.20", "status": "Active"}
            ],
            "biometrics": {
                "egfr": 78,
                "blood_pressure": "132/84"
            },
            "medications": [
                {"id": "med-001", "name": "Warfarin", "status": "active"}
            ]
        }

        # Scenario: Patient grants ONLY medications; revokes allergies, diagnoses, biometrics
        scopes = {"medications": True, "allergies": False, "diagnoses": False, "biometrics": False}
        payload = self.gateway.filter_patient_payload(full_patient_data, scopes, session_id="SES-002")

        self.assertEqual(payload["allergies"], [], "Allergies must be empty when scope is unselected")
        self.assertEqual(payload["diagnoses"], [], "Diagnoses must be empty when scope is unselected")
        self.assertIsNone(payload["biometrics"], "Biometrics must be None when scope is unselected")
        self.assertEqual(len(payload["active_regimen"]), 1, "Medications must be present when scope is selected")

    def test_doctor_access_api_endpoint_filters_unselected_scopes(self):
        """
        Verify API /api/doctor/access applies server-side allowlist filtering on responses.
        """
        # Create share with ONLY medications and allergies (diagnoses & biometrics unselected)
        share_res = self.client.post("/api/share/create", json={
            "patient_id": "GK-8821",
            "scopes": {"medications": True, "allergies": True, "diagnoses": False, "biometrics": False}
        })
        passcode = share_res.json()["passcode"]

        access_res = self.client.post("/api/doctor/access", json={
            "passcode": passcode,
            "doctor_name": "Dr. Elena Vance, MD"
        })
        self.assertEqual(access_res.status_code, 200)
        data = access_res.json()

        self.assertTrue(len(data["allergies"]) > 0, "Allergies should be populated")
        self.assertTrue(len(data["active_regimen"]) > 0, "Medications should be populated")
        self.assertEqual(data["diagnoses"], [], "Unselected diagnoses must be filtered to empty list")
        self.assertIsNone(data["biometrics"], "Unselected biometrics must be filtered to None")
        self.assertNotIn("private_notes", data)
        self.assertNotIn("notes", data)

    # --- REQUIREMENT 3: 300-SECOND SESSION & TOKEN EXPIRATION ---

    def test_passcode_expiration_after_300_seconds(self):
        """
        Verify that an unconsumed 6-digit access code expires after 300 seconds.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        # Simulate 299 seconds elapsed -> still valid
        simulated_now = time.time() + 299
        self.assertFalse(self.gateway.is_code_expired(code, current_time=simulated_now))

        # Simulate 301 seconds elapsed -> expired
        expired_time = time.time() + 301
        self.assertTrue(self.gateway.is_code_expired(code, current_time=expired_time))

        # Directly setting expires_at to the past to test consume_code
        self.gateway._shares[code]["expires_at"] = time.time() - 1

        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("expired", str(ctx.exception).lower())

    def test_session_expiration_after_300_seconds(self):
        """
        Verify that once consumed, a clinical consultation session expires after 300 seconds.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        code = token["raw_code"]
        rec = self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        session_id = rec["session_id"]
        consumed_at = rec["consumed_at"]

        # At t = consumed_at + 250s: session must be valid
        self.assertTrue(
            self.gateway.validate_session(session_id, current_time=consumed_at + 250),
            "Session must remain valid within 300-second window"
        )
        self.assertFalse(
            self.gateway.is_session_expired(session_id, current_time=consumed_at + 250)
        )

        # At t = consumed_at + 301s: session must be expired
        self.assertFalse(
            self.gateway.validate_session(session_id, current_time=consumed_at + 301),
            "Session must expire strictly after 300 seconds"
        )
        self.assertTrue(
            self.gateway.is_session_expired(session_id, current_time=consumed_at + 301)
        )

        # get_session must raise ValueError when session has expired
        with self.assertRaises(ValueError) as ctx:
            self.gateway.get_session(session_id, current_time=consumed_at + 301)
        self.assertIn("expired", str(ctx.exception).lower())

    def test_doctor_access_api_endpoint_403_on_expired_code(self):
        """
        Verify /api/doctor/access returns HTTP 403 when consuming an expired code.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        # Expire in global ephemeral_gateway instance
        from app.security.ephemeral_gateway import ephemeral_gateway
        ephemeral_gateway._shares[code] = self.gateway._shares[code]
        ephemeral_gateway._shares[code]["expires_at"] = time.time() - 5

        res = self.client.post("/api/doctor/access", json={
            "passcode": token["passcode"],
            "doctor_name": "Dr. Elena Vance, MD"
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("expired", res.json()["detail"].lower())

    # --- AUDIT LOGGER SECURITY TESTS ---

    def test_audit_logger_records_replay_attack_and_sanitizes(self):
        """
        Verify audit logger records replay blocking events and sanitizes sensitive note scopes.
        """
        # Test sanitization
        record = audit_logger.log_event(
            actor="Attacker",
            event="Payload Test",
            code="123-456",
            scopes=["Medications", "Private Note Leak Test", "Allergies"],
            status="Flagged"
        )
        self.assertIn("[REDACTED_SENSITIVE_SCOPE]", record["scopes"])
        self.assertNotIn("Private Note Leak Test", record["scopes"])

        # Test security alert logging on replay attempt
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]
        self.gateway.consume_code(code)

        # Replay attempt
        try:
            self.gateway.consume_code(code)
        except ValueError:
            pass

        logs = audit_logger.get_logs()
        alert_logs = [log for log in logs if "SECURITY ALERT" in log["event"]]
        self.assertTrue(len(alert_logs) > 0, "Security alert must be recorded in audit log on replay attempt")
        self.assertIn("Blocked", alert_logs[0]["status"])

if __name__ == "__main__":
    unittest.main()
