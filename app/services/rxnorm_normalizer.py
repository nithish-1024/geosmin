"""
app/services/rxnorm_normalizer.py
Clinical Pharmacoinformatics RxNorm/openFDA Normalization Engine
VJ Hackathon 2026 • Healthcare Problem Statement 4 (Geosmin)

Role: Clinical Pharmacoinformatics Engineer (data-garden-bear & evals-watch-owl)
Features:
1. NLM RxNav REST API approximateTerm query with thread-safe local SQLite fallback.
2. Combined Trigram + Normalized Levenshtein similarity metric with phonetic calibration.
3. FDA/ISMP Look-Alike Sound-Alike (LASA) phonetic conflict detector (e.g. Celebrex vs Celexa).
4. Strict cutoffs:
   - Similarity < 0.70: Rejects with 422 Unprocessable Entity.
   - 0.70 <= Similarity < 0.88: Returns ranked disambiguation candidate options.
   - Similarity >= 0.88: Returns canonical RxCUI, generic name, brand aliases, and ATC codes.
"""

import json
import os
import re
import sqlite3
import threading
import time
from typing import Dict, Any, List, Optional, Tuple

import httpx
from fastapi import HTTPException, APIRouter

# ==============================================================================
# 1. PHONETIC ALGORITHMS & FDA/ISMP LASA DETECTOR
# ==============================================================================

def compute_soundex(name: str) -> str:
    """Computes standard American Soundex phonetic representation."""
    cleaned = re.sub(r"[^a-z]", "", name.lower())
    if not cleaned:
        return ""
    first = cleaned[0].upper()
    mapping = {
        "b": "1", "f": "1", "p": "1", "v": "1",
        "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
        "d": "3", "t": "3",
        "l": "4",
        "m": "5", "n": "5",
        "r": "6"
    }
    encoded = [first]
    prev = mapping.get(cleaned[0], "")
    for ch in cleaned[1:]:
        code = mapping.get(ch, "")
        if code and code != prev:
            encoded.append(code)
        prev = code
    res = "".join(encoded)
    return (res + "0000")[:4]

def compute_metaphone(name: str) -> str:
    """
    Computes pharmaceutical English metaphone encoding for drug names.
    Normalizes transliterations like 'ph' -> 'f', 'ch'/'ck' -> 'k', 'x' -> 'ks'.
    """
    s = name.lower()
    s = re.sub(r"[^a-z]", "", s)
    if not s:
        return ""
    # Initial silent letters
    if s.startswith(("pn", "kn", "gn", "ps", "wr")):
        s = s[1:]
    # Normalizations
    s = s.replace("ph", "f")
    s = s.replace("th", "t")
    s = s.replace("ck", "k")
    s = s.replace("ch", "k")
    s = re.sub(r"c(?=[eiy])", "s", s)
    s = s.replace("c", "k")
    s = s.replace("x", "ks")
    # Collapse double consonants
    s = re.sub(r"(.)\1+", r"\1", s)
    s = s.replace("kss", "ks")
    if not s:
        return ""
    # Retain first letter, remove vowels from the rest
    first = s[0].upper()
    rest = re.sub(r"[aeiouy]", "", s[1:]).upper()
    rest = re.sub(r"(.)\1+", r"\1", rest)
    return first + rest

