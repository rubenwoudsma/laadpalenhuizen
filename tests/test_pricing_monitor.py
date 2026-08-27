import json
import unittest
from pathlib import Path

from scripts import check_pricing_sources as monitor


ROOT = Path(__file__).resolve().parents[1]


class PricingMonitorTest(unittest.TestCase):
    def test_config_is_valid(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(len(config["sources"]), 10)
        self.assertIn("tap_light", {source["id"] for source in config["sources"]})

    def test_normalize_page_removes_markup_and_whitespace(self):
        text = monitor.normalize_page("<h1>Tap&nbsp;Electric</h1>\n<p>Light   +5% transactiekosten</p>")
        self.assertEqual(text, "tap electric light +5% transactiekosten")

    def test_normalize_page_ignores_script_style_and_template_payloads(self):
        text = monitor.normalize_page(
            "<p>Shell Recharge Basic</p>"
            "<script>" + ("generated " * 1000) + "</script>"
            "<style>.hidden { display:none }</style>"
            "<template>duplicate tariff content</template>"
            "<p>Geen maandelijkse kosten</p>"
        )
        self.assertEqual(text, "shell recharge basic geen maandelijkse kosten")

    def test_current_reference_snippets_match_all_configured_checks(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        snippets = {
            "anwb_free": "Gratis laadpas zonder abonnement. Wel betaal je per laadsessie een starttarief van € 0,89.",
            "tap_light": "Light De beste keuze. € 0.00 /maand Je betaalt het tarief van de laadpaal +5% transactiekosten per sessie.",
            "vattenfall": "Voor onze gratis laadpas betaal je geen abonnementskosten. Je betaalt een starttarief van €0,35 als je laadt bij laadpalen die niet van ons zijn.",
            "eflux_flex": "Flex Gratis. €0,31 per laadsessie. €0,024/kWh toeslag op sessies bij niet-E-Flux laadpunten.",
            "shell_basic": "Shell Recharge Basic Geen maandelijkse kosten. DC: € 0,79 / kWh - € 0,82 / kWh - € 0,85 / kWh. AC: € 0,50 / kWh - € 0,55 / kWh - € 0,60 / kWh. € 0,35 transactiekosten per laadsessie.",
            "laadkompas_free": "Laadpas zonder abonnement. Het tarief is € 0,47 per laadsessie.",
            "totalenergies_mrae": "Provincies Flevoland, Noord-Holland en Utrecht MRA-E 2 t/m 5 €0,40 €0,48. MRA-E 6 €0,30 €0,36. MRA-E 6 - Dynamische tarieven €0,34 €0,36. Snelladers DC Provincies Flevoland, Noord-Holland en Utrecht (MRA-E) €0,45 €0,54.",
            "ubitricity_mrae_direct": "Ad Hoc Opladen via QR-code op scherm. Per kWh 0,35€. RFID / Apps Per kWh: ANWB, Greenchoice, Tap Electric, Essent, MoveMove, Green Caravan, Eneco, Shell Recharge (App), Vattenfall Incharge, MKB Brandstof.",
            "totalenergies_direct_payment": "Het laadtarief bestaat uit een basisprijs (CPO-prijs), dit is ook de ad-hoc of direct payment prijs. De extra toeslag geldt niet bij betaling met een gewone betaal/creditkaart (Direct Payment, ook wel Ad-Hoc).",
            "vattenfall_direct_support": "Met je betaalpas opladen: scan de QR-code en vul je betaalgegevens in. Je kan dit gebruiken op onze openbare laadpalen zolang ze een sticker met QR-code hebben.",
        }
        for source in config["sources"]:
            with self.subTest(source=source["id"]):
                normalized = monitor.normalize_page(f"<p>{snippets[source['id']]}</p>")
                self.assertEqual(monitor.evaluate_source(source, normalized), [])

    def test_current_live_wording_for_previous_false_positives(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        by_id = {source["id"]: source for source in config["sources"]}
        live_snippets = {
            "vattenfall": "Voor onze laadpas betaal je geen abonnementskosten. Je betaalt alleen voor het opladen zelf en een starttarief van €0,35 als je laadt bij laadpalen die niet van ons zijn.",
            "shell_basic": "Shell Recharge Basic Geen maandelijkse kosten. Laden bij andere aanbieders DC: € 0,79 / kWh - € 0,82 / kWh - € 0,85 / kWh AC: € 0,50 / kWh - € 0,55 / kWh - € 0,60 / kWh. Goed om te weten € 0,35 transactiekosten per laadsessie.",
            "laadkompas_free": "Geen abonnementskosten: je betaalt alleen wanneer je oplaadt. Scherp laadtarief van € 0,47, plus het tarief per kWh van de betreffende laadpaal. Het starttarief van € 0,47 komt bij een abonnement te vervallen.",
        }
        for source_id, snippet in live_snippets.items():
            with self.subTest(source=source_id):
                self.assertEqual(monitor.evaluate_source(by_id[source_id], monitor.normalize_page(snippet)), [])

    def test_workflow_handles_disabled_issues_and_uses_current_actions(self):
        workflow = (ROOT / ".github" / "workflows" / "pricing-monitor.yml").read_text(encoding="utf-8")
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/setup-python@v7", workflow)
        self.assertIn("actions/github-script@v9", workflow)
        self.assertIn("repo.has_issues", workflow)
        self.assertIn("Job Summary", workflow)

    def test_mismatch_is_reported(self):
        source = {
            "checks": [{"label": "expected fee", "patterns": [r"0[,.]89"]}],
        }
        self.assertEqual(monitor.evaluate_source(source, "starttarief 1,25"), ["expected fee"])


if __name__ == "__main__":
    unittest.main()
