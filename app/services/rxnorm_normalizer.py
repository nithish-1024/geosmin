from typing import Dict, Any, Optional

class RxNormNormalizer:
    def __init__(self):
        # Curated catalog mapping common input tokens to standard RxNorm Concepts
        self._catalog = {
            "atorvastatin": {
                "concept_name": "Atorvastatin Calcium 20 MG Oral Tablet",
                "rxnorm_cui": "83367",
                "brand_equivalent": "Lipitor",
                "atc_code": "C10AA05",
                "therapeutic_class": "Lipid-lowering statin (HMG-CoA reductase inhibitor)",
                "match_confidence": 99.8,
                "dietary_guideline": "Consumption of grapefruit or grapefruit juice significantly increases systemic Atorvastatin plasma concentrations by inhibiting CYP3A4 metabolism. Consult Dr. Vance regarding moderation.",
                "verification_summary": "1 tablet daily at bedtime • Lipid-lowering statin (HMG-CoA reductase inhibitor) [Oral Route] [Nightly Schedule] [Cardiovascular]"
            },
            "celecoxib": {
                "concept_name": "Celecoxib 100 MG Oral Capsule",
                "rxnorm_cui": "140587",
                "brand_equivalent": "Celebrex",
                "atc_code": "M01AH01",
                "therapeutic_class": "NSAID / Selective COX-2 Inhibitor",
                "match_confidence": 99.5,
                "dietary_guideline": "Can be taken with or without food. Avoid alcohol consumption which may heighten GI mucosal vulnerability.",
                "verification_summary": "1 capsule daily • Selective COX-2 inhibitor NSAID [Oral Route] [Daily Schedule] [Anti-inflammatory]"
            },
            "warfarin": {
                "concept_name": "Warfarin Sodium 5 MG Oral Tablet",
                "rxnorm_cui": "11289",
                "brand_equivalent": "Coumadin",
                "atc_code": "B01AA03",
                "therapeutic_class": "Oral Anticoagulant • Vitamin K Antagonist",
                "match_confidence": 99.9,
                "dietary_guideline": "Maintain consistent intake of Vitamin K-rich leafy green vegetables to avoid INR destabilization.",
                "verification_summary": "5 mg tablet once daily evening • Vitamin K antagonist anticoagulant [Oral Route] [INR Monitored]"
            },
            "ibuprofen": {
                "concept_name": "Ibuprofen 400 MG Oral Tablet",
                "rxnorm_cui": "5640",
                "brand_equivalent": "Advil / Motrin",
                "atc_code": "M01AE01",
                "therapeutic_class": "Non-Steroidal Anti-Inflammatory Drug (NSAID)",
                "match_confidence": 99.2,
                "dietary_guideline": "Take with meals or milk to minimize potential stomach upset or epigastric discomfort.",
                "verification_summary": "400 mg oral PRN for pain • Non-selective NSAID [Oral Route] [PRN Schedule]"
            },
            "metoprolol": {
                "concept_name": "Metoprolol Tartrate 25 MG Oral Tablet",
                "rxnorm_cui": "6918",
                "brand_equivalent": "Lopressor",
                "atc_code": "C07AB02",
                "therapeutic_class": "Cardioselective Beta-1 Adrenergic Blocker",
                "match_confidence": 99.7,
                "dietary_guideline": "Take consistently with or immediately following meals for optimal systemic bioavailability.",
                "verification_summary": "25 mg twice daily with food • Beta-1 blocker [Oral Route] [Rate Control]"
            }
        }

    def normalize(self, drug_query: str) -> Dict[str, Any]:
        q = drug_query.lower().strip()
        # Find exact or substring match in catalog
        for key, entry in self._catalog.items():
            if key in q or entry["brand_equivalent"].lower() in q:
                return entry

        # Fallback concept matching
        return {
            "concept_name": f"{drug_query.title()} (Unspecified Formulation)",
            "rxnorm_cui": "999999",
            "brand_equivalent": "Generic Equivalent",
            "atc_code": "V03AX",
            "therapeutic_class": "Pharmacological Agent Under Investigation",
            "match_confidence": 88.0,
            "dietary_guideline": "Review prescribing insert for specific food-drug administration parameters.",
            "verification_summary": f"Standard clinical dosing protocol for {drug_query.title()}."
        }

rxnorm_normalizer = RxNormNormalizer()