# Curated FDA/ISMP Look-Alike Sound-Alike (LASA) Pairs with Tall Man Lettering
LASA_PAIRS = [
    {
        "pair": ("celebrex", "celexa"),
        "tall_man": ("CELEbrex", "ceLEXA"),
        "generics": ("celecoxib", "citalopram"),
        "classes": ("Selective COX-2 Inhibitor NSAID", "Selective Serotonin Reuptake Inhibitor (SSRI)"),
        "clinical_alert": "CRITICAL LASA HAZARD: Confusing CELEbrex (celecoxib) with ceLEXA (citalopram) risks major GI hemorrhage or unmonitored psychotropic antidepressant exposure."
    },
    {
        "pair": ("zantac", "xanax"),
        "tall_man": ("ZANtac", "XANax"),
        "generics": ("ranitidine", "alprazolam"),
        "classes": ("Histamine H2-Receptor Antagonist", "High-Potency Benzodiazepine Anxiolytic"),
        "clinical_alert": "CRITICAL LASA HAZARD: Confusing ZANtac (ranitidine) with XANax (alprazolam) risks severe CNS depression, respiratory sedation, and ataxia."
    },
    {
        "pair": ("clonazepam", "lorazepam"),
        "tall_man": ("cloNAZEpam", "lorAZEpam"),
        "generics": ("clonazepam", "lorazepam"),
        "classes": ("Long-Acting Benzodiazepine Anticonvulsant", "Intermediate-Acting Benzodiazepine"),
        "clinical_alert": "LASA ALERT: Differing elimination half-lives (clonazepam 30-40h vs lorazepam 10-20h) can lead to unexpected drug accumulation and toxicity."
    },
    {
        "pair": ("adderall", "inderal"),
        "tall_man": ("ADDERall", "INDeral"),
        "generics": ("amphetamine / dextroamphetamine", "propranolol"),
        "classes": ("CNS Stimulant", "Nonselective Beta-Adrenergic Blocker"),
        "clinical_alert": "CRITICAL LASA HAZARD: Confusing CNS stimulant ADDERall with beta-blocker INDeral risks catastrophic cardiovascular instability."
    },
    {
        "pair": ("hydralazine", "hydroxyzine"),
        "tall_man": ("hydrALAzine", "hydrOXYzine"),
        "generics": ("hydralazine", "hydroxyzine"),
        "classes": ("Arteriolar Vasodilator Antihypertensive", "First-Generation H1 Antihistamine"),
        "clinical_alert": "CRITICAL LASA HAZARD: Administering hydrALAzine instead of hydrOXYzine can trigger profound refractory hypotension and reflexive tachycardia."
    },
    {
        "pair": ("prednisone", "prednisolone"),
        "tall_man": ("predniSONE", "prednisoLONE"),
        "generics": ("prednisone", "prednisolone"),
        "classes": ("Systemic Glucocorticoid (Prodrug)", "Active Systemic Glucocorticoid"),
        "clinical_alert": "LASA ALERT: PredniSONE requires hepatic 11-beta-HSD conversion; prednisoLONE is active metabolite. Crucial in severe hepatic impairment."
    },
    {
        "pair": ("vinblastine", "vincristine"),
        "tall_man": ("vinBLAStine", "vinCRIStine"),
        "generics": ("vinblastine", "vincristine"),
        "classes": ("Vinca Alkaloid Antineoplastic", "Vinca Alkaloid Antineoplastic"),
        "clinical_alert": "FATAL LASA HAZARD: Overdosing vincristine due to vinblastine substitution induces fatal neurotoxicity and peripheral neuropathy."
    },
    {
        "pair": ("lamictal", "lamisil"),
        "tall_man": ("laMICtal", "laMIZil"),
        "generics": ("lamotrigine", "terbinafine"),
        "classes": ("Anticonvulsant / Mood Stabilizer", "Allylamine Antifungal"),
        "clinical_alert": "LASA ALERT: Rapid titration of laMICtal (lamotrigine) risks life-threatening Stevens-Johnson Syndrome (SJS) and Toxic Epidermal Necrolysis."
    },
    {
        "pair": ("prilosec", "prozac"),
        "tall_man": ("PRILosec", "PROzac"),
        "generics": ("omeprazole", "fluoxetine"),
        "classes": ("Proton Pump Inhibitor (PPI)", "SSRI Antidepressant"),
        "clinical_alert": "LASA ALERT: Inadvertent administration of PROzac instead of PRILosec exposes patient to neuropsychiatric alterations and CYP2D6 inhibition."
    },
    {
        "pair": ("seroquel", "serzone"),
        "tall_man": ("SEROquel", "SERzone"),
        "generics": ("quetiapine", "nefazodone"),
        "classes": ("Atypical Antipsychotic", "Antidepressant"),
        "clinical_alert": "LASA ALERT: Inadvertent substitution risks extrapyramidal symptoms, QT prolongation, or hepatotoxicity."
    }
]

# ==============================================================================
# 2. STRING DISTANCE & PHONETIC SIMILARITY METRICS
# ==============================================================================

def compute_trigrams(s: str) -> set[str]:
    """Extracts padded character trigrams from string."""
    cleaned = re.sub(r"[^a-z0-9]", "", s.lower())
    if not cleaned:
        return set()
    padded = f"$${cleaned}$$"
    return set(padded[i:i + 3] for i in range(len(padded) - 2))

def trigram_similarity(s1: str, s2: str) -> float:
    """Calculates Sørensen–Dice coefficient on character trigrams."""
    t1 = compute_trigrams(s1)
    t2 = compute_trigrams(s2)
    if not t1 and not t2:
        return 1.0
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    return (2.0 * intersection) / (len(t1) + len(t2))

def levenshtein_distance(s1: str, s2: str) -> int:
    """Computes exact edit distance between two strings."""
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,      # deletion
                dp[i][j - 1] + 1,      # insertion
                dp[i - 1][j - 1] + cost # substitution
            )
    return dp[m][n]

def normalized_levenshtein_similarity(s1: str, s2: str) -> float:
    """Calculates length-normalized Levenshtein similarity [0.0, 1.0]."""
    c1 = re.sub(r"[^a-z0-9]", "", s1.lower())
    c2 = re.sub(r"[^a-z0-9]", "", s2.lower())
    if c1 == c2:
        return 1.0
    max_len = max(len(c1), len(c2))
    if max_len == 0:
        return 1.0
    dist = levenshtein_distance(c1, c2)
    return max(0.0, 1.0 - (dist / max_len))

def phonetic_normalize(s: str) -> str:
    """Pharmacological digraph normalization for sound-alike comparison."""
    norm = s.lower()
    norm = norm.replace("ph", "f").replace("th", "t").replace("ch", "k").replace("x", "ks")
    norm = re.sub(r"(.)\1+", r"\1", norm)
    return norm

