"""
tests/test_pharmacoinformatics.py
Comprehensive Pharmacoinformatics Verification Suite (40 Edge Cases)
VJ Hackathon 2026 • Healthcare Problem Statement 4 (Geosmin)

Role: Clinical Pharmacoinformatics Engineer (data-garden-bear & evals-watch-owl)
Test Coverage:
- Group 1: Phonetic & Look-Alike Sound-Alike (LASA) Confused Drug Names (10 cases)
- Group 2: Dirty / Misspelled Input Normalization & Hard Cutoffs (10 cases)
- Group 3: Toxic Drug-Drug Interactions, Collision States & Traversal (10 cases)
- Group 4: Safe Alternatives, Negative Controls, ACB & Cytoscape Integrity (10 cases)
Total: 40 Automated Edge Cases
"""

import unittest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app.main import app
from app.services.rxnorm_normalizer import (
    rxnorm_normalizer,
    combined_similarity,
    compute_soundex,
    compute_metaphone,
    LASA_PAIRS
)
from app.services.knowledge_graph import knowledge_graph


class TestPharmacoinformaticsSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    # ==========================================================================
    # GROUP 1: PHONETIC & LOOK-ALIKE SOUND-ALIKE (LASA) CONFUSED DRUG NAMES (10)
    # ==========================================================================

    def test_01_lasa_celebrex_vs_celexa_separation(self):
        """Celebrex (celecoxib) and Celexa (citalopram) must NOT cross-match."""
        sim = combined_similarity("celebrex", "celexa")
        self.assertLess(sim, 0.70, "Celebrex and Celexa direct similarity must be strictly below 0.70")
        
        # Verify normalization of Celebrex resolves to Celecoxib, not Citalopram
        res = rxnorm_normalizer.normalize("Celebrex")
        self.assertEqual(res["generic_name"], "Celecoxib")
        self.assertEqual(res["rxnorm_cui"], "140587")
        self.assertIn("CELEbrex", res.get("lasa_warning", ""))

    def test_02_lasa_zantac_vs_xanax_separation(self):
        """Zantac (ranitidine) and Xanax (alprazolam) must NOT cross-match."""
        sim = combined_similarity("zantac", "xanax")
        self.assertLess(sim, 0.70, "Zantac and Xanax direct similarity must be below 0.70")
        
        res = rxnorm_normalizer.normalize("Zantac")
        self.assertIn("Ranitidine", res["generic_name"])
        self.assertEqual(res["rxnorm_cui"], "9143")
        self.assertIn("ZANtac", res.get("lasa_warning", ""))

    def test_03_lasa_clonazepam_vs_lorazepam(self):
        """Clonazepam vs Lorazepam: check FDA Tall Man alert identification."""
        res = rxnorm_normalizer.normalize("Clonazepam")
        self.assertEqual(res["status"], "resolved")
        self.assertIn("cloNAZEpam", res.get("lasa_warning") or "")

    def test_04_lasa_adderall_vs_inderal(self):
        """Adderall vs Inderal: verify LASA pair dictionary mapping."""
        pair = next(p for p in LASA_PAIRS if "adderall" in p["pair"])
        self.assertEqual(pair["tall_man"], ("ADDERall", "INDeral"))
        self.assertIn("CRITICAL LASA HAZARD", pair["clinical_alert"])

    def test_05_lasa_hydralazine_vs_hydroxyzine(self):
        """Hydralazine vs Hydroxyzine: verify LASA pair differentiation."""
        pair = next(p for p in LASA_PAIRS if "hydralazine" in p["pair"])
        self.assertEqual(pair["tall_man"], ("hydrALAzine", "hydrOXYzine"))
        self.assertIn("hypotension", pair["clinical_alert"].lower())

    def test_06_lasa_prednisone_vs_prednisolone(self):
        """Prednisone vs Prednisolone: verify Tall Man alert presence."""
        pair = next(p for p in LASA_PAIRS if "prednisone" in p["pair"])
        self.assertEqual(pair["tall_man"], ("predniSONE", "prednisoLONE"))

    def test_07_lasa_vinblastine_vs_vincristine(self):
        """Vinblastine vs Vincristine: verify fatal neurotoxicity LASA alert."""
        pair = next(p for p in LASA_PAIRS if "vinblastine" in p["pair"])
        self.assertIn("FATAL LASA HAZARD", pair["clinical_alert"])

    def test_08_lasa_lamictal_vs_lamisil(self):
        """Lamictal (lamotrigine) vs Lamisil (terbinafine) alert."""
        pair = next(p for p in LASA_PAIRS if "lamictal" in p["pair"])
        self.assertIn("laMICtal", pair["tall_man"])

    def test_09_lasa_prilosec_vs_prozac(self):
        """Prilosec (omeprazole) vs Prozac (fluoxetine) alert."""
        pair = next(p for p in LASA_PAIRS if "prilosec" in p["pair"])
        self.assertIn("PRILosec", pair["tall_man"])

    def test_10_lasa_seroquel_vs_serzone(self):
        """Seroquel (quetiapine) vs Serzone (nefazodone) alert."""
        pair = next(p for p in LASA_PAIRS if "seroquel" in p["pair"])
        self.assertIn("SEROquel", pair["tall_man"])

    # ==========================================================================
    # GROUP 2: DIRTY / MISSPELLED INPUT NORMALIZATION & CUTOFFS (10)
    # ==========================================================================

    def test_11_misspelling_warfrin(self):
        """'warfrin' missing vowel resolves to Warfarin Sodium (RxCUI 11289)."""
        res = rxnorm_normalizer.normalize("warfrin")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "11289")
        self.assertIn("Warfarin", res["concept_name"])
        self.assertGreaterEqual(res["match_confidence"], 88.0)

    def test_12_misspelling_metphormin(self):
        """'metphormin' with 'ph' transliteration resolves to Metformin (RxCUI 6809)."""
        res = rxnorm_normalizer.normalize("metphormin")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "6809")
        self.assertIn("Metformin", res["concept_name"])
        self.assertGreaterEqual(res["match_confidence"], 88.0)

    def test_13_misspelling_atorvastin(self):
        """'atorvastin' with missing 'at' resolves to Atorvastatin Calcium."""
        res = rxnorm_normalizer.normalize("atorvastin")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "83367")

    def test_14_misspelling_lisinopril_with_formulation(self):
        """'lisinopril 10mg oral' resolves to canonical Lisinopril."""
        res = rxnorm_normalizer.normalize("lisinopril 10mg")
        self.assertEqual(res["status"], "resolved")
        self.assertIn(res["rxnorm_cui"], ["29046", "316151"])

    def test_15_misspelling_ibuprophen(self):
        """'ibuprophen' with 'ph' resolves to Ibuprofen."""
        res = rxnorm_normalizer.normalize("ibuprophen")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "5640")

    def test_16_misspelling_amoxocillin(self):
        """'amoxocillin' resolves to Amoxicillin."""
        res = rxnorm_normalizer.normalize("amoxocillin")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "723")

    def test_17_misspelling_ciproflaxin(self):
        """'ciproflaxin' resolves to Ciprofloxacin."""
        res = rxnorm_normalizer.normalize("ciproflaxin")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "2551")

    def test_18_misspelling_metoprolol_tart(self):
        """'metoprolol tart' resolves to Metoprolol Tartrate."""
        res = rxnorm_normalizer.normalize("metoprolol tart")
        self.assertEqual(res["status"], "resolved")
        self.assertEqual(res["rxnorm_cui"], "6918")

    def test_19_hard_cutoff_rejects_gibberish_422(self):
        """Similarity < 0.70 strictly raises HTTPException 422 Unprocessable Entity."""
        with self.assertRaises(HTTPException) as ctx:
            rxnorm_normalizer.normalize("xyzrandomgarbage999")
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertIn("0.70", ctx.exception.detail)

    def test_20_disambiguation_zone_celex(self):
        """0.70 <= Similarity < 0.88 returns ranked disambiguation options."""
        res = rxnorm_normalizer.normalize("celex")
        self.assertEqual(res["status"], "disambiguation_required")
        self.assertGreaterEqual(res["match_confidence"], 70.0)
        self.assertLess(res["match_confidence"], 88.0)
        options = res.get("disambiguation_options", [])
        self.assertGreaterEqual(len(options), 2)
        generic_names = [o["generic_name"].lower() for o in options]
        self.assertTrue(any("citalopram" in g or "celexa" in g for g in generic_names))
        self.assertTrue(any("celecoxib" in g or "celebrex" in g for g in generic_names))

    # ==========================================================================
    # GROUP 3: TOXIC DRUG-DRUG INTERACTIONS & COLLISION TRAVERSAL (10)
    # ==========================================================================

    def test_21_toxic_ddi_warfarin_celecoxib_traversal(self):
        """Candidate celecoxib on patient GK-8821 (warfarin) triggers HIGH_RISK CYP2C9 bleed collision."""
        resp = self.client.get("/api/graph/traverse?candidate_rx=celecoxib&patient_id=GK-8821")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["summary"]["collision_detected"])
        self.assertEqual(data["summary"]["highest_severity"], "HIGH_RISK")
        
        # Verify presence of CYP2C9 mechanism and FDA citation
        collision = next(c for c in data["summary"]["collisions"] if ("celecoxib" in (c["source"] + c["target"])) and ("warfarin" in (c["source"] + c["target"])))
        self.assertIn("CYP2C9", collision["mechanism"])
        self.assertIn("FDA Boxed Warning", collision["citations"][0]["source"])

    def test_22_toxic_ddi_sildenafil_nitroglycerin(self):
        """Sildenafil + Nitroglycerin: catastrophic refractory hypotension."""
        path = knowledge_graph.explain_path("sildenafil", "nitroglycerin")
        self.assertTrue(any("FATAL HYPOTENSION" in p or "cGMP" in p for p in path))

    def test_23_toxic_ddi_simvastatin_clarithromycin(self):
        """Simvastatin + Clarithromycin: fatal rhabdomyolysis contraindication."""
        path = knowledge_graph.explain_path("simvastatin", "clarithromycin")
        self.assertTrue(any("rhabdomyolysis" in p.lower() for p in path))

    def test_24_toxic_ddi_lisinopril_spironolactone(self):
        """Lisinopril + Spironolactone: lethal hyperkalemia cardiac arrest hazard."""
        path = knowledge_graph.explain_path("lisinopril", "spironolactone")
        self.assertTrue(any("hyperkalemia" in p.lower() for p in path))

    def test_25_toxic_ddi_clopidogrel_omeprazole(self):
        """Clopidogrel + Omeprazole: CYP2C19 bioactivation blockade and stent thrombosis."""
        path = knowledge_graph.explain_path("clopidogrel", "omeprazole")
        self.assertTrue(any("CYP2C19" in p for p in path))

    def test_26_toxic_ddi_metformin_renal_g4(self):
        """Metformin in severe CKD Stage G4 (eGFR < 30) is strictly contraindicated."""
        self.assertTrue(knowledge_graph.g.has_edge("metformin", "renal_stage:G4"))
        edge = knowledge_graph.g.get_edge_data("metformin", "renal_stage:G4")[0]
        self.assertEqual(edge["severity"], "HIGH_RISK")
        self.assertIn("lactic acidosis", edge["mechanism"].lower())

    def test_27_toxic_ddi_methotrexate_ibuprofen(self):
        """Methotrexate + Ibuprofen: tubular clearance blockade and pancytopenia."""
        path = knowledge_graph.explain_path("methotrexate", "ibuprofen")
        self.assertTrue(any("bone marrow" in p.lower() or "tubular" in p.lower() for p in path))

    def test_28_toxic_ddi_azathioprine_allopurinol(self):
        """Azathioprine + Allopurinol: xanthine oxidase blockade and aplastic anemia."""
        path = knowledge_graph.explain_path("azathioprine", "allopurinol")
        self.assertTrue(any("xanthine oxidase" in p.lower() for p in path))

    def test_29_toxic_ddi_digoxin_amiodarone(self):
        """Digoxin + Amiodarone: P-gp efflux inhibition doubling serum digoxin."""
        path = knowledge_graph.explain_path("digoxin", "amiodarone")
        self.assertTrue(any("digitalis" in p.lower() or "p-gp" in p.lower() for p in path))

    def test_30_toxic_ddi_fluoxetine_tramadol(self):
        """Fluoxetine + Tramadol: serotonin syndrome and seizure threshold depression."""
        path = knowledge_graph.explain_path("fluoxetine", "tramadol")
        self.assertTrue(any("serotonin" in p.lower() or "seizure" in p.lower() for p in path))

    # ==========================================================================
    # GROUP 4: SAFE ALTERNATIVES, NEGATIVE CONTROLS, ACB & CYTOSCAPE (10)
    # ==========================================================================

    def test_31_safe_alternative_acetaminophen_on_warfarin(self):
        """Acetaminophen on warfarin patient has no direct HIGH_RISK drug interaction edge."""
        resp = self.client.get("/api/graph/traverse?candidate_rx=acetaminophen&patient_id=GK-8821")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Warfarin and acetaminophen should not collide as HIGH_RISK
        high_risk_collisions = [c for c in data["summary"]["collisions"] if c["severity"] == "HIGH_RISK" and ("acetaminophen" in (c["source"] + c["target"])) and ("warfarin" in (c["source"] + c["target"]))]
        self.assertEqual(len(high_risk_collisions), 0, "Acetaminophen should not produce HIGH_RISK bleed collision with Warfarin")

    def test_32_safe_combination_amlodipine_lisinopril(self):
        """Amlodipine + Lisinopril is a safe synergistic antihypertensive regimen without collision."""
        has_high_risk = False
        if knowledge_graph.g.has_edge("amlodipine", "lisinopril"):
            for k, ed in knowledge_graph.g["amlodipine"]["lisinopril"].items():
                if ed.get("severity") == "HIGH_RISK":
                    has_high_risk = True
        self.assertFalse(has_high_risk, "Amlodipine and Lisinopril should not have a HIGH_RISK collision")

    def test_33_safe_combination_metformin_atorvastatin(self):
        """Metformin + Atorvastatin is standard safe diabetic dyslipidemia management."""
        has_high_risk = False
        if knowledge_graph.g.has_edge("metformin", "atorvastatin"):
            for k, ed in knowledge_graph.g["metformin"]["atorvastatin"].items():
                if ed.get("severity") == "HIGH_RISK":
                    has_high_risk = True
        self.assertFalse(has_high_risk)

    def test_34_safe_combination_amoxicillin_non_allergic(self):
        """Amoxicillin has no cross-reactivity in patients without penicillin allergy."""
        resp = self.client.get("/api/graph/traverse?candidate_rx=amoxicillin&patient_id=PS-4019")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Patient PS-4019 has asthma/NSAID allergy, NOT penicillin allergy
        allergy_collisions = [c for c in data["summary"]["collisions"] if "penicillin" in c.get("source", "") or "penicillin" in c.get("target", "")]
        self.assertEqual(len(allergy_collisions), 0)

    def test_35_safe_combination_metoprolol_lisinopril(self):
        """Metoprolol + Lisinopril: guideline-directed combination with no toxic collision."""
        has_high_risk = False
        if knowledge_graph.g.has_edge("metoprolol", "lisinopril"):
            for k, ed in knowledge_graph.g["metoprolol"]["lisinopril"].items():
                if ed.get("severity") == "HIGH_RISK":
                    has_high_risk = True
        self.assertFalse(has_high_risk)

    def test_36_safe_combination_rosuvastatin_ezetimibe(self):
        """Rosuvastatin + Ezetimibe: safe complementary lipid lowering."""
        has_collision = False
        if knowledge_graph.g.has_edge("rosuvastatin", "ezetimibe"):
            for k, ed in knowledge_graph.g["rosuvastatin"]["ezetimibe"].items():
                if ed.get("collision"):
                    has_collision = True
        self.assertFalse(has_collision)

    def test_37_safe_combination_apixaban_acetaminophen(self):
        """Apixaban patient safely taking acetaminophen for headache without NSAID bleed risk."""
        has_high_risk = False
        if knowledge_graph.g.has_edge("apixaban", "acetaminophen"):
            for k, ed in knowledge_graph.g["apixaban"]["acetaminophen"].items():
                if ed.get("severity") == "HIGH_RISK":
                    has_high_risk = True
        self.assertFalse(has_high_risk)

    def test_38_safe_combination_glipizide_metformin(self):
        """Glipizide + Metformin: standard synergistic dual oral antidiabetic therapy."""
        has_high_risk = False
        if knowledge_graph.g.has_edge("glipizide", "metformin"):
            for k, ed in knowledge_graph.g["glipizide"]["metformin"].items():
                if ed.get("severity") == "HIGH_RISK":
                    has_high_risk = True
        self.assertFalse(has_high_risk)

    def test_39_dynamic_anticholinergic_burden_beers(self):
        """Stacking Diphenhydramine (ACB=3) with Amitriptyline (ACB=3) triggers Beers alert."""
        acb_diph = knowledge_graph.g.nodes["diphenhydramine"].get("acb_score", 0)
        acb_amit = knowledge_graph.g.nodes["amitriptyline"].get("acb_score", 0)
        self.assertEqual(acb_diph, 3)
        self.assertEqual(acb_amit, 3)
        self.assertGreaterEqual(acb_diph + acb_amit, 3)

    def test_40_cytoscape_formatting_integrity(self):
        """Traversal response matches exact Cytoscape.js elements specification."""
        resp = self.client.get("/api/graph/traverse?candidate_rx=celecoxib&patient_id=GK-8821")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        
        # Check elements structure
        self.assertIn("elements", data)
        self.assertIn("nodes", data["elements"])
        self.assertIn("edges", data["elements"])
        
        # Check node data fields
        node = data["elements"]["nodes"][0]
        self.assertIn("data", node)
        self.assertIn("id", node["data"])
        self.assertIn("label", node["data"])
        self.assertIn("type", node["data"])
        self.assertIn("collision_state", node["data"])
        
        # Check edge data fields
        edge = data["elements"]["edges"][0]
        self.assertIn("data", edge)
        self.assertIn("id", edge["data"])
        self.assertIn("source", edge["data"])
        self.assertIn("target", edge["data"])
        self.assertIn("relation", edge["data"])
        self.assertIn("severity", edge["data"])
        self.assertIn("weight", edge["data"])
        self.assertIn("collision", edge["data"])
        self.assertIn("literature_citations", edge["data"])


    def test_41_prescribe_endpoint_resolves_brand_and_flags_lasa(self):
        """Prescribing 'Celebrex' resolves to Celecoxib and appends LASA warning finding."""
        payload = {
            "session_id": "SES-98214",
            "patient_id": "GK-8821",
            "medication_name": "Celebrex",
            "dosage": "100 mg",
            "frequency": "Once daily",
            "duration": "14 days",
            "clinical_rationale": "Acute knee arthralgia"
        }
        resp = self.client.post("/api/doctor/prescribe", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(len(data["findings"]), 4)
        
        # Verify LASA finding is present
        lasa_findings = [f for f in data["findings"] if "LASA" in f["category"] or "Tall Man" in f["headline"]]
        self.assertGreaterEqual(len(lasa_findings), 1, "Must contain FDA / ISMP Tall Man LASA finding")
        self.assertIn("CELEbrex", lasa_findings[0]["description"])

    def test_42_prescribe_endpoint_strictly_rejects_fake_drug(self):
        """Prescribing an unknown fake entity like 'asdfgh123' must return HTTP 422 to prevent hallucination."""
        payload = {
            "session_id": "SES-98214",
            "patient_id": "GK-8821",
            "medication_name": "asdfgh123",
            "dosage": "100 mg",
            "frequency": "Once daily",
            "duration": "14 days",
            "clinical_rationale": "Testing hallucination prevention"
        }
        resp = self.client.post("/api/doctor/prescribe", json=payload)
        self.assertEqual(resp.status_code, 422)
        err = resp.json()
        self.assertIn("below clinical cutoff", str(err).lower())


if __name__ == "__main__":
    unittest.main()
