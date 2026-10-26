"""
tests/test_graph_visualizer_frontend.py
Frontend Canvas Architect Verification Suite (pixel-penguin-ui & motion-magic-moth)
Validates Cytoscape.js, CoSE/Cola physics integration, ray traversal engine,
and Doctor consultation portal visualizer contracts.
"""

import os
import unittest
from fastapi.testclient import TestClient
from app.main import app

class TestGraphVisualizerFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.static_dir = os.path.join(os.path.dirname(__file__), "..", "app", "static")

    def test_01_index_html_includes_canvas_libraries(self):
        """index.html must load Cytoscape.js, WebCola, cytoscape-cola, D3, and graph_visualizer.js."""
        index_path = os.path.join(self.static_dir, "index.html")
        self.assertTrue(os.path.exists(index_path), "index.html must exist")
        with open(index_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("cytoscape/3.28.1/cytoscape.min.js", content, "Cytoscape CDN script must be included")
        self.assertIn("WebCola/cola.min.js", content, "WebCola script must be included")
        self.assertIn("cytoscape-cola", content, "cytoscape-cola extension must be included")
        self.assertIn("d3/7.8.5/d3.min.js", content, "D3.js CDN script must be included")
        self.assertIn("/static/js/graph_visualizer.js", content, "graph_visualizer.js must be loaded")
        self.assertIn("/static/js/app.js", content, "app.js must be loaded")

    def test_02_graph_visualizer_js_architecture(self):
        """graph_visualizer.js must define PharmacologicalGraphVisualizer with core methods."""
        gv_path = os.path.join(self.static_dir, "js", "graph_visualizer.js")
        self.assertTrue(os.path.exists(gv_path), "graph_visualizer.js must exist")
        with open(gv_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Class definition & export
        self.assertIn("class PharmacologicalGraphVisualizer", code)
        self.assertIn("window.PharmacologicalGraphVisualizer", code)

        # Physics & Cytoscape initialization
        self.assertIn("initCytoscape", code)
        self.assertIn("getLayoutConfig", code)
        self.assertIn("cola", code)
        self.assertIn("cose", code)

        # Semantic color taxonomy
        self.assertIn("CANDIDATE_RX", code)
        self.assertIn("#06B6D4", code, "Cyan Candidate Rx color must be defined")
        self.assertIn("#0D9488", code, "Teal active meds color must be defined")
        self.assertIn("#8B5CF6", code, "Purple metabolic enzymes color must be defined")
        self.assertIn("#EF4444", code, "Red contraindications color must be defined")
        self.assertIn("#F59E0B", code, "Amber duplicate pathways color must be defined")

        # Step-by-step Ray Traversal Engine
        self.assertIn("runStepByStepTraversal", code)
        self.assertIn("triggerShockwaveAtNode", code)
        self.assertIn("startContinuousRayTracing", code)

        # Slide-out Evidence Drawer
        self.assertIn("openEvidenceDrawer", code)
        self.assertIn("closeEvidenceDrawer", code)
        self.assertIn("FDA Black Box Warning Alert", code)
        self.assertIn("CPIC & CLINICAL GUIDANCE", code)
        self.assertIn("BIOCHEMICAL MECHANISM", code)

        # Interactive controls
        self.assertIn("filterBySeverity", code)
        self.assertIn("changeLayout", code)
        self.assertIn("toggleTheme", code)
        self.assertIn("searchNodes", code)

    def test_03_custom_css_animation_keyframes(self):
        """custom.css must provide 60fps shockwave, ray beam pulse, and glassmorphism styling."""
        css_path = os.path.join(self.static_dir, "css", "custom.css")
        self.assertTrue(os.path.exists(css_path), "custom.css must exist")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("@keyframes shockwaveExpand", css, "Shockwave keyframes must exist")
        self.assertIn("@keyframes rayBeamPulse", css, "Ray beam glow keyframes must exist")
        self.assertIn("@keyframes shieldAuraGlow", css, "Safety shield glow keyframes must exist")
        self.assertIn("#pgv-evidence-drawer", css, "Evidence drawer CSS rules must exist")
        self.assertIn("prefers-reduced-motion", css, "Reduced motion accessibility rule must exist")

    def test_04_doctor_portal_integration_in_app_js(self):
        """app.js must mount the visualizer in Doctor Consultation workflow."""
        app_path = os.path.join(self.static_dir, "js", "app.js")
        self.assertTrue(os.path.exists(app_path), "app.js must exist")
        with open(app_path, "r", encoding="utf-8") as f:
            app_code = f.read()

        self.assertIn("state.doctorStep === 'graph_view'", app_code, "graph_view route must be supported")
        self.assertIn("renderDoctorGraphView", app_code, "renderDoctorGraphView function must exist")
        self.assertIn("doctor-safety-graph-container", app_code, "Safety report embedded container must exist")
        self.assertIn("doctor-full-graph-container", app_code, "Full-canvas container must exist")
        self.assertIn("PharmacologicalGraphVisualizer", app_code, "Visualizer instantiation must exist in app.js")

    def test_05_api_graph_traversal_contract(self):
        """GET /api/graph/traverse endpoint returns valid Cytoscape schema."""
        resp = self.client.get("/api/graph/traverse?candidate_rx=celecoxib&patient_id=GK-8821")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Elements validation
        self.assertIn("elements", data)
        self.assertIn("nodes", data["elements"])
        self.assertIn("edges", data["elements"])
        self.assertGreaterEqual(len(data["elements"]["nodes"]), 10)
        self.assertGreaterEqual(len(data["elements"]["edges"]), 10)

        # Summary validation
        self.assertIn("summary", data)
        self.assertIn("collisions", data["summary"])
        self.assertIn("er_decision_support", data["summary"])
        self.assertTrue(data["summary"]["collision_detected"])
        self.assertIn("Acetaminophen", " ".join(data["summary"]["er_decision_support"]["safe_alternatives"]))

if __name__ == "__main__":
    unittest.main()