def combined_similarity(query: str, target: str) -> float:
    """
    Combined Trigram + Normalized Levenshtein similarity metric enriched with
    clinical phonetic alignment (Metaphone/Soundex) to catch sound-alike typos.
    """
    q_clean = re.sub(r"[^a-z0-9]", "", query.lower())
    t_clean = re.sub(r"[^a-z0-9]", "", target.lower())

    if not q_clean or not t_clean:
        return 0.0

    if q_clean == t_clean:
        return 1.0

    # Strip clinical dosage and formulation tokens (e.g. 'lisinopril 10mg' -> 'lisinopril')
    q_nodose = re.sub(r"\b\d+\s*(mg|mcg|g|ml)?\b|\b(oral|tablet|tab|capsule|cap|film|coated)\b", "", query.lower()).strip()
    q_nodose_clean = re.sub(r"[^a-z0-9]", "", q_nodose)
    if q_nodose_clean and q_nodose_clean == t_clean:
        return 0.98

    # Token-level matching: e.g. 'metoprolol tart' vs 'metoprolol tartrate'
    q_words = [w for w in re.split(r"[^a-z0-9]+", query.lower().strip()) if w]
    t_words = [w for w in re.split(r"[^a-z0-9]+", target.lower().strip()) if w]
    if len(q_words) >= 2 and len(t_words) >= 2:
        if q_words[0] == t_words[0] and len(q_words[0]) >= 6 and t_words[1].startswith(q_words[1]):
            return 0.95

    # Substring / Prefix matching for truncated clinician entry
    # (Incomplete queries should trigger disambiguation, not instant resolution)
    if q_clean in t_clean or t_clean in q_clean:
        min_l, max_l = min(len(q_clean), len(t_clean)), max(len(q_clean), len(t_clean))
        if min_l >= 4:
            # Prefix ratio: e.g. 5/6 = 0.833 for 'celex' in 'celexa'
            prefix_ratio = min_l / max_l
            t_sim = trigram_similarity(q_clean, t_clean)
            sub_score = 0.5 * prefix_ratio + 0.5 * t_sim
            # If query is identical except for formulation/strength words, allow high score
            if min_l / max_l >= 0.90:
                return min(1.0, round(sub_score + 0.05, 4))
            return min(0.86, round(sub_score, 4))

    dist = levenshtein_distance(q_clean, t_clean)
    max_len = max(len(q_clean), len(t_clean))

    sx_match = compute_soundex(q_clean) == compute_soundex(t_clean)
    mp_match = compute_metaphone(q_clean) == compute_metaphone(t_clean)

    # Phonetic misspelling calibration for single or double typos on clinical drug names
    if dist <= 1 and max_len >= 6:
        base_lexical = 0.89
    elif dist <= 3 and max_len >= 10 and sx_match and mp_match:
        base_lexical = 0.89
    else:
        t_lex = trigram_similarity(q_clean, t_clean)
        l_lex = normalized_levenshtein_similarity(q_clean, t_clean)
        base_lexical = 0.5 * t_lex + 0.5 * l_lex

    # Phonetic normalized metric
    qp = phonetic_normalize(q_clean)
    tp = phonetic_normalize(t_clean)
    t_phon = trigram_similarity(qp, tp)
    l_phon = normalized_levenshtein_similarity(qp, tp)
    base_phonetic = 0.5 * t_phon + 0.5 * l_phon

    # Phonetic agreement bonus
    sx_match = compute_soundex(q_clean) == compute_soundex(t_clean)
    mp_match = compute_metaphone(q_clean) == compute_metaphone(t_clean)

    phonetic_bonus = 0.0
    if sx_match and mp_match:
        phonetic_bonus = 0.10
    elif sx_match or mp_match:
        phonetic_bonus = 0.05

    score = max(base_lexical, base_phonetic) + phonetic_bonus
    return min(1.0, round(score, 4))

# ==============================================================================
# 3. CURATED CLINICAL FORMULARY & SQLITE CACHE ENGINE
# ==============================================================================

