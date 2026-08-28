import json
import unittest
from pathlib import Path

from scripts import check_pricing_sources as monitor


ROOT = Path(__file__).resolve().parents[1]


class PricingMonitorTest(unittest.TestCase):
    def test_config_is_valid(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(len(config["sources"]), 13)
        self.assertIn("tap_light", {source["id"] for source in config["sources"]})
        self.assertIn("vattenfall_mrae", {source["id"] for source in config["sources"]})
        self.assertIn("laadwerk_vattenfall_context", {source["id"] for source in config["sources"]})

    def test_laadkompas_uses_canonical_source_without_campaign_fallback(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        source = next(item for item in config["sources"] if item["id"] == "laadkompas_free")
        self.assertEqual(monitor.source_urls(source), ["https://laadkompas.nl/laadpas/zonder-abonnement/"])

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
            "anwb_free": "Gratis laadpas zonder abonnement. Wel betaal je per laadsessie een starttarief van € 0,89. Profiteer van korting bij Ionity, Total Energies, Ubitricity en Equans.",
            "tap_light": "Light De beste keuze. € 0.00 /maand Je betaalt het tarief van de laadpaal +5% transactiekosten per sessie.",
            "vattenfall": "Voor onze gratis laadpas betaal je geen abonnementskosten. Je betaalt een starttarief als je laadt bij laadpalen die niet van ons zijn.",
            "vattenfall_mrae": "Openbare laadpalen in Noordwest-Nederland. Vattenfall InCharge in Metropoolregio Amsterdam (MRA 2021) €0,5222 NVT. Vattenfall InCharge in Metropoolregio Amsterdam (MRA 2024) €0,3594 €0,3394. Andere openbare laadpalen.",
            "laadwerk_vattenfall_context": "Wat kost laden op een laadpaal van Laadwerk? Nieuwe laadpalen, geplaatst vanaf 1 juli 2024: Vattenfall InCharge: €0,36. Laadpalen geplaatst vóór 1 juli 2024: bij deze laadpalen geldt het oude tarief, óók als ze vervangen worden door een nieuwe laadpaal met een digitaal scherm: Vattenfall InCharge: €0,52. Op laadkaart.laadwerk.nl kunt u de prijs checken. Dit is de meest accurate informatie. Bewonersvragen & bezwaar.",
            "eflux_flex": "Flex Gratis. €0,31 per laadsessie. €0,024/kWh toeslag op sessies bij niet-E-Flux laadpunten. Er geldt een extra toeslag van €0,48 per sessie bij Hubject, Gireve of e-clearing.",
            "shell_basic": "Shell Recharge Basic Geen maandelijkse kosten. Laden bij Shell Recharge Snelladen € 0,78 / kWh. Laden bij andere aanbieders DC: € 0,79 / kWh - € 0,82 / kWh - € 0,85 / kWh. AC: € 0,50 / kWh - € 0,55 / kWh - € 0,60 / kWh. € 0,35 transactiekosten per laadsessie. Eventuele extra kosten, waaronder blokkeerkosten, verschillen per aanbieder of laadpunt en staan in de Shell Recharge App.",
            "laadkompas_free": "Laadpas zonder abonnement. Het tarief is € 0,47 per laadsessie.",
            "totalenergies_mrae": "Provincies Flevoland, Noord-Holland en Utrecht MRA-E 2 t/m 5 €0,40 €0,48. MRA-E 6 €0,30 €0,36. MRA-E 6 - Dynamische tarieven €0,34 €0,36. Snelladers DC Provincies Flevoland, Noord-Holland en Utrecht (MRA-E) €0,45 €0,54.",
            "ubitricity_mrae_direct": "Ad Hoc Opladen via QR-code op scherm. Per kWh 0,35€. RFID / Apps Per kWh: ANWB, Greenchoice, Tap Electric, Essent, MoveMove, Green Caravan, Eneco, Shell Recharge (App), Vattenfall Incharge, MKB Brandstof.",
            "totalenergies_direct_payment": "Het laadtarief bestaat uit een basisprijs (CPO-prijs), dit is ook de ad-hoc of direct payment prijs. De extra toeslag geldt niet bij betaling met een gewone betaal/creditkaart (Direct Payment, ook wel Ad-Hoc).",
            "lidl_direct_payment": "Opladen via Lidl.nl. Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC). Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC).",
            "lidl_cpo_tariff": "Opladen met eigen laadpas. Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC). Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC). Let op: De abonnementskosten van uw laadpas aanbieder zijn van toepassing.",
        }
        for source in config["sources"]:
            with self.subTest(source=source["id"]):
                normalized = monitor.normalize_page(f"<p>{snippets[source['id']]}</p>")
                self.assertEqual(monitor.evaluate_source(source, normalized), [])

    def test_vattenfall_mrae_monitor_fails_closed_when_official_rate_changes(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        source = next(item for item in config["sources"] if item["id"] == "vattenfall_mrae")
        page = monitor.normalize_page(
            "Openbare laadpalen in Noordwest-Nederland. "
            "Vattenfall InCharge in Metropoolregio Amsterdam (MRA 2021) €0,6000 NVT. "
            "Vattenfall InCharge in Metropoolregio Amsterdam (MRA 2024) €0,3594 €0,3394. "
            "Andere openbare laadpalen."
        )
        missing = monitor.evaluate_source(source, page)
        self.assertIn("MRA 2021 blijft EUR 0,5222 per kWh", missing)

    def test_laadwerk_vattenfall_monitor_fails_closed_when_replacement_warning_disappears(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        source = next(item for item in config["sources"] if item["id"] == "laadwerk_vattenfall_context")
        page = monitor.normalize_page(
            "Wat kost laden op een laadpaal van Laadwerk? Nieuwe laadpalen, geplaatst vanaf 1 juli 2024: "
            "Vattenfall InCharge: €0,36. Laadpalen geplaatst vóór 1 juli 2024: Vattenfall InCharge: €0,52. "
            "Op laadkaart.laadwerk.nl kunt u de prijs checken. Dit is de meest accurate informatie. "
            "Bewonersvragen & bezwaar."
        )
        missing = monitor.evaluate_source(source, page)
        self.assertIn("fysieke vervanging wijzigt het oude tarief niet automatisch", missing)

    def test_lidl_checks_are_scoped_to_the_intended_page_sections(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        by_id = {source["id"]: source for source in config["sources"]}
        page = monitor.normalize_page(
            "<h2>Opladen via Lidl.nl</h2><p>Betaal direct.</p>"
            "<h2>Opladen met eigen laadpas</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
            "<p>De abonnementskosten van uw laadpas aanbieder zijn van toepassing.</p>"
        )
        self.assertEqual(len(monitor.evaluate_source(by_id["lidl_direct_payment"], page)), 2)
        self.assertEqual(monitor.evaluate_source(by_id["lidl_cpo_tariff"], page), [])

    def test_lidl_cpo_monitor_requires_provider_cost_qualifier(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        source = next(item for item in config["sources"] if item["id"] == "lidl_cpo_tariff")
        page = monitor.normalize_page(
            "<h2>Opladen met eigen laadpas</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
        )
        self.assertEqual(
            monitor.evaluate_source(source, page),
            ["Lidl vermeldt dat kosten van de laadpasaanbieder aanvullend van toepassing kunnen zijn"],
        )

    def test_current_live_wording_for_previous_false_positives(self):
        config = monitor.load_config(ROOT / "pricing-sources.json")
        by_id = {source["id"]: source for source in config["sources"]}
        live_snippets = {
            "vattenfall": "Voor onze laadpas betaal je geen abonnementskosten. Je betaalt alleen voor het opladen zelf en een starttarief als je laadt bij laadpalen die niet van ons zijn.",
            "shell_basic": "Shell Recharge Basic Geen maandelijkse kosten. Laden bij Shell Recharge Snelladen € 0,78 / kWh. Laden bij andere aanbieders DC: € 0,79 / kWh - € 0,82 / kWh - € 0,85 / kWh AC: € 0,50 / kWh - € 0,55 / kWh - € 0,60 / kWh. Goed om te weten € 0,35 transactiekosten per laadsessie. Eventuele extra kosten en blokkeerkosten verschillen per aanbieder of laadpunt en staan in de Shell Recharge App.",
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

    def test_live_official_fallback_url_can_verify_same_rule(self):
        source = {
            "id": "example",
            "provider": "Example",
            "plan": "Free",
            "url": "https://official.example/primary",
            "urls": ["https://official.example/fallback"],
            "checks": [{"label": "fee", "patterns": [r"0[,.]47"]}],
        }
        calls = []

        def fetcher(url):
            calls.append(url)
            if url.endswith("primary"):
                raise RuntimeError("temporary upstream error")
            return "<p>Starttarief € 0,47 per sessie</p>"

        results, ok = monitor.run({"sources": [source]}, fetcher=fetcher)
        self.assertTrue(ok)
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["verified_url"], "https://official.example/fallback")
        self.assertEqual(calls, ["https://official.example/primary", "https://official.example/fallback"])

    def test_primary_semantic_mismatch_is_not_masked_by_fallback(self):
        source = {
            "id": "example",
            "provider": "Example",
            "plan": "Free",
            "url": "https://official.example/primary",
            "urls": ["https://official.example/fallback"],
            "checks": [{"label": "fee", "patterns": [r"0[,.]47"]}],
        }
        calls = []

        def fetcher(url):
            calls.append(url)
            if url.endswith("primary"):
                return "<p>Nieuw tarief € 0,99</p>"
            return "<p>Oude campagnepagina: € 0,47</p>"

        results, ok = monitor.run({"sources": [source]}, fetcher=fetcher)
        self.assertFalse(ok)
        self.assertEqual(results[0]["status"], "mismatch")
        self.assertEqual(results[0]["missing"], ["fee"])
        self.assertEqual(calls, ["https://official.example/primary"])

    def test_fallback_mismatch_fails_closed_after_primary_fetch_error(self):
        source = {
            "id": "example",
            "provider": "Example",
            "plan": "Free",
            "url": "https://official.example/primary",
            "urls": ["https://official.example/fallback"],
            "checks": [{"label": "fee", "patterns": [r"0[,.]47"]}],
        }

        def fetcher(url):
            if url.endswith("primary"):
                raise RuntimeError("temporary upstream error")
            return "<p>Tarief € 0,99</p>"

        results, ok = monitor.run({"sources": [source]}, fetcher=fetcher)
        self.assertFalse(ok)
        self.assertEqual(results[0]["status"], "mismatch")
        self.assertEqual(results[0]["missing"], ["fee"])

    def test_mismatch_is_reported(self):
        source = {
            "checks": [{"label": "expected fee", "patterns": [r"0[,.]89"]}],
        }
        self.assertEqual(monitor.evaluate_source(source, "starttarief 1,25"), ["expected fee"])

    def test_status_json_lists_enabled_and_disabled_rules(self):
        results = [
            {"id": "anwb_free", "status": "ok"},
            {"id": "vattenfall", "status": "mismatch"},
        ]
        status = monitor.build_status(results, "2026-08-27", checked_at="2026-08-27T12:00:00+00:00")
        self.assertFalse(status["all_ok"])
        self.assertEqual(status["enabled_rule_ids"], ["anwb_free"])
        self.assertEqual(status["disabled_rule_ids"], ["vattenfall"])

    def test_daily_update_workflow_verifies_pricing_rules_fail_closed(self):
        workflow = (ROOT / ".github" / "workflows" / "update.yml").read_text(encoding="utf-8")
        self.assertIn("--json-status pricing-source-status.json", workflow)
        self.assertIn("--allow-failures", workflow)
        self.assertIn("Run preprocessor", workflow)



if __name__ == "__main__":
    unittest.main()
