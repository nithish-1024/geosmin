import os
import re
import time
import io
import math
import logging
from typing import List, Dict, Any, Optional, Tuple

import ctranslate2
from faster_whisper import WhisperModel

from app.models.speech_schemas import (
    WordConfidence,
    VernacularEntity,
    StructuredClinicalSIG,
    TranscriptionResponse
)
from app.services.rxnorm_normalizer import rxnorm_normalizer

logger = logging.getLogger("geosmin.speech")
logger.setLevel(logging.INFO)

# --- VERNACULAR & CLINICAL KNOWLEDGE DICTIONARY ---

VERNACULAR_DICTIONARY: List[Dict[str, Any]] = [
    # --- TELUGU VERNACULAR PHRASES ---
    {
        "phrase": "bhojanam taruvatha",
        "variants": ["bhojanam taruvatha", "bhojanam tharuvatha", "tinna taruvatha", "tinna tharuvatha", "annam tharuvatha", "bhojanam tarwata"],
        "language": "Telugu",
        "meaning": "Post-prandial (After meals)",
        "sig_code": "PC",
        "administration": "post-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "bhojanam mundu",
        "variants": ["bhojanam mundu", "tinadaniki mundu", "tinataniki mundu", "annam mundu", "bhojananiki mundu"],
        "language": "Telugu",
        "meaning": "Pre-prandial (Before meals)",
        "sig_code": "AC",
        "administration": "pre-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "poduna",
        "variants": ["poduna", "podunne", "udayam", "podune"],
        "language": "Telugu",
        "meaning": "Morning (Omni Mane)",
        "sig_code": "OM",
        "administration": "morning dose",
        "category": "timing"
    },
    {
        "phrase": "madhyahnam",
        "variants": ["madhyahnam", "madhyanam", "madyanam"],
        "language": "Telugu",
        "meaning": "Midday / Afternoon",
        "sig_code": "NOON",
        "administration": "afternoon dose",
        "category": "timing"
    },
    {
        "phrase": "sayantram",
        "variants": ["sayantram", "sayanthram", "sayantramu"],
        "language": "Telugu",
        "meaning": "Evening (Vesp)",
        "sig_code": "VESP",
        "administration": "evening dose",
        "category": "timing"
    },
    {
        "phrase": "ratri",
        "variants": ["ratri", "rathri", "padukune mundu", "padukunetappudu"],
        "language": "Telugu",
        "meaning": "At bedtime (Quaque Hora Somni)",
        "sig_code": "QHS",
        "administration": "at bedtime",
        "category": "timing"
    },
    {
        "phrase": "roju ki rendu sarlu",
        "variants": ["roju ki rendu sarlu", "rojuku rendu sarlu", "rendu sarlu", "rendu pootla"],
        "language": "Telugu",
        "meaning": "Twice daily (Bis in die)",
        "sig_code": "BID",
        "administration": "twice daily",
        "category": "frequency"
    },
    {
        "phrase": "roju ki moodu sarlu",
        "variants": ["roju ki moodu sarlu", "rojuku moodu sarlu", "moodu sarlu", "moodu pootla"],
        "language": "Telugu",
        "meaning": "Three times daily (Ter in die)",
        "sig_code": "TID",
        "administration": "three times daily",
        "category": "frequency"
    },
    {
        "phrase": "roju ki okkasari",
        "variants": ["roju ki okkasari", "rojuku okasari", "okka sari", "oka sari", "roju ki okasari"],
        "language": "Telugu",
        "meaning": "Once daily (Quaque die)",
        "sig_code": "QD",
        "administration": "once daily",
        "category": "frequency"
    },
    {
        "phrase": "noppi unte",
        "variants": ["noppi unte", "avasaram unte", "badha unte", "noppi vachinappudu"],
        "language": "Telugu",
        "meaning": "As needed (Pro re nata)",
        "sig_code": "PRN",
        "administration": "as needed for acute pain",
        "category": "frequency"
    },
    {
        "phrase": "mandulu",
        "variants": ["mandulu", "gullilu", "tabletlu", "mathralu", "matralu"],
        "language": "Telugu",
        "meaning": "Oral tablets / capsules",
        "sig_code": "PO",
        "administration": "oral administration",
        "category": "route"
    },

    # --- HINDI VERNACULAR PHRASES ---
    {
        "phrase": "subah khali pet",
        "variants": ["subah khali pet", "khali pet", "khali pet subah"],
        "language": "Hindi",
        "meaning": "Pre-prandial / Morning empty stomach",
        "sig_code": "AC",
        "administration": "morning pre-prandial on empty stomach",
        "category": "timing"
    },
    {
        "phrase": "khana khane ke baad",
        "variants": ["khana khane ke baad", "khane ke baad", "khana kha kar", "khana khane k baad"],
        "language": "Hindi",
        "meaning": "Post-prandial (After meals)",
        "sig_code": "PC",
        "administration": "post-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "khana khane se pehle",
        "variants": ["khana khane se pehle", "khane se pehle", "khana se pehle"],
        "language": "Hindi",
        "meaning": "Pre-prandial (Before meals)",
        "sig_code": "AC",
        "administration": "pre-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "subah aur sham",
        "variants": ["subah aur sham", "subah sham", "subah aur shaam", "subah sham ko"],
        "language": "Hindi",
        "meaning": "Morning and evening (Twice daily)",
        "sig_code": "BID",
        "administration": "twice daily (morning and evening)",
        "category": "frequency"
    },
    {
        "phrase": "din me do baar",
        "variants": ["din me do baar", "din mein do baar", "do baar", "do bar"],
        "language": "Hindi",
        "meaning": "Twice daily (Bis in die)",
        "sig_code": "BID",
        "administration": "twice daily",
        "category": "frequency"
    },
    {
        "phrase": "din me teen baar",
        "variants": ["din me teen baar", "din mein teen baar", "teen baar", "teen bar"],
        "language": "Hindi",
        "meaning": "Three times daily (Ter in die)",
        "sig_code": "TID",
        "administration": "three times daily",
        "category": "frequency"
    },
    {
        "phrase": "din me ek baar",
        "variants": ["din me ek baar", "din mein ek baar", "ek baar", "ek bar"],
        "language": "Hindi",
        "meaning": "Once daily (Quaque die)",
        "sig_code": "QD",
        "administration": "once daily",
        "category": "frequency"
    },
    {
        "phrase": "raat ko sone se pehle",
        "variants": ["raat ko sone se pehle", "sone se pehle", "raat ko", "sone ke waqt"],
        "language": "Hindi",
        "meaning": "At bedtime (Quaque Hora Somni)",
        "sig_code": "QHS",
        "administration": "at bedtime",
        "category": "timing"
    },
    {
        "phrase": "dard ke liye",
        "variants": ["dard ke liye", "jab dard ho", "zarurat padne par", "jab jarurat ho"],
        "language": "Hindi",
        "meaning": "As needed (Pro re nata)",
        "sig_code": "PRN",
        "administration": "as needed for acute pain",
        "category": "frequency"
    },
    {
        "phrase": "dawa",
        "variants": ["dawa", "goli", "davai", "tablets"],
        "language": "Hindi",
        "meaning": "Oral tablets / medications",
        "sig_code": "PO",
        "administration": "oral administration",
        "category": "route"
    },

    # --- INDIAN ENGLISH PHRASES ---
    {
        "phrase": "after food",
        "variants": ["after food", "after meals", "post food", "post meals"],
        "language": "Indian English",
        "meaning": "Post-prandial (After meals)",
        "sig_code": "PC",
        "administration": "post-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "before food",
        "variants": ["before food", "before meals", "empty stomach"],
        "language": "Indian English",
        "meaning": "Pre-prandial (Before meals)",
        "sig_code": "AC",
        "administration": "pre-prandial oral administration",
        "category": "timing"
    },
    {
        "phrase": "twice daily",
        "variants": ["twice daily", "twice a day", "two times a day", "bid"],
        "language": "Indian English",
        "meaning": "Twice daily (Bis in die)",
        "sig_code": "BID",
        "administration": "twice daily",
        "category": "frequency"
    },
    {
        "phrase": "thrice daily",
        "variants": ["thrice daily", "thrice a day", "three times a day", "tid"],
        "language": "Indian English",
        "meaning": "Three times daily (Ter in die)",
        "sig_code": "TID",
        "administration": "three times daily",
        "category": "frequency"
    },
    {
        "phrase": "once daily",
        "variants": ["once daily", "once a day", "daily once", "od", "qd"],
        "language": "Indian English",
        "meaning": "Once daily",
        "sig_code": "QD",
        "administration": "once daily",
        "category": "frequency"
    },
    {
        "phrase": "at bedtime",
        "variants": ["at bedtime", "night time", "bedtime", "before sleep", "qhs"],
        "language": "Indian English",
        "meaning": "At bedtime (Quaque Hora Somni)",
        "sig_code": "QHS",
        "administration": "at bedtime",
        "category": "timing"
    },
    {
        "phrase": "as needed",
        "variants": ["as needed", "when required", "sos", "prn", "for pain"],
        "language": "Indian English",
        "meaning": "As needed (Pro re nata)",
        "sig_code": "PRN",
        "administration": "as needed for symptoms",
        "category": "frequency"
    }
]

