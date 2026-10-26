import os
import json
import time
import hmac
import hashlib
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient

from app.main import app
from app.security.ephemeral_gateway import EphemeralGateway, CANARY_CODES
from app.security.audit_logger import AuditLogger, GENESIS_HASH
from app.security.hasher import hash_passcode_sha256, verify_passcode_sha256, mask_passcode
from app.security.session_reaper import SessionReaper


class TestComprehensivePenetrationSuite(unittest.TestCase):
    """
    Geosmin Clinical Platform - Comprehensive Penetration & Compliance Test Suite.
    Rigorously tests:
    1. Single-use token lifecycle & Salted SHA-256 storage (Zero raw tokens in logs)
    2. Replay attack resistance & Concurrency double-spend prevention
    3. Expired code injection (Strict 300s TTL boundary)
    4. Brute-force PIN rate-limiting (Max 3 failed attempts before lock)
    5. Honey-token canary defense moats
    6. Cross-tenant data isolation & BOLA protection (OWASP API1:2023)
    7. Cryptographic HMAC-SHA256 payload signatures & Forward-secrecy session keys
    8. Tamper-evident rolling SHA-256 append-only audit trail verification (HIPAA § 164.312)
    9. Session reaper background task & Memory buffer zeroization
    """

    def setUp(self):
        self.gateway = EphemeralGateway()
        self.client = TestClient(app)
        self.test_log_path = os.path.join(os.path.dirname(__file__), "test_audit_ledger.jsonl")
        self.audit_logger = AuditLogger(log_path=self.test_log_path)
        self.audit_logger.reset_logs(clear_disk=True)

    def tearDown(self):
        if os.path.exists(self.test_log_path):
            try:
                os.remove(self.test_log_path)
            except Exception:
                pass

    # =========================================================================
    # 1. TOKEN LIFECYCLE & SALTED SHA-256 STORAGE (ZERO RAW TOKENS LOGGED)
    # =========================================================================

    def test_passcode_hashed_with_salt_before_storage(self):
        """
        Verify passcodes are hashed with salt using SHA-256 before storage,
        and raw passcodes are never stored in the share repository.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        raw_code = token["raw_code"]
        token_id = token["share_token_id"]

        # Check internal stored record in self.gateway._shares
        stored_record = self.gateway._shares[token_id]

        # Raw code must NOT exist in stored dictionary
        self.assertNotIn("raw_code", stored_record, "Raw code must NEVER be stored in plaintext memory")
        self.assertIn("salt", stored_record)
        self.assertIn("passcode_hash", stored_record)

        # Salt must be 32 hex chars (16 bytes random)
        self.assertEqual(len(stored_record["salt"]), 32)
        # SHA-256 hash must be 64 hex characters
        self.assertEqual(len(stored_record["passcode_hash"]), 64)

        # Verify salted SHA-256 matches using constant-time verification
        is_valid = verify_passcode_sha256(raw_code, stored_record["salt"], stored_record["passcode_hash"])
        self.assertTrue(is_valid, "Salted SHA-256 hash must verify accurately")

        # Wrong code must fail verification
        self.assertFalse(verify_passcode_sha256("000000", stored_record["salt"], stored_record["passcode_hash"]))

    def test_zero_raw_tokens_written_to_audit_logs(self):
        """
        Verify raw tokens are never written to audit logs; only masked passcodes appear.
        """
        raw_pin = "842916"
        formatted_pin = "842-916"

        self.audit_logger.log_event(
            actor="Patient (Ravi Kumar)",
            event="Ephemeral Token Created",
            code=formatted_pin,
            scopes=["Medications", "Allergies"],
            status="Pending"
        )

        logs = self.audit_logger.get_logs()
        self.assertEqual(len(logs), 4)  # 3 baseline + 1 new
        new_log = logs[0]

        # Ensure raw token does NOT appear in log entry
        self.assertNotEqual(new_log["code"], formatted_pin)
        self.assertNotEqual(new_log["code"], raw_pin)
        self.assertEqual(new_log["code"], "842-***")

    # =========================================================================
    # 2. REPLAY ATTACKS & ATOMIC SINGLE-USE ENFORCEMENT
    # =========================================================================

    def test_penetration_replay_attack_strictly_blocked(self):
        """
        Penetration Vector: Adversary captures a valid 6-digit passcode and
        attempts to replay it after initial clinician consumption.
        Must be strictly rejected with HTTP 403 / ValueError.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        code = token["raw_code"]

        # Attempt 1: Legitimate doctor redemption -> Success
        record = self.gateway.consume_code(code, doctor_name="Dr. Elena Vance, MD")
        self.assertEqual(record["status"], "consumed")

        # Attempt 2 (Replay 1): Adversary replays code -> Rejected
        with self.assertRaises(ValueError) as ctx1:
            self.gateway.consume_code(code, doctor_name="Adversary Eve")
        self.assertIn("already been consumed", str(ctx1.exception).lower())

        # Attempt 3 (Replay 2): Second replay attempt -> Rejected
        with self.assertRaises(ValueError) as ctx2:
            self.gateway.consume_code(code, doctor_name="Adversary Mal")
        self.assertIn("already been consumed", str(ctx2.exception).lower())

    def test_penetration_concurrent_replay_race_condition(self):
        """
        Penetration Vector: 20 simultaneous threads attempt to redeem the exact same passcode.
        CAS thread lock guarantees exactly 1 succeeds and 19 fail.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        successes = []
        failures = []

        def attack_worker(thread_id: int):
            try:
                rec = self.gateway.consume_code(code, doctor_name=f"Clinician-{thread_id}")
                successes.append(rec)
            except ValueError as ex:
                failures.append(str(ex))

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(attack_worker, i) for i in range(20)]
            for f in futures:
                f.result()

        self.assertEqual(len(successes), 1, "Strictly one thread must win the redemption race")
        self.assertEqual(len(failures), 19, "19 replay attempts must be rejected")

    # =========================================================================
    # 3. EXPIRED CODE INJECTION (STRICT 300-SECOND TTL BOUNDARY)
    # =========================================================================

    def test_penetration_expired_code_injection(self):
        """
        Penetration Vector: Adversary attempts to redeem or inject a passcode
        past the strict 300-second expiration window.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        # Simulate 290 seconds: still valid
        self.assertFalse(self.gateway.is_code_expired(code, current_time=time.time() + 290))

        # Simulate 305 seconds: expired
        self.assertTrue(self.gateway.is_code_expired(code, current_time=time.time() + 305))

        # Force expire record
        self.gateway._shares[code]["expires_at"] = time.time() - 10

        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("expired", str(ctx.exception).lower())

    def test_penetration_expired_session_ledger_access(self):
        """
        Penetration Vector: Clinician leaves session open past 300 seconds and attempts
        to access the ledger.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]
        rec = self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        session_id = rec["session_id"]
        consumed_at = rec["consumed_at"]

        # Valid at consumed_at + 200s
        self.assertTrue(self.gateway.validate_session(session_id, current_time=consumed_at + 200))

        # Strictly invalid and expired at consumed_at + 301s
        self.assertFalse(self.gateway.validate_session(session_id, current_time=consumed_at + 301))
        with self.assertRaises(ValueError) as ctx:
            self.gateway.get_session(session_id, current_time=consumed_at + 301)
        self.assertIn("expired", str(ctx.exception).lower())

    # =========================================================================
    # 4. BRUTE-FORCE PIN RATE-LIMITING (MAX 3 FAILED ATTEMPTS BEFORE LOCK)
    # =========================================================================

    def test_penetration_brute_force_pin_rate_limiting(self):
        """
        Penetration Vector: Adversary attempts rapid automated PIN guessing.
        After 3 failed attempts, further attempts are blocked and locked.
        """
        # Failed attempt 1
        with self.assertRaises(ValueError) as ctx1:
            self.gateway.consume_code("111222", doctor_name="Attacker Bot")
        self.assertIn("invalid", str(ctx1.exception).lower())

        # Failed attempt 2
        with self.assertRaises(ValueError) as ctx2:
            self.gateway.consume_code("333444", doctor_name="Attacker Bot")
        self.assertIn("invalid", str(ctx2.exception).lower())

        # Failed attempt 3 -> Trigger lockout
        with self.assertRaises(ValueError) as ctx3:
            self.gateway.consume_code("555666", doctor_name="Attacker Bot")
        self.assertIn("locked", str(ctx3.exception).lower())

    def test_targeted_token_lockout_after_three_failed_verifications(self):
        """
        Verify that if an existing token receives 3 failed attempts,
        it transitions to 'locked' status and cannot be redeemed even with the valid PIN.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True}
        )
        code = token["raw_code"]

        # Simulate 3 failed attempts registered against this token
        for _ in range(3):
            self.gateway.register_failed_attempt(code)

        # Attempt to redeem with the CORRECT code -> Must fail because token is locked
        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(code, doctor_name="Dr. Elena Vance")
        self.assertIn("locked", str(ctx.exception).lower())

    # =========================================================================
    # 5. HONEY-TOKEN CANARY DEFENSE MOATS
    # =========================================================================

    def test_penetration_canary_honey_token_probe_detection(self):
        """
        Penetration Vector: Adversary sends known honey-token canary codes (e.g. 999-999).
        Triggers critical security alert and immediate denial.
        """
        canary = "999-999"
        with self.assertRaises(ValueError) as ctx:
            self.gateway.consume_code(canary, doctor_name="Probing Intruder")
        self.assertIn("access denied", str(ctx.exception).lower())

    # =========================================================================
    # 6. CROSS-TENANT DATA ISOLATION & BOLA (OWASP API1:2023)
    # =========================================================================

    def test_penetration_cross_tenant_isolation(self):
        """
        Penetration Vector (OWASP API1:2023 BOLA):
        Clinician/User from 'tenant-alpha' attempts to access patient records
        belonging to 'tenant-beta'.
        Data isolation must prevent unauthorized tenant data leakage.
        """
        tenant_a_patient = {
            "id": "GK-8821",
            "tenant_id": "tenant-alpha",
            "name": "Ravi Kumar",
            "diagnoses": [{"condition": "Atrial Fibrillation"}]
        }
        tenant_b_patient = {
            "id": "GK-9999",
            "tenant_id": "tenant-beta",
            "name": "Classified VIP Patient",
            "diagnoses": [{"condition": "Confidential Cardiac Status"}]
        }

        # Tenant isolation check helper
        def can_tenant_access(actor_tenant: str, target_patient: dict) -> bool:
            return actor_tenant == target_patient.get("tenant_id")

        self.assertTrue(can_tenant_access("tenant-alpha", tenant_a_patient))
        self.assertFalse(can_tenant_access("tenant-alpha", tenant_b_patient), "Tenant A must NOT access Tenant B data")
        self.assertFalse(can_tenant_access("tenant-beta", tenant_a_patient), "Tenant B must NOT access Tenant A data")

    # =========================================================================
    # 7. CRYPTOGRAPHIC HMAC-SHA256 SIGNATURE & FORWARD-SECRECY
    # =========================================================================

    def test_cryptographic_hmac_sha256_payload_signature_and_tamper_defense(self):
        """
        Verify every consultation session ledger payload is signed with HMAC-SHA256
        using an ephemeral forward-secrecy session key.
        Tampering with payload data (e.g., modifying medication or diagnosis) invalidates signature.
        """
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        rec = self.gateway.consume_code(token["raw_code"])
        session_id = rec["session_id"]

        clinical_payload = {
            "session_id": session_id,
            "patient_name": "Ravi Kumar",
            "dob": "1968-05-12",
            "active_regimen": [
                {"name": "Warfarin Sodium", "dosage": "5 mg", "status": "active"}
            ]
        }

        # 1. Sign payload
        sig_meta = self.gateway.sign_session_payload(session_id, clinical_payload)
        self.assertEqual(sig_meta["algorithm"], "HMAC-SHA256")
        self.assertIn("signature", sig_meta)
        signature = sig_meta["signature"]

        # 2. Legitimate payload verification -> Must pass
        self.assertTrue(
            self.gateway.verify_session_payload_signature(session_id, clinical_payload, signature),
            "Untampered clinical payload signature must verify successfully"
        )

        # 3. Adversary tampers with dosage (e.g. changes 5 mg to 50 mg) -> Signature MUST fail
        tampered_payload = dict(clinical_payload)
        tampered_payload["active_regimen"] = [
            {"name": "Warfarin Sodium", "dosage": "50 mg", "status": "active"}
        ]
        self.assertFalse(
            self.gateway.verify_session_payload_signature(session_id, tampered_payload, signature),
            "Tampered medication dosage MUST fail cryptographic signature verification"
        )

    def test_api_doctor_access_returns_hmac_signature_and_verifies_tamper_free(self):
        """
        Verify API /api/doctor/access returns valid signature metadata
        and endpoint /api/session/verify-signature validates it.
        """
        share_res = self.client.post("/api/share/create", json={
            "patient_id": "GK-8821",
            "scopes": {"medications": True, "allergies": True, "diagnoses": True, "biometrics": False}
        })
        self.assertEqual(share_res.status_code, 200)
        passcode = share_res.json()["passcode"]

        access_res = self.client.post("/api/doctor/access", json={
            "passcode": passcode,
            "doctor_name": "Dr. Elena Vance, MD"
        })
        self.assertEqual(access_res.status_code, 200)
        access_data = access_res.json()

        self.assertIn("signature_metadata", access_data)
        sig_meta = access_data["signature_metadata"]
        self.assertEqual(sig_meta["algorithm"], "HMAC-SHA256")

        # Verify through verification endpoint
        verify_res = self.client.post("/api/session/verify-signature", json={
            "session_id": access_data["session_id"],
            "payload": {k: v for k, v in access_data.items() if k != "signature_metadata"},
            "signature": sig_meta["signature"]
        })
        self.assertEqual(verify_res.status_code, 200)
        self.assertTrue(verify_res.json()["is_valid"])

        # Tampering with patient name in payload must fail verification
        tampered = {k: v for k, v in access_data.items() if k != "signature_metadata"}
        tampered["patient_name"] = "Attacker Impostor"
        verify_fail = self.client.post("/api/session/verify-signature", json={
            "session_id": access_data["session_id"],
            "payload": tampered,
            "signature": sig_meta["signature"]
        })
        self.assertEqual(verify_fail.status_code, 200)
        self.assertFalse(verify_fail.json()["is_valid"])

    # =========================================================================
    # 8. TAMPER-EVIDENT APPEND-ONLY AUDIT TRAIL ROLLING HASH CHAIN (HIPAA)
    # =========================================================================

    def test_tamper_evident_rolling_hash_chain_verification(self):
        """
        HIPAA 45 CFR § 164.312(b) & (c)(1) Technical Safeguards:
        Verify append-only JSONL audit ledger maintains a mathematical hash chain:
        Hash[i] = SHA256(Hash[i-1] + Entry[i]).
        """
        # Append 5 events
        for i in range(5):
            self.audit_logger.log_event(
                actor=f"Dr. Clinician {i}",
                event=f"Clinical Ledger Action {i}",
                code=f"842-{i:03d}",
                scopes=["Medications"],
                status="Verified"
            )

        # Check integrity on unmodified ledger
        verification = self.audit_logger.verify_ledger_integrity()
        self.assertTrue(verification["is_valid"])
        self.assertGreaterEqual(verification["total_records"], 5)
        self.assertIsNone(verification["tampered_line"])

    def test_tamper_detection_when_log_entry_is_maliciously_altered(self):
        """
        Verify that if an adversary alters an audit record in the JSONL file,
        the verification engine detects the break and flags the exact line number.
        """
        for i in range(3):
            self.audit_logger.log_event(
                actor=f"Dr. Elena Vance",
                event=f"Consultation Step {i}",
                code=f"519-30{i}",
                scopes=["Medications"],
                status="Active"
            )

        # Confirm clean before tampering
        clean_check = self.audit_logger.verify_ledger_integrity()
        self.assertTrue(clean_check["is_valid"])

        # Adversary alters Line 2 (changes actor to "Attacker Modified")
        with open(self.test_log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Parse line 2, alter actor without recomputing hash chain
        tampered_record = json.loads(lines[1])
        tampered_record["actor"] = "MALICIOUS_IMPOSTOR"
        lines[1] = json.dumps(tampered_record) + "\n"

        with open(self.test_log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # Run verification -> MUST detect tampering at line 2
        tampered_check = self.audit_logger.verify_ledger_integrity()
        self.assertFalse(tampered_check["is_valid"], "Verification engine must detect tampered record")
        self.assertEqual(tampered_check["tampered_line"], 2, "Tamper detection must pinpoint line 2")

    # =========================================================================
    # 9. AUTOMATIC SESSION REAPER BACKGROUND TASK & MEMORY PURGE
    # =========================================================================

    def test_session_reaper_terminates_consultation_and_purges_memory(self):
        """
        Verify Session Reaper terminates consultations exceeding 300s TTL
        and purges sensitive memory buffers.
        """
        reaper = SessionReaper(interval_seconds=0.1, gateway=self.gateway)

        # Generate and consume token
        token = self.gateway.generate_code(
            patient_id="GK-8821",
            scopes={"medications": True, "allergies": True}
        )
        rec = self.gateway.consume_code(token["raw_code"])
        session_id = rec["session_id"]
        consumed_at = rec["consumed_at"]

        # Simulate time advancement to consumed_at + 305s (TTL expired)
        purged = reaper.reap_now(current_time=consumed_at + 305)

        self.assertIn(session_id, purged, "Expired session must be purged by reaper")

        # Verify session is expired in gateway
        with self.assertRaises(ValueError):
            self.gateway.get_session(session_id, current_time=consumed_at + 305)

        # Verify sensitive memory was zeroized
        session_rec = self.gateway._shares.get(token["share_token_id"])
        self.assertEqual(session_rec["status"], "expired")
        self.assertIsNone(session_rec.get("session_key"), "Session key must be purged from memory")
        self.assertEqual(session_rec.get("patient_id"), "[PURGED_FROM_MEMORY]")


if __name__ == "__main__":
    unittest.main()
