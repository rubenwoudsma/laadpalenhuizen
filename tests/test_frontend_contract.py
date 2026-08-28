import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTest(unittest.TestCase):
    def test_schema5_frontend_refuses_excluded_or_partial_quotes(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("quality.decision_grade === 'exclude'", app)
        self.assertIn("quality.cost_completeness === 'partial'", app)
        self.assertIn("Laagste indicatie", app)
        self.assertNotIn("Top 3 groen", app)

    def test_app_script_is_versioned_to_avoid_old_frontend_with_new_json(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="app.js?v=20260828"', html)
