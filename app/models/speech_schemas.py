from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class WordConfidence(BaseModel):
    word: str
    start: float
    end: float
    probability: float
    low_confidence: bool = False
    suggestions: List[str] = Field(default_factory=list)

class VernacularEntity(BaseModel):
    raw_phrase: str
    language: str  # "Telugu", "Hindi", "Indian English"
    medical_meaning: str
    clinical_sig_code: str  # "PC", "AC", "BID", "TID", "QHS", "PRN", "PO"
    mapped_administration: str
    category: str = "timing"  # "timing", "frequency", "route", "form"

class StructuredClinicalSIG(BaseModel):
    drug_name: Optional[str] = None
    rxnorm_cui: Optional[str] = None
    dosage: Optional[str] = None
    cadence: Optional[str] = None
    frequency_sig: Optional[str] = None
    route: str = "Oral Tablet (PO)"
    sig_full: str
    standard_sig_code: str
    vernacular_terms_detected: List[VernacularEntity] = Field(default_factory=list)
    raw_transcript: str
    confidence_score: float

class TranscriptionResponse(BaseModel):
    success: bool = True
    text: str
    detected_language: str
    language_probability: float
    duration_seconds: float
    words: List[WordConfidence]
    average_confidence: float
    low_confidence_word_count: int
    clinical_sig: Optional[StructuredClinicalSIG] = None
    vernacular_entities: List[VernacularEntity] = Field(default_factory=list)
    processing_time_ms: float
    engine_metadata: Dict[str, Any] = Field(default_factory=dict)

class SigParseRequest(BaseModel):
    text: str
    language_hint: Optional[str] = "auto"
    target_patient_id: Optional[str] = None

class SpeechHealthResponse(BaseModel):
    status: str
    engine: str
    model_size: str
    device: str
    compute_type: str
    cpu_threads: int
    vernacular_vocab_count: int
    supported_languages: List[str]
