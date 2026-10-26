import io
import wave
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.speech_router import speech_router
from app.services.speech_service import medical_speech_engine, VERNACULAR_DICTIONARY

# Create isolated test app with the speech router
app = FastAPI(title="Geosmin Speech Pipeline Test")
app.include_router(speech_router)
client = TestClient(app)

def create_synthetic_wav(duration: float = 1.0, freq: float = 440.0) -> bytes:
    """Generate in-memory 16kHz mono PCM WAV bytes for testing."""
    sample_rate = 16000
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    audio_data = (np.sin(2 * np.pi * freq * t) * 0.1).astype(np.float32)

    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes((audio_data * 32767).astype(np.int16).tobytes())
    return wav_io.getvalue()


def test_speech_health_diagnostics():
    response = client.get("/api/speech/health")
    assert response.status_code == 200
    data = response.json()
    assert data["engine"] == "faster-whisper (CTranslate2)"
    assert "int8" in data["compute_type"].lower()
    assert data["vernacular_vocab_count"] >= 20
    assert "te" in data["supported_languages"]
    assert "hi" in data["supported_languages"]
    assert "en" in data["supported_languages"]


def test_vernacular_dictionary_structure():
    response = client.get("/api/speech/vernacular-dictionary")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 20
    assert "Telugu" in data["languages"]
    assert "Hindi" in data["languages"]
    assert "Indian English" in data["languages"]

    # Verify Telugu post-prandial mapping
    telugu_matches = [
        item for item in data["dictionary"]
        if item["language"] == "Telugu" and item["sig_code"] == "PC"
    ]
    assert len(telugu_matches) > 0
    assert "bhojanam taruvatha" in telugu_matches[0]["variants"]


def test_telugu_vernacular_sig_extraction():
    # Test Telugu code-switching: Paracetamol + bhojanam taruvatha + roju ki rendu sarlu
    input_text = "Paracetamol 650mg bhojanam taruvatha roju ki rendu sarlu"
    response = client.post("/api/speech/parse-sig", json={"text": input_text})
    assert response.status_code == 200
    sig = response.json()

    assert sig["drug_name"] == "Paracetamol"
    assert sig["dosage"] == "650mg"
    assert "PC" in sig["standard_sig_code"]
    assert "BID" in sig["standard_sig_code"]
    assert "post-prandial" in sig["sig_full"].lower()
    assert len(sig["vernacular_terms_detected"]) >= 2


def test_hindi_vernacular_sig_extraction():
    # Test Hindi code-switching: Pantoprazole + subah khali pet + din me ek baar
    input_text = "Pantoprazole 40mg subah khali pet din me ek baar"
    response = client.post("/api/speech/parse-sig", json={"text": input_text})
    assert response.status_code == 200
    sig = response.json()

    assert sig["drug_name"] == "Pantoprazole"
    assert sig["dosage"] == "40mg"
    assert "AC" in sig["standard_sig_code"]
    assert "QD" in sig["standard_sig_code"]
    assert "empty stomach" in sig["sig_full"].lower() or "pre-prandial" in sig["sig_full"].lower()


def test_colloquial_bp_tablet_mapping():
    # Test colloquial expression: "BP tablet subah khali pet"
    input_text = "BP tablet subah khali pet"
    response = client.post("/api/speech/parse-sig", json={"text": input_text})
    assert response.status_code == 200
    sig = response.json()

    assert "Antihypertensive" in sig["drug_name"]
    assert "AC" in sig["standard_sig_code"]


def test_mandulu_telugu_entity_mapping():
    # Specification: "mandulu bhojanam tharuvatha" -> "post-prandial oral administration"
    input_text = "mandulu bhojanam tharuvatha"
    response = client.post("/api/speech/parse-sig", json={"text": input_text})
    assert response.status_code == 200
    sig = response.json()

    assert "PO" in sig["standard_sig_code"]
    assert "PC" in sig["standard_sig_code"]
    assert any("post-prandial" in e["mapped_administration"] for e in sig["vernacular_terms_detected"])


def test_phonetic_drug_correction_suggestions():
    # Low-confidence misspelled drug token suggestions
    suggestions = medical_speech_engine._generate_drug_suggestions("Paracetam")
    assert any("Paracetamol" in s for s in suggestions)

    suggestions_met = medical_speech_engine._generate_drug_suggestions("Metform")
    assert any("Metformin" in s for s in suggestions_met)

    suggestions_ator = medical_speech_engine._generate_drug_suggestions("Atorva")
    assert any("Atorvastatin" in s for s in suggestions_ator)


def test_transcribe_endpoint_with_wav_upload():
    wav_bytes = create_synthetic_wav(duration=1.0)
    files = {"file": ("dictation.wav", wav_bytes, "audio/wav")}
    data = {"language": "en-US", "clinical_context": "Atorvastatin 20mg daily"}

    response = client.post("/api/speech/transcribe", files=files, data=data)
    assert response.status_code == 200
    body = response.json()

    assert body["success"] is True
    assert "text" in body
    assert "detected_language" in body
    assert "words" in body
    assert "clinical_sig" in body
    assert "processing_time_ms" in body
    assert body["processing_time_ms"] > 0
    assert "engine_metadata" in body
    assert body["engine_metadata"]["model_size"] == "small"


def test_transcribe_empty_file_rejected():
    files = {"file": ("empty.wav", b"", "audio/wav")}
    response = client.post("/api/speech/transcribe", files=files)
    assert response.status_code == 400
    assert "empty" in response.json()["detail"].lower()