CANONICAL_FORMULARY: Dict[str, Dict[str, Any]] = {
    "celecoxib": {
        "concept_name": "Celecoxib 100 MG Oral Capsule",
        "generic_name": "Celecoxib",
        "rxnorm_cui": "140587",
        "brand_aliases": ["Celebrex"],
        "atc_code": "M01AH01",
        "therapeutic_class": "Selective COX-2 Inhibitor NSAID",
        "dietary_guideline": "Administer with food or milk if gastrointestinal discomfort occurs. Avoid concomitant alcohol.",
        "verification_summary": "1 capsule daily • Selective COX-2 inhibitor NSAID [Oral Route] [Daily Schedule] [Anti-inflammatory]",
        "lasa_warning": "FDA Tall Man Alert: CELEbrex (celecoxib) can be confused with ceLEXA (citalopram). Confirm indication before dispensing."
    },
    "citalopram": {
        "concept_name": "Citalopram 20 MG Oral Tablet",
        "generic_name": "Citalopram",
        "rxnorm_cui": "2556",
        "brand_aliases": ["Celexa"],
        "atc_code": "N06AB04",
        "therapeutic_class": "Selective Serotonin Reuptake Inhibitor (SSRI)",
        "dietary_guideline": "May be taken with or without food. Avoid abrupt cessation; monitor QT interval at doses > 20 mg/day in elderly.",
        "verification_summary": "20 mg daily • SSRI Antidepressant [Oral Route] [QT Monitored]",
        "lasa_warning": "FDA Tall Man Alert: ceLEXA (citalopram) can be confused with CELEbrex (celecoxib). Confirm psychotropic vs anti-inflammatory indication."
    },
    "warfarin": {
        "concept_name": "Warfarin Sodium 5 MG Oral Tablet",
        "generic_name": "Warfarin Sodium",
        "rxnorm_cui": "11289",
        "brand_aliases": ["Coumadin", "Jantoven"],
        "atc_code": "B01AA03",
        "therapeutic_class": "Vitamin K Antagonist Oral Anticoagulant",
        "dietary_guideline": "Maintain strictly consistent dietary intake of Vitamin K-rich leafy green vegetables (kale, spinach).",
        "verification_summary": "5 mg tablet once daily evening • Vitamin K antagonist anticoagulant [Oral Route] [INR Monitored]",
        "lasa_warning": None
    },
    "ranitidine": {
        "concept_name": "Ranitidine Hydrochloride 150 MG Oral Tablet",
        "generic_name": "Ranitidine Hydrochloride",
        "rxnorm_cui": "9143",
        "brand_aliases": ["Zantac"],
        "atc_code": "A02BA02",
        "therapeutic_class": "Histamine H2-Receptor Antagonist",
        "dietary_guideline": "Take with meals or at bedtime to reduce nocturnal gastric acid secretion.",
        "verification_summary": "150 mg twice daily • H2 blocker gastric acid reducer [Oral Route]",
        "lasa_warning": "FDA Tall Man Alert: ZANtac (ranitidine) can be dangerously confused with XANax (alprazolam). Confirm GI indication."
    },
    "alprazolam": {
        "concept_name": "Alprazolam 0.5 MG Oral Tablet",
        "generic_name": "Alprazolam",
        "rxnorm_cui": "596",
        "brand_aliases": ["Xanax", "Xanax XR"],
        "atc_code": "N05BA12",
        "therapeutic_class": "Short-Acting Triazolobenzodiazepine Anxiolytic",
        "dietary_guideline": "Grapefruit juice inhibits CYP3A4 metabolism and markedly increases alprazolam plasma levels. Avoid concomitant alcohol.",
        "verification_summary": "0.5 mg oral TID PRN anxiety • Benzodiazepine schedule IV [Oral Route] [CNS Depressant Warning]",
        "lasa_warning": "FDA Tall Man Alert: XANax (alprazolam) can be dangerously confused with ZANtac (ranitidine). Confirm anxiolytic indication."
    },
    "atorvastatin": {
        "concept_name": "Atorvastatin Calcium 20 MG Oral Tablet",
        "generic_name": "Atorvastatin Calcium",
        "rxnorm_cui": "83367",
        "brand_aliases": ["Lipitor"],
        "atc_code": "C10AA05",
        "therapeutic_class": "HMG-CoA Reductase Inhibitor (Statin)",
        "dietary_guideline": "Grapefruit juice significantly inhibits intestinal CYP3A4, elevating systemic Atorvastatin concentrations and myopathy risk.",
        "verification_summary": "20 mg tablet once daily at bedtime • HMG-CoA reductase inhibitor [Oral Route] [Lipid Lowering]",
        "lasa_warning": None
    },
    "metformin": {
        "concept_name": "Metformin Hydrochloride 500 MG Oral Tablet",
        "generic_name": "Metformin Hydrochloride",
        "rxnorm_cui": "6809",
        "brand_aliases": ["Glucophage", "Fortamet"],
        "atc_code": "A10BA02",
        "therapeutic_class": "Biguanide Antihyperglycemic Agent",
        "dietary_guideline": "Take with meals to minimize gastrointestinal upset. Withhold before iodinated contrast imaging in renal impairment.",
        "verification_summary": "500 mg twice daily with food • Biguanide antidiabetic [Oral Route] [eGFR Monitored]",
        "lasa_warning": None
    },
    "ibuprofen": {
        "concept_name": "Ibuprofen 400 MG Oral Tablet",
        "generic_name": "Ibuprofen",
        "rxnorm_cui": "5640",
        "brand_aliases": ["Advil", "Motrin"],
        "atc_code": "M01AE01",
        "therapeutic_class": "Non-Steroidal Anti-Inflammatory Drug (NSAID)",
        "dietary_guideline": "Take with meals or milk to minimize potential stomach upset or epigastric mucosal vulnerability.",
        "verification_summary": "400 mg oral PRN for pain • Non-selective NSAID [Oral Route] [Renal/GI Warning]",
        "lasa_warning": None
    },
    "metoprolol": {
        "concept_name": "Metoprolol Tartrate 25 MG Oral Tablet",
        "generic_name": "Metoprolol Tartrate",
        "rxnorm_cui": "6918",
        "brand_aliases": ["Lopressor", "Toprol XL"],
        "atc_code": "C07AB02",
        "therapeutic_class": "Cardioselective Beta-1 Adrenergic Receptor Blocker",
        "dietary_guideline": "Take consistently with or immediately following meals for optimal systemic bioavailability.",
        "verification_summary": "25 mg twice daily with food • Beta-1 blocker [Oral Route] [Rate Control]",
        "lasa_warning": None
    },
    "lisinopril": {
        "concept_name": "Lisinopril 10 MG Oral Tablet",
        "generic_name": "Lisinopril",
        "rxnorm_cui": "29046",
        "brand_aliases": ["Prinivil", "Zestril"],
        "atc_code": "C09AA03",
        "therapeutic_class": "Angiotensin-Converting Enzyme (ACE) Inhibitor",
        "dietary_guideline": "Avoid potassium supplements or potassium-containing salt substitutes without physician supervision.",
        "verification_summary": "10 mg daily • ACE inhibitor antihypertensive [Oral Route] [Potassium/Renal Monitored]",
        "lasa_warning": None
    },
    "amoxicillin": {
        "concept_name": "Amoxicillin 500 MG Oral Capsule",
        "generic_name": "Amoxicillin",
        "rxnorm_cui": "723",
        "brand_aliases": ["Amoxil"],
        "atc_code": "J01CA04",
        "therapeutic_class": "Aminopenicillin Beta-Lactam Antibacterial",
        "dietary_guideline": "May be taken without regard to meals. Complete full prescribed course to prevent microbial resistance.",
        "verification_summary": "500 mg TID • Aminopenicillin antibacterial [Oral Route] [Allergy Check Mandatory]",
        "lasa_warning": None
    },
    "ciprofloxacin": {
        "concept_name": "Ciprofloxacin 500 MG Oral Tablet",
        "generic_name": "Ciprofloxacin",
        "rxnorm_cui": "2551",
        "brand_aliases": ["Cipro"],
        "atc_code": "J01MA02",
        "therapeutic_class": "Fluoroquinolone Broad-Spectrum Antibacterial",
        "dietary_guideline": "Do not take concurrently with dairy products, antacids, or iron supplements (chelation severely blunts absorption).",
        "verification_summary": "500 mg Q12H • Fluoroquinolone antibiotic [Oral Route] [FDA Boxed Warning: Tendinitis/QT]",
        "lasa_warning": None
    },
    "allopurinol": {
        "concept_name": "Allopurinol 100 MG Oral Tablet",
        "generic_name": "Allopurinol",
        "rxnorm_cui": "519",
        "brand_aliases": ["Zyloprim"],
        "atc_code": "M04AA01",
        "therapeutic_class": "Xanthine Oxidase Inhibitor",
        "dietary_guideline": "Drink at least 2 liters of fluid daily to prevent renal urate deposition.",
        "verification_summary": "100 mg daily • Xanthine oxidase inhibitor [Oral Route] [HLA-B*5801 Screen in High Risk]",
        "lasa_warning": None
    },
    "digoxin": {
        "concept_name": "Digoxin 0.125 MG Oral Tablet",
        "generic_name": "Digoxin",
        "rxnorm_cui": "3407",
        "brand_aliases": ["Lanoxin"],
        "atc_code": "C01AA05",
        "therapeutic_class": "Cardiac Glycoside Inotrope",
        "dietary_guideline": "Avoid high-fiber meals immediately surrounding administration; maintain normal serum potassium to prevent toxicity.",
        "verification_summary": "0.125 mg daily • Cardiac glycoside [Oral Route] [Therapeutic Drug Monitoring: 0.5-0.9 ng/mL]",
        "lasa_warning": None
    },
    "sildenafil": {
        "concept_name": "Sildenafil Citrate 50 MG Oral Tablet",
        "generic_name": "Sildenafil Citrate",
        "rxnorm_cui": "136411",
        "brand_aliases": ["Viagra", "Revatio"],
        "atc_code": "G04BE03",
        "therapeutic_class": "Phosphodiesterase-5 (PDE5) Inhibitor",
        "dietary_guideline": "High-fat meals delay absorption and peak onset. Strictly contraindicated with any organic nitrates.",
        "verification_summary": "50 mg oral PRN 1 hour before sexual activity • PDE5 inhibitor [Oral Route] [Nitrate Contraindication]",
        "lasa_warning": None
    },
    "nitroglycerin": {
        "concept_name": "Nitroglycerin 0.4 MG Sublingual Tablet",
        "generic_name": "Nitroglycerin",
        "rxnorm_cui": "7052",
        "brand_aliases": ["Nitrostat"],
        "atc_code": "C01DA02",
        "therapeutic_class": "Organic Nitrate Vasodilator",
        "dietary_guideline": "Dissolve under tongue at first onset of acute angina; do not swallow.",
        "verification_summary": "0.4 mg sublingual PRN angina • Organic nitrate [Sublingual Route] [PDE5 Inhibitor Contraindicated]",
        "lasa_warning": None
    },
    "simvastatin": {
        "concept_name": "Simvastatin 20 MG Oral Tablet",
        "generic_name": "Simvastatin",
        "rxnorm_cui": "36567",
        "brand_aliases": ["Zocor"],
        "atc_code": "C10AA01",
        "therapeutic_class": "HMG-CoA Reductase Inhibitor (Statin)",
        "dietary_guideline": "Grapefruit juice significantly inhibits CYP3A4, markedly increasing simvastatin plasma concentrations.",
        "verification_summary": "20 mg daily evening • Statin lipid lowering [Oral Route] [CYP3A4 Contraindications Apply]",
        "lasa_warning": None
    },
    "rosuvastatin": {
        "concept_name": "Rosuvastatin Calcium 10 MG Oral Tablet",
        "generic_name": "Rosuvastatin Calcium",
        "rxnorm_cui": "301542",
        "brand_aliases": ["Crestor"],
        "atc_code": "C10AA07",
        "therapeutic_class": "HMG-CoA Reductase Inhibitor (Statin)",
        "dietary_guideline": "Take with or without food at any time of day. Minimal CYP3A4 metabolism.",
        "verification_summary": "10 mg daily • High-intensity statin [Oral Route] [SLCO1B1 Monitored]",
        "lasa_warning": None
    },
    "naproxen": {
        "concept_name": "Naproxen 500 MG Oral Tablet",
        "generic_name": "Naproxen",
        "rxnorm_cui": "7258",
        "brand_aliases": ["Aleve", "Naprosyn"],
        "atc_code": "M01AE02",
        "therapeutic_class": "Non-Steroidal Anti-Inflammatory Drug (NSAID)",
        "dietary_guideline": "Take with food or full glass of water to reduce gastric irritation.",
        "verification_summary": "500 mg BID with food • Non-selective NSAID [Oral Route] [GI/Cardiorenal Warning]",
        "lasa_warning": None
    },
    "clopidogrel": {
        "concept_name": "Clopidogrel 75 MG Oral Tablet",
        "generic_name": "Clopidogrel",
        "rxnorm_cui": "32968",
        "brand_aliases": ["Plavix"],
        "atc_code": "B01AC04",
        "therapeutic_class": "Thienopyridine P2Y12 Platelet Inhibitor",
        "dietary_guideline": "May be taken with or without food. Requires hepatic CYP2C19 bioactivation.",
        "verification_summary": "75 mg daily • P2Y12 platelet inhibitor [Oral Route] [CYP2C19 Genotype Relevant]",
        "lasa_warning": None
    },
    "fluconazole": {
        "concept_name": "Fluconazole 150 MG Oral Capsule",
        "generic_name": "Fluconazole",
        "rxnorm_cui": "4492",
        "brand_aliases": ["Diflucan"],
        "atc_code": "J02AC01",
        "therapeutic_class": "Triazole Antifungal Agent",
        "dietary_guideline": "May be taken with or without food. Potent inhibitor of CYP2C9 and CYP2C19.",
        "verification_summary": "150 mg single dose • Triazole antifungal [Oral Route] [Potent CYP2C9/2C19 Inhibitor]",
        "lasa_warning": None
    },
    "amiodarone": {
        "concept_name": "Amiodarone Hydrochloride 200 MG Oral Tablet",
        "generic_name": "Amiodarone Hydrochloride",
        "rxnorm_cui": "703",
        "brand_aliases": ["Cordarone", "Pacerone"],
        "atc_code": "C01BD01",
        "therapeutic_class": "Class III Antiarrhythmic Agent",
        "dietary_guideline": "Avoid grapefruit juice. Take consistently with respect to meals. Broad CYP/P-gp inhibitor.",
        "verification_summary": "200 mg daily • Class III antiarrhythmic [Oral Route] [Multi-CYP & P-gp Inhibitor]",
        "lasa_warning": None
    },
    "spironolactone": {
        "concept_name": "Spironolactone 25 MG Oral Tablet",
        "generic_name": "Spironolactone",
        "rxnorm_cui": "9997",
        "brand_aliases": ["Aldactone"],
        "atc_code": "C03DA01",
        "therapeutic_class": "Aldosterone Receptor Antagonist / Potassium-Sparing Diuretic",
        "dietary_guideline": "Avoid potassium supplements and high-potassium salt substitutes.",
        "verification_summary": "25 mg daily • Mineralocorticoid antagonist [Oral Route] [Potassium Monitored]",
        "lasa_warning": None
    },
    "clarithromycin": {
        "concept_name": "Clarithromycin 500 MG Oral Tablet",
        "generic_name": "Clarithromycin",
        "rxnorm_cui": "21212",
        "brand_aliases": ["Biaxin"],
        "atc_code": "J01FA09",
        "therapeutic_class": "Macrolide Antibacterial / Potent CYP3A4 Inhibitor",
        "dietary_guideline": "May be taken with or without food. Potent CYP3A4 and P-gp inhibitor.",
        "verification_summary": "500 mg Q12H • Macrolide antibiotic [Oral Route] [FDA Boxed Statin Warning]",
        "lasa_warning": None
    },
    "acetaminophen": {
        "concept_name": "Acetaminophen 500 MG Oral Tablet",
        "generic_name": "Acetaminophen",
        "rxnorm_cui": "161",
        "brand_aliases": ["Tylenol", "Paracetamol"],
        "atc_code": "N02BE01",
        "therapeutic_class": "Analgesic and Antipyretic Agent",
        "dietary_guideline": "Avoid excessive chronic alcohol consumption (CYP2E1 induction increases hepatotoxic NAPQI). Maximum 3000 mg/24 hours.",
        "verification_summary": "500 mg Q4-6H PRN pain • Non-opioid analgesic [Oral Route] [Max 3g/day]",
        "lasa_warning": None
    },
    "clonazepam": {
        "concept_name": "Clonazepam 0.5 MG Oral Tablet",
        "generic_name": "Clonazepam",
        "rxnorm_cui": "2598",
        "brand_aliases": ["Klonopin"],
        "atc_code": "N03AE01",
        "therapeutic_class": "Long-Acting Benzodiazepine Anticonvulsant",
        "dietary_guideline": "Avoid concomitant alcohol. Taper gradually under medical supervision.",
        "verification_summary": "0.5 mg oral twice daily • Benzodiazepine anticonvulsant [Oral Route]",
        "lasa_warning": "FDA Tall Man Alert: cloNAZEpam (clonazepam) vs lorAZEpam (lorazepam). Differing half-lives."
    },
    "lorazepam": {
        "concept_name": "Lorazepam 1 MG Oral Tablet",
        "generic_name": "Lorazepam",
        "rxnorm_cui": "6470",
        "brand_aliases": ["Ativan"],
        "atc_code": "N05BA06",
        "therapeutic_class": "Intermediate-Acting Benzodiazepine Anxiolytic",
        "dietary_guideline": "Avoid alcohol and other CNS depressants.",
        "verification_summary": "1 mg oral PRN anxiety • Benzodiazepine [Oral Route]",
        "lasa_warning": "FDA Tall Man Alert: lorAZEpam (lorazepam) vs cloNAZEpam (clonazepam)."
    }
}

