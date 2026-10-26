import os
import io
import time
import tempfile
import logging
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status
from fastapi.responses import JSONResponse

from app.models.speech_schemas import (
    TranscriptionResponse,
    StructuredClinicalSIG,
    SigParseRequest,
    SpeechHealthResponse,
    VernacularEntity
)
from app.services.speech_service import (
    medical_speech_engine,
    VERNACULAR_DICTIONARY,
    DRUG_REFERENCE_CATALOG
)

logger = logging.getLogger("geosmin.speech_router")

# Export both speech_router and router
router = APIRouter(tags=["Medical Speech Recognition & Vernacular Dictation"])
speech_router = router

@router.post("/api/speech/transcribe", response_model=TranscriptionResponse)
@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_medical_audio(
    file: UploadFile = File(..., description="Audio file in WebM, WAV, MP3, or OGG format"),
    language: Optional[str] = Form(None, description="Optional language hint: 'en-US', 'hi-IN', 'te-IN'"),
    clinical_context: Optional[str] = Form(None, description="Optional clinical context e.g. active patient diagnoses or drug history")
):
    """
    Multilingual Medical Dictation Endpoint:
    - Accepts multipart/form-data audio (WebM Opus 16kHz, WAV, MP3).
    - Runs faster-whisper (CTranslate2 INT8) on CPU/CUDA.
    - Applies clinical initial_prompt with Latin SIG codes and Indian vernacular terms.
    - Returns transcribed text, word-level timestamps, acoustic confidence scores,
      amber highlight tags for confidence < 0.80 with 1-click phonetic drug suggestions,
      and structured clinical SIG instructions.
    """
    if not file:
        raise HTTPException(status_code=400, detail="No audio file provided.")

    allowed_types = ["audio/webm", "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3", "audio/ogg", "application/octet-stream"]
    content_type = file.content_type or ""
    
    # Read uploaded bytes
    try:
        audio_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read audio stream: {str(e)}")

    if len(audio_bytes) < 64:
        raise HTTPException(status_code=400, detail="Audio file is empty or too short.")

    # Determine file extension
    ext = ".webm"
    if "wav" in content_type or file.filename.endswith(".wav"):
        ext = ".wav"
    elif "mp3" in content_type or "mpeg" in content_type or file.filename.endswith(".mp3"):
        ext = ".mp3"
    elif "ogg" in content_type or file.filename.endswith(".ogg"):
        ext = ".ogg"

    # Write temporarily to disk for CTranslate2 audio decoding
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            temp_path = tmp.name

        # Execute transcription through medical speech engine
        result: TranscriptionResponse = medical_speech_engine.transcribe_audio_file(
            audio_source=temp_path,
            language=language,
            clinical_context=clinical_context
        )
        return result

    except Exception as e:
        logger.error(f"Error during audio transcription: {e}", exc_info=True)
        # Resilient fallback: return clinical error structure rather than crashing
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Speech transcription pipeline error: {str(e)}"
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


@router.post("/api/speech/parse-sig", response_model=StructuredClinicalSIG)
@router.post("/parse-sig", response_model=StructuredClinicalSIG)
async def parse_clinical_sig_from_text(payload: SigParseRequest):
    """
    Convert text or code-switched phrases (Telugu, Hindi, Indian English)
    into structured clinical SIG instructions without requiring audio input.
    """
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text field cannot be empty.")

    entities = medical_speech_engine.extract_vernacular_entities(payload.text)
    sig = medical_speech_engine.parse_clinical_sig(payload.text, entities)
    return sig


@router.get("/api/speech/vernacular-dictionary")
@router.get("/vernacular-dictionary")
async def get_vernacular_dictionary():
    """
    Return curated pharmaceutical vernacular vocabulary for Telugu, Hindi,
    and Indian English with standardized clinical SIG mappings.
    """
    return {
        "count": len(VERNACULAR_DICTIONARY),
        "languages": ["Telugu", "Hindi", "Indian English"],
        "dictionary": VERNACULAR_DICTIONARY,
        "sample_code_switches": [
            {
                "input": "Paracetamol 650mg bhojanam taruvatha roju ki rendu sarlu",
                "language": "Telugu + English",
                "standard_sig": "Take Paracetamol 650mg orally post-prandial, twice daily.",
                "sig_code": "1 TAB PO PC BID"
            },
            {
                "input": "Pantoprazole 40mg subah khali pet din me ek baar",
                "language": "Hindi + English",
                "standard_sig": "Take Pantoprazole 40mg orally morning pre-prandial on empty stomach, once daily.",
                "sig_code": "1 TAB PO AC QD"
            },
            {
                "input": "BP tablet subah khali pet",
                "language": "Hindi + English",
                "standard_sig": "Administer blood pressure medication orally morning pre-prandial on empty stomach.",
                "sig_code": "1 TAB PO AC"
            },
            {
                "input": "mandulu bhojanam tharuvatha",
                "language": "Telugu",
                "standard_sig": "Oral medication post-prandial administration.",
                "sig_code": "PO PC"
            }
        ]
    }


@router.get("/api/speech/health", response_model=SpeechHealthResponse)
@router.get("/health")
async def speech_health():
    """Return health and performance diagnostics of faster-whisper CTranslate2 pipeline."""
    metrics = medical_speech_engine.get_health_metrics()
    return SpeechHealthResponse(
        status=metrics["status"],
        engine=metrics["engine"],
        model_size=metrics["model_size"],
        device=metrics["device"],
        compute_type=metrics["compute_type"],
        cpu_threads=metrics["cpu_threads"],
        vernacular_vocab_count=metrics["vernacular_vocab_count"],
        supported_languages=metrics["supported_languages"]
    )