# Clinical Pharmaceutical Catalog for phonetic correction and drug recognition
DRUG_REFERENCE_CATALOG = [
    {"name": "Paracetamol", "dosage": "650mg", "cui": "161", "forms": ["tablet", "syrup"]},
    {"name": "Metformin", "dosage": "500mg", "cui": "6809", "forms": ["tablet", "extended release"]},
    {"name": "Pantoprazole", "dosage": "40mg", "cui": "40790", "forms": ["tablet", "capsule"]},
    {"name": "Omeprazole", "dosage": "20mg", "cui": "7646", "forms": ["delayed release capsule"]},
    {"name": "Atorvastatin", "dosage": "20mg", "cui": "83367", "forms": ["film coated tablet"]},
    {"name": "Amlodipine", "dosage": "5mg", "cui": "17767", "forms": ["tablet"]},
    {"name": "Telmisartan", "dosage": "40mg", "cui": "38454", "forms": ["tablet"]},
    {"name": "Metoprolol", "dosage": "25mg", "cui": "6918", "forms": ["succinate", "tartrate"]},
    {"name": "Warfarin", "dosage": "5mg", "cui": "11289", "forms": ["tablet"]},
    {"name": "Ibuprofen", "dosage": "400mg", "cui": "5640", "forms": ["tablet", "capsule"]},
    {"name": "Celecoxib", "dosage": "100mg", "cui": "140587", "forms": ["capsule"]},
    {"name": "Lisinopril", "dosage": "10mg", "cui": "29046", "forms": ["tablet"]},
    {"name": "Azithromycin", "dosage": "500mg", "cui": "18631", "forms": ["tablet"]},
    {"name": "Amoxicillin", "dosage": "500mg", "cui": "723", "forms": ["capsule"]},
    {"name": "Ciprofloxacin", "dosage": "500mg", "cui": "2551", "forms": ["tablet"]},
    {"name": "Rosuvastatin", "dosage": "10mg", "cui": "301542", "forms": ["tablet"]},
    {"name": "Levothyroxine", "dosage": "50mcg", "cui": "10582", "forms": ["tablet"]},
    {"name": "Clopidogrel", "dosage": "75mg", "cui": "32968", "forms": ["tablet"]},
    {"name": "Losartan", "dosage": "50mg", "cui": "5224", "forms": ["tablet"]}
]