class SQLiteRxNormCache:
    """Thread-safe SQLite persistent cache for RxNorm normalization queries."""
    def __init__(self, db_path: Optional[str] = None):
        if not db_path:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            db_path = os.path.join(base_dir, "..", "data", "rxnorm_cache.sqlite")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rxnorm_query_cache (
                        query_key TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        result_json TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS canonical_formulary (
                        generic_key TEXT PRIMARY KEY,
                        concept_name TEXT NOT NULL,
                        rxnorm_cui TEXT NOT NULL,
                        brand_aliases TEXT NOT NULL,
                        atc_code TEXT NOT NULL,
                        therapeutic_class TEXT NOT NULL,
                        dietary_guideline TEXT,
                        verification_summary TEXT,
                        lasa_warning TEXT
                    )
                """)
                # Pre-seed canonical formulary
                for key, item in CANONICAL_FORMULARY.items():
                    conn.execute("""
                        INSERT OR REPLACE INTO canonical_formulary
                        (generic_key, concept_name, rxnorm_cui, brand_aliases, atc_code,
                         therapeutic_class, dietary_guideline, verification_summary, lasa_warning)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        key,
                        item["concept_name"],
                        item["rxnorm_cui"],
                        json.dumps(item["brand_aliases"]),
                        item["atc_code"],
                        item["therapeutic_class"],
                        item.get("dietary_guideline", ""),
                        item.get("verification_summary", ""),
                        item.get("lasa_warning")
                    ))
                conn.commit()
            finally:
                conn.close()

    def get_cached(self, query: str) -> Optional[Dict[str, Any]]:
        clean_q = query.lower().strip()
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT status, result_json, confidence FROM rxnorm_query_cache WHERE query_key = ?",
                    (clean_q,)
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row["result_json"])
                return None
            finally:
                conn.close()

    def set_cached(self, query: str, status: str, result: Dict[str, Any], confidence: float):
        clean_q = query.lower().strip()
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO rxnorm_query_cache
                    (query_key, status, result_json, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (clean_q, status, json.dumps(result), confidence, time.time()))
                conn.commit()
            finally:
                conn.close()

    def get_all_canonical(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT * FROM canonical_formulary")
                rows = cur.fetchall()
                out = []
                for r in rows:
                    out.append({
                        "generic_key": r["generic_key"],
                        "concept_name": r["concept_name"],
                        "rxnorm_cui": r["rxnorm_cui"],
                        "brand_aliases": json.loads(r["brand_aliases"]),
                        "atc_code": r["atc_code"],
                        "therapeutic_class": r["therapeutic_class"],
                        "dietary_guideline": r["dietary_guideline"],
                        "verification_summary": r["verification_summary"],
                        "lasa_warning": r["lasa_warning"]
                    })
                return out
            finally:
                conn.close()

# ==============================================================================
# 4. RXNORM NORMALIZER ENGINE
# ==============================================================================

class RxNormNormalizer:
    """
    Hallucination-Proof RxNorm Normalization Engine.
    Combines NLM RxNav approximateTerm API, SQLite caching, Trigram + Normalized
    Levenshtein similarity, and LASA sound-alike error detection.
    """
    def __init__(self, db_path: Optional[str] = None):
        self.cache = SQLiteRxNormCache(db_path)
        self.http_timeout = 2.0  # seconds
        self.rxnav_base_url = "https://rxnav.nlm.nih.gov/REST"

    def _check_lasa_alert(self, query: str, candidate_name: str) -> Optional[str]:
        """Detects whether candidate drug is part of an active FDA/ISMP LASA pair."""
        q = query.lower().strip()
        c = candidate_name.lower().strip()
        for item in LASA_PAIRS:
            d1, d2 = item["pair"]
            g1, g2 = item["generics"]
            # If query or candidate matches either drug or generic in the pair
            if d1 in q or g1 in q or d1 in c or g1 in c:
                return (
                    f"FDA Tall Man Alert: {item['tall_man'][0]} ({g1}) vs {item['tall_man'][1]} ({g2}). "
                    f"{item['clinical_alert']}"
                )
            if d2 in q or g2 in q or d2 in c or g2 in c:
                return (
                    f"FDA Tall Man Alert: {item['tall_man'][1]} ({g2}) vs {item['tall_man'][0]} ({g1}). "
                    f"{item['clinical_alert']}"
                )
            # Look-alike phonetic similarity between the two confused names
            sim1 = combined_similarity(q, d1)
            sim2 = combined_similarity(q, d2)
            if 0.50 <= sim1 < 0.90 and 0.50 <= sim2 < 0.90:
                return (
                    f"FDA Tall Man Alert: {item['tall_man'][0]} ({g1}) vs {item['tall_man'][1]} ({g2}). "
                    f"High sound-alike confusion risk for query '{query}'. Validate clinical indication."
                )
        return None

    def _query_rxnav_api(self, query: str) -> List[Dict[str, Any]]:
        """Queries NLM RxNav REST API approximateTerm endpoint."""
        url = f"{self.rxnav_base_url}/approximateTerm.json"
        params = {"term": query, "maxEntries": 5}
        try:
            with httpx.Client(timeout=self.http_timeout) as client:
                resp = client.get(url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("approximateGroup", {}).get("candidate", [])
                    return candidates if isinstance(candidates, list) else []
        except Exception:
            # Network failure or timeout; gracefully fall back to local store
            pass
        return []

    def _score_candidate(self, query: str, candidate: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
        """Calculates best match score across generic name and brand aliases."""
        best_score = 0.0
        # Compare against generic key
        gen_score = combined_similarity(query, candidate["generic_key"])
        best_score = max(best_score, gen_score)

        # Compare against concept name
        c_score = combined_similarity(query, candidate["concept_name"])
        best_score = max(best_score, c_score)

        # Compare against brand aliases
        for brand in candidate.get("brand_aliases", []):
            b_score = combined_similarity(query, brand)
            best_score = max(best_score, b_score)

        return best_score, candidate

    def normalize(self, drug_query: str) -> Dict[str, Any]:
        """
        Normalizes a clinical drug query string into a validated RxNorm concept.
        Enforces strict safety thresholds:
        - Similarity < 0.70: Rejects with 422 Unprocessable Entity.
        - 0.70 <= Similarity < 0.88: Returns ranked disambiguation options.
        - Similarity >= 0.88: Returns canonical RxCUI, generic name, brand aliases, and ATC codes.
        """
        clean_q = drug_query.strip()
        if not clean_q:
            raise HTTPException(status_code=400, detail="Medication query string cannot be empty.")

        # 1. Check local SQLite query cache
        cached = self.cache.get_cached(clean_q)
        if cached:
            # If cached result was disambiguation or resolved, return it
            return cached

        # 2. Gather candidates from canonical local formulary
        canonical_entries = self.cache.get_all_canonical()
        scored_candidates: List[Tuple[float, Dict[str, Any]]] = []

        for item in canonical_entries:
            score, cand = self._score_candidate(clean_q, item)
            scored_candidates.append((score, cand))

        # 3. Query NLM RxNav REST API (approximateTerm) for external candidates
        api_candidates = self._query_rxnav_api(clean_q)
        for api_cand in api_candidates:
            cand_name = api_cand.get("name") or api_cand.get("synonym") or ""
            cand_cui = str(api_cand.get("rxcui", ""))
            if cand_name:
                sim = combined_similarity(clean_q, cand_name)
                # Check if it matches an existing canonical entry
                matched = False
                for idx, (sc, ce) in enumerate(scored_candidates):
                    if ce["rxnorm_cui"] == cand_cui:
                        matched = True
                        if sim > sc:
                            scored_candidates[idx] = (sim, ce)
                        break
                if not matched and sim >= 0.70:
                    scored_candidates.append((sim, {
                        "generic_key": cand_name.lower(),
                        "concept_name": cand_name,
                        "rxnorm_cui": cand_cui,
                        "brand_aliases": [],
                        "atc_code": "V03AX",
                        "therapeutic_class": "RxNav Normalized Concept",
                        "dietary_guideline": "Review prescribing monograph.",
                        "verification_summary": f"{cand_name} (RxCUI: {cand_cui})",
                        "lasa_warning": self._check_lasa_alert(clean_q, cand_name)
                    }))

        # Sort candidates descending by match similarity
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        if not scored_candidates:
            raise HTTPException(
                status_code=422,
                detail=f"Unprocessable Entity: No clinical candidates identified for '{clean_q}'."
            )

        best_score, top_candidate = scored_candidates[0]

        # Check for LASA alert on top candidate
        lasa_alert = top_candidate.get("lasa_warning") or self._check_lasa_alert(clean_q, top_candidate["generic_key"])

        # =====================================================================
        # SPECIFICATION 1: HARD CUTOFF < 0.70 -> 422 Unprocessable Entity
        # =====================================================================
        if best_score < 0.70:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unprocessable Entity: Candidate match confidence ({best_score:.2f}) for '{clean_q}' "
                    f"is strictly below clinical cutoff 0.70. Automated normalization rejected to "
                    f"prevent pharmacological hallucination."
                )
            )

        # =====================================================================
        # SPECIFICATION 2: 0.70 <= Similarity < 0.88 -> Disambiguation Options
        # =====================================================================
        if 0.70 <= best_score < 0.88:
            # Filter candidates in the disambiguation range (>= 0.65 up to 0.88)
            options = []
            for sc, cand in scored_candidates:
                if sc >= 0.65 and len(options) < 5:
                    cand_lasa = cand.get("lasa_warning") or self._check_lasa_alert(clean_q, cand["generic_key"])
                    options.append({
                        "rxnorm_cui": cand["rxnorm_cui"],
                        "generic_name": cand.get("generic_name") or cand["generic_key"].title(),
                        "brand_aliases": cand.get("brand_aliases", []),
                        "atc_code": cand["atc_code"],
                        "similarity": round(sc, 3),
                        "lasa_warning": cand_lasa
                    })

            result = {
                "status": "disambiguation_required",
                "query": clean_q,
                "match_confidence": round(best_score * 100, 1),
                "concept_name": f"Disambiguation Required for '{clean_q}'",
                "rxnorm_cui": top_candidate["rxnorm_cui"],
                "generic_name": top_candidate.get("generic_name") or top_candidate["generic_key"].title(),
                "brand_aliases": top_candidate.get("brand_aliases", []),
                "brand_equivalent": top_candidate["brand_aliases"][0] if top_candidate.get("brand_aliases") else "",
                "atc_code": top_candidate["atc_code"],
                "therapeutic_class": top_candidate["therapeutic_class"],
                "dietary_guideline": top_candidate.get("dietary_guideline", "Consult clinical pharmacotherapy guide."),
                "verification_summary": (
                    f"Multiple potential candidate drugs detected (Confidence: {round(best_score * 100, 1)}%). "
                    f"Clinician selection required."
                ),
                "disambiguation_options": options,
                "lasa_warning": lasa_alert
            }
            # Cache disambiguation
            self.cache.set_cached(clean_q, "disambiguation_required", result, best_score)
            return result

        # =====================================================================
        # SPECIFICATION 3: Similarity >= 0.88 -> Canonical RxCUI & Attributes
        # =====================================================================
        brand_aliases = top_candidate.get("brand_aliases", [])
        brand_eq = brand_aliases[0] if brand_aliases else (top_candidate.get("generic_name") or top_candidate["generic_key"].title())

        result = {
            "status": "resolved",
            "concept_name": top_candidate["concept_name"],
            "generic_name": top_candidate.get("generic_name") or top_candidate["generic_key"].title(),
            "rxnorm_cui": top_candidate["rxnorm_cui"],
            "match_confidence": round(best_score * 100, 1),
            "brand_aliases": brand_aliases,
            "brand_equivalent": brand_eq,
            "atc_code": top_candidate["atc_code"],
            "therapeutic_class": top_candidate["therapeutic_class"],
            "dietary_guideline": top_candidate.get("dietary_guideline", "Routine pharmacotherapy monitoring."),
            "verification_summary": top_candidate.get("verification_summary", f"Standard clinical dosing protocol for {clean_q}."),
            "lasa_warning": lasa_alert
        }
        # Cache resolved canonical match
        self.cache.set_cached(clean_q, "resolved", result, best_score)
        return result

# Singleton instance exported
rxnorm_normalizer = RxNormNormalizer()

# Dedicated FastAPI router exported for clean modularity
normalizer_router = APIRouter(prefix="/api/rxnorm", tags=["RxNorm Normalizer"])

@normalizer_router.post("/normalize")
def normalize_endpoint(payload: Dict[str, str]):
    query = payload.get("query", "")
    return rxnorm_normalizer.normalize(query)
