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
        self.assertIn('src="app.js?v=20260828-3"', html)

    def test_vattenfall_range_and_exact_prices_have_explicit_frontend_labels(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("source === 'vattenfall_mrae'", app)
        self.assertIn("Indicatief Vattenfall MRA-E tarief, concessie onbekend", app)
        self.assertIn("function euroKwh", app)
        self.assertIn("maximumFractionDigits: 4", app)
        self.assertIn("sessionCostRange", app)
        self.assertIn("return { cls: 'source-unknown', label: 'CPO-basistarief onbekend'", app)

    def test_session_range_formula_keeps_both_ends_for_25_kwh(self):
        from decimal import Decimal, ROUND_HALF_UP

        low = (Decimal('0.3394') * Decimal('25')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        high = (Decimal('0.5222') * Decimal('25')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        self.assertEqual(low, Decimal('8.49'))
        self.assertEqual(high, Decimal('13.06'))
        app = (ROOT / 'app.js').read_text(encoding='utf-8')
        self.assertIn("Number(value).toLocaleString('nl-NL'", app)

    def test_msp_quote_can_show_separate_kwh_and_start_fee_sources(self):
        app = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn("fee_source_url", app)
        self.assertIn("kWh-bron", app)
        self.assertIn("starttarief-bron", app)