def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two normalized tokens."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


class MedicalSpeechEngine:
    """
    Multilingual Medical Dictation Pipeline using faster-whisper (CTranslate2 INT8)
    with Indian vernacular adaptation (Telugu, Hindi, Indian English).
    """

    CLINICAL_INITIAL_PROMPT = (
        "Clinical dictation notes: Patient taking Paracetamol 650mg bhojanam taruvatha, "
        "Metformin 500mg subah khali pet bid, Pantoprazole 40mg prn. "
        "Rx: Amlodipine 5mg od, Telmisartan 40mg, Atorvastatin 20mg, Warfarin 5mg, "
        "Ibuprofen 400mg, Omeprazole 20mg, Lisinopril 10mg. "
        "Cadence: bid, tid, qid, prn, qhs, ac, pc. Units: mg, mcg, ml, tab. "
        "Regional phrasings: bhojanam mundu, mandulu, khane ke baad, din me do baar, raat ko sone se pehle."
    )

    def __init__(self, model_size: str = "small"):
        self.model_size = os.getenv("WHISPER_MODEL_SIZE", model_size)
        self.model: Optional[WhisperModel] = None
        self.device = "cpu"
        self.compute_type = "int8"
        self.cpu_threads = min(os.cpu_count() or 4, 8)
        self.initialized = False
        self._init_error: Optional[str] = None

    def initialize(self):
        """Lazy load and initialize faster-whisper with INT8 quantization."""
        if self.initialized and self.model is not None:
            return

        # Attempt CUDA INT8 if available and cuBLAS is loadable, else fallback to CPU INT8
        cuda_count = ctranslate2.get_cuda_device_count()
        if cuda_count > 0:
            try:
                logger.info("Attempting faster-whisper initialization on CUDA (int8_float16)...")
                self.model = WhisperModel(
                    self.model_size,
                    device="cuda",
                    compute_type="int8_float16"
                )
                self.device = "cuda"
                self.compute_type = "int8_float16"
                self.initialized = True
                logger.info("faster-whisper successfully initialized on CUDA INT8.")
                return
            except Exception as e:
                logger.warning(f"CUDA initialization failed ({e}). Falling back to CPU INT8...")

        # CPU Mode with INT8 optimization
        try:
            logger.info(f"Initializing faster-whisper on CPU with INT8 and {self.cpu_threads} threads...")
            self.model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.cpu_threads
            )
            self.device = "cpu"
            self.compute_type = "int8"
            self.initialized = True
            logger.info(f"faster-whisper model '{self.model_size}' loaded successfully on CPU INT8.")
        except Exception as e:
            self._init_error = str(e)
            logger.error(f"Failed to load WhisperModel: {e}")
            raise

    def get_health_metrics(self) -> Dict[str, Any]:
        """Return engine operational status and performance metrics."""
        return {
            "status": "healthy" if self.initialized else "ready_to_load",
            "engine": "faster-whisper (CTranslate2)",
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "cpu_threads": self.cpu_threads,
            "vernacular_vocab_count": len(VERNACULAR_DICTIONARY),
            "supported_languages": ["en", "hi", "te"],
            "initial_prompt_length": len(self.CLINICAL_INITIAL_PROMPT),
            "init_error": self._init_error
        }

    def transcribe_audio_file(
        self,
        audio_source: Any,
        language: Optional[str] = None,
        clinical_context: Optional[str] = None
    ) -> TranscriptionResponse:
        """
        Transcribe audio input (file path, file-like BytesIO, or raw audio bytes)
        using faster-whisper with clinical prompt steering and vernacular extraction.
        """
        t0 = time.time()
        self.initialize()

        # Build prompt: incorporate general clinical prompt + optional specific context
        prompt = self.CLINICAL_INITIAL_PROMPT
        if clinical_context:
            prompt = f"{prompt} Patient context: {clinical_context[:100]}."

        # Map frontend language code (e.g., 'te-IN' -> 'te', 'hi-IN' -> 'hi', 'en-US' -> 'en')
        whisper_lang = None
        if language:
            code = language.split("-")[0].lower()
            if code in ["en", "hi", "te"]:
                whisper_lang = code

        # Transcribe with INT8 CPU/CUDA performance tuning:
        # - beam_size=1 (greedy search for 2x faster dictation inference)
        # - vad_filter=True to strip silence
        # - condition_on_previous_text=False to prevent hallucination loops in isolated dictations
        # - word_timestamps=True for per-token acoustic confidence scoring
        try:
            segments, info = self.model.transcribe(
                audio_source,
                language=whisper_lang,
                initial_prompt=prompt,
                beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
                condition_on_previous_text=False,
                word_timestamps=True
            )
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise

        full_text_parts = []
        words_out: List[WordConfidence] = []
        low_confidence_count = 0

        for segment in segments:
            full_text_parts.append(segment.text.strip())
            if segment.words:
                for w in segment.words:
                    clean_word = w.word.strip()
                    prob = float(w.probability)
                    is_low = prob < 0.80
                    if is_low:
                        low_confidence_count += 1
                    
                    suggestions = self._generate_drug_suggestions(clean_word) if is_low else []

                    words_out.append(WordConfidence(
                        word=clean_word,
                        start=round(float(w.start), 2),
                        end=round(float(w.end), 2),
                        probability=round(prob, 3),
                        low_confidence=is_low,
                        suggestions=suggestions
                    ))

        transcript = " ".join(full_text_parts).strip()
        avg_confidence = (
            sum(w.probability for w in words_out) / len(words_out)
            if words_out else (info.language_probability if hasattr(info, 'language_probability') else 0.90)
        )

        # Detect vernacular entities and build structured clinical SIG
        vernacular_entities = self.extract_vernacular_entities(transcript)
        clinical_sig = self.parse_clinical_sig(transcript, vernacular_entities, avg_confidence)

        duration = getattr(info, 'duration', 0.0)
        proc_time = round((time.time() - t0) * 1000, 2)

        return TranscriptionResponse(
            success=True,
            text=transcript,
            detected_language=info.language,
            language_probability=round(float(info.language_probability), 3),
            duration_seconds=round(float(duration), 2),
            words=words_out,
            average_confidence=round(float(avg_confidence), 3),
            low_confidence_word_count=low_confidence_count,
            clinical_sig=clinical_sig,
            vernacular_entities=vernacular_entities,
            processing_time_ms=proc_time,
            engine_metadata={
                "device": self.device,
                "compute_type": self.compute_type,
                "model_size": self.model_size
            }
        )

    def extract_vernacular_entities(self, text: str) -> List[VernacularEntity]:
        """
        Scan text for Indian vernacular phrases (Telugu, Hindi, Indian English)
        and map them to standardized clinical terminology.
        """
        detected: List[VernacularEntity] = []
        text_lower = text.lower()

        for entry in VERNACULAR_DICTIONARY:
            for variant in entry["variants"]:
                # Use word boundary or substring match
                pattern = r'\b' + re.escape(variant) + r'\b'
                if re.search(pattern, text_lower):
                    entity = VernacularEntity(
                        raw_phrase=variant,
                        language=entry["language"],
                        medical_meaning=entry["meaning"],
                        clinical_sig_code=entry["sig_code"],
                        mapped_administration=entry["administration"],
                        category=entry["category"]
                    )
                    # Deduplicate by phrase
                    if not any(d.raw_phrase == variant for d in detected):
                        detected.append(entity)
                    break
        return detected

    def parse_clinical_sig(
        self,
        transcript: str,
        vernacular_entities: Optional[List[VernacularEntity]] = None,
        confidence: float = 0.95
    ) -> StructuredClinicalSIG:
        """
        Seamlessly converts code-switched or clinical audio transcripts into
        structured clinical SIG instructions (e.g., '1 TAB PO PC BID').
        """
        if vernacular_entities is None:
            vernacular_entities = self.extract_vernacular_entities(transcript)

        t_lower = transcript.lower()

        # 1. Identify Candidate Drug Name
        detected_drug: Optional[str] = None
        detected_cui: Optional[str] = None

        # Check curated drug reference catalog first
        for drug in DRUG_REFERENCE_CATALOG:
            if re.search(r'\b' + re.escape(drug["name"].lower()) + r'\b', t_lower):
                detected_drug = drug["name"]
                detected_cui = drug["cui"]
                break

        # Check common clinical colloquialisms (e.g. "BP tablet", "sugar tablet")
        if not detected_drug:
            if re.search(r'\b(bp|blood pressure)\s*(?:tablet|tablets|medicine|dawa|mandulu|goli)?\b', t_lower):
                detected_drug = "Antihypertensive Therapy (Amlodipine / Telmisartan)"
                detected_cui = "17767"
            elif re.search(r'\b(sugar|diabetes)\s*(?:tablet|tablets|medicine|dawa|mandulu|goli)?\b', t_lower):
                detected_drug = "Oral Antidiabetic Therapy (Metformin)"
                detected_cui = "6809"
            elif re.search(r'\b(gas|acidity|acid)\s*(?:tablet|tablets|medicine|dawa|mandulu|goli)?\b', t_lower):
                detected_drug = "Gastroprotective Therapy (Pantoprazole / Omeprazole)"
                detected_cui = "40790"
            elif re.search(r'\b(pain|painkiller|dard|noppi)\s*(?:tablet|tablets|medicine|dawa|mandulu|goli)?\b', t_lower):
                detected_drug = "Analgesic Therapy (Paracetamol 650mg)"
                detected_cui = "161"

        # Check individual words against rxnorm normalizer safely
        if not detected_drug:
            words = re.findall(r'\b[a-zA-Z]{4,}\b', transcript)
            for w in words:
                try:
                    norm = rxnorm_normalizer.normalize(w)
                    if norm and norm.get("status") in ["resolved", "disambiguation_required"] and norm.get("rxnorm_cui"):
                        detected_drug = norm.get("generic_name") or norm.get("concept_name", w.title())
                        detected_cui = norm.get("rxnorm_cui")
                        break
                except Exception:
                    continue

        # 2. Extract Dosage (e.g. 650mg, 500 mg, 5mg, 40 mg, 10ml)
        dosage_match = re.search(r'\b(\d+(?:\.\d+)?\s*(?:mg|mcg|ml|g|iu|tablets?|capsules?))\b', transcript, re.IGNORECASE)
        detected_dosage = dosage_match.group(1).strip() if dosage_match else None

        # If no dosage in transcript but drug detected, default from reference catalog
        if not detected_dosage and detected_drug:
            for drug in DRUG_REFERENCE_CATALOG:
                if drug["name"].lower() == detected_drug.lower():
                    detected_dosage = drug["dosage"]
                    break

        # 3. Extract Timing, Cadence & SIG Codes
        timing_entities = [e for e in vernacular_entities if e.category == "timing"]
        freq_entities = [e for e in vernacular_entities if e.category == "frequency"]

        # Priority resolution for SIG codes
        sig_timing = timing_entities[0].clinical_sig_code if timing_entities else None
        sig_freq = freq_entities[0].clinical_sig_code if freq_entities else None

        # Build Standard SIG Code (e.g. "1 TAB PO PC BID")
        sig_code_parts = ["1 TAB", "PO"]
        if sig_timing and sig_timing != "PO":
            sig_code_parts.append(sig_timing)
        if sig_freq and sig_freq not in sig_code_parts:
            sig_code_parts.append(sig_freq)
        
        standard_sig = " ".join(sig_code_parts)

        # Build Human-Readable Clinical SIG string
        timing_desc = timing_entities[0].mapped_administration if timing_entities else "oral administration"
        freq_desc = freq_entities[0].mapped_administration if freq_entities else "as directed"

        if detected_drug and detected_dosage:
            sig_full = f"Take {detected_drug} {detected_dosage} orally {timing_desc}, {freq_desc}."
        elif detected_drug:
            sig_full = f"Take {detected_drug} orally {timing_desc}, {freq_desc}."
        else:
            sig_full = f"Administer orally {timing_desc}, {freq_desc}."

        cadence_display = f"{timing_desc.capitalize()} • {freq_desc.capitalize()}"

        return StructuredClinicalSIG(
            drug_name=detected_drug or "Clinically Specified Medication",
            rxnorm_cui=detected_cui or "RxNorm-Pending",
            dosage=detected_dosage or "1 tablet",
            cadence=cadence_display,
            frequency_sig=sig_freq or sig_timing or "QD",
            route="Oral Tablet (PO)",
            sig_full=sig_full,
            standard_sig_code=standard_sig,
            vernacular_terms_detected=vernacular_entities,
            raw_transcript=transcript,
            confidence_score=round(confidence, 3)
        )

    def _generate_drug_suggestions(self, word: str) -> List[str]:
        """
        Generate 1-click phonetic and edit-distance drug suggestions
        for low confidence tokens (< 0.80).
        """
        w_clean = re.sub(r'[^a-zA-Z]', '', word).lower()
        if len(w_clean) < 3:
            return []

        candidates = []
        for drug in DRUG_REFERENCE_CATALOG:
            d_name = drug["name"]
            dist = levenshtein_distance(w_clean, d_name.lower()[:len(w_clean) + 2])
            # Normalize distance
            if dist <= 3 or w_clean in d_name.lower():
                candidates.append((dist, f"{d_name} {drug['dosage']}"))

        candidates.sort(key=lambda x: x[0])
        return [c[1] for c in candidates[:3]]


# Global Singleton Instance for cleanly exported service
medical_speech_engine = MedicalSpeechEngine()
