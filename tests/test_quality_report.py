import unittest

from scripts.generate_quality_report import build_github_summary, build_quality_report


class QualityReportTests(unittest.TestCase):
    def quote(self, basis="ndw", grade="reliable", completeness="complete", specificity="connector", source_quality="high", missing=None, reasons=None):
        return {
            "kwh": 0.40,
            "session": 0.0,
            "basis": basis,
            "route": "msp_roaming",
            "quality": {
                "source_quality": source_quality,
                "price_specificity": specificity,
                "cost_completeness": completeness,
                "decision_grade": grade,
                "reasons": reasons or [],
                "unmodelled_costs": missing or [],
            },
        }

    def profile(self, source="ndw", rate=0.40, decision="reliable", direct=None, pricing=None):
        return {
            "id": "connector-1",
            "connector_type": "Type 2",
            "current_type": "AC",
            "power_kw": 22.0,
            "count": 1,
            "available_count": 1,
            "statuses": ["AVAILABLE"],
            "evse_ids": ["NL*AAA*1"],
            "last_updated": "2026-08-27T09:30:00Z",
            "tariff": {
                "source": source,
                "rate": rate,
                "quality": {
                    "source_quality": "high" if source != "operator_median" else "low",
                    "price_specificity": "connector" if source == "ndw" else "regional",
                },
            },
            "direct_payment": direct or {"supported": False, "priced": False},
            "pricing": pricing or {
                "anwb_free": self.quote(),
                "tap_light": self.quote(),
            },
            "decision_status": decision,
        }

    def sample_dataset(self):
        old_profile = self.profile()
        new_profile = self.profile(
            source="totalenergies_mrae",
            decision="indicative",
            direct={"supported": True, "priced": True},
            pricing={
                "direct_pay": self.quote("official_cpo_adhoc", "reliable", specificity="network"),
                "tap_light": self.quote("totalenergies_mrae", "indicative", specificity="regional"),
            },
        )
        gap_profile = self.profile(
            source="unknown",
            rate=None,
            decision="insufficient",
            direct={"supported": True, "priced": False},
            pricing={
                "vattenfall": self.quote(
                    "official_cpo_msp_rate", "exclude", "partial", "network", "high",
                    ["Voorbeeld roamingtoeslag onbekend"],
                )
            },
        )
        locations = [
            {
                "id": "old", "name": "Old operator", "address": "Example 1, Huizen", "operator": "Old CPO", "party_id": "OLD",
                "available": False, "statuses": ["UNKNOWN"], "last_updated": "2026-08-01T10:00:00Z",
                "evse_ids": ["NL*OLD*1"], "connector_options": [old_profile], "decision_status": "reliable", "fully_reliable": True,
            },
            {
                "id": "new", "name": "New operator", "address": "Example 1, Huizen", "operator": "New CPO", "party_id": "NEW",
                "available": True, "statuses": ["AVAILABLE"], "last_updated": "2026-08-27T09:30:00Z",
                "evse_ids": ["NL*NEW*1"], "connector_options": [new_profile], "decision_status": "indicative", "fully_reliable": False,
            },
            {
                "id": "gap", "name": "Gap", "address": "Other 2, Huizen", "operator": "Gap CPO", "party_id": "GAP",
                "available": True, "statuses": ["AVAILABLE"], "last_updated": "2026-08-27T08:00:00Z",
                "evse_ids": ["NL*GAP*1"], "connector_options": [gap_profile], "decision_status": "insufficient", "fully_reliable": False,
            },
        ]
        return {
            "schema_version": 5,
            "generated_at": "2026-08-27T10:00:00+00:00",
            "stats": {
                "total": 3, "connector_profiles": 3, "available_snapshot": 2,
                "comparison_ready": 2, "decision_ready": 1, "indicative_only": 1, "insufficient": 1,
                "fully_reliable": 1, "unknown_base_rate": 1, "adhoc_priced": 1,
                "adhoc_priced_ndw": 0, "adhoc_priced_official": 1, "direct_payment_known": 2,
                "msp_quotes_official": 1,
            },
            "pricing_rule_verification": {
                "mode": "daily_verified_fail_closed", "checked_at": "2026-08-27T09:00:00Z", "all_ok": False,
                "disabled_rules": ["vattenfall"],
            },
            "official_pricing_harvest": {
                "sources": [
                    {"id": "source_ok", "party_id": "AAA", "status": "ok"},
                    {"id": "source_bad", "party_id": "BBB", "status": "unavailable", "error": "changed"},
                ]
            },
            "locations": locations,
        }

    def test_builds_v2_decision_coverage(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["coverage"]["total_locations"], 3)
        self.assertEqual(report["coverage"]["reliable_locations"], 1)
        self.assertEqual(report["coverage"]["indicative_locations"], 1)
        self.assertEqual(report["coverage"]["insufficient_locations"], 1)
        self.assertEqual(report["connector_coverage"]["total_profiles"], 3)

    def test_reports_direct_and_base_gaps_by_connector_profile(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["direct_payment"]["known_profiles"], 2)
        self.assertEqual(report["direct_payment"]["priced_profiles"], 1)
        self.assertEqual(report["direct_payment"]["rankable_profiles"], 1)
        self.assertEqual(report["direct_payment"]["excluded_profiles"], 0)
        self.assertEqual(report["direct_payment"]["supported_unpriced_profiles"], 1)
        self.assertEqual(report["direct_payment"]["unpriced_by_operator"][0]["party_id"], "GAP")
        self.assertEqual(report["base_pricing"]["unknown_profiles"], 1)

    def test_data_quality_kpis_are_separate_from_decision_quality(self):
        report = build_quality_report(self.sample_dataset())
        data_quality = report["data_quality"]
        self.assertEqual(data_quality["high_quality_source_profiles"], 3)
        self.assertEqual(data_quality["base_tariff_known_profiles"], 2)
        self.assertEqual(data_quality["direct_priced_profiles"], 1)
        self.assertEqual(data_quality["direct_known_profiles"], 2)
        self.assertEqual(data_quality["complete_price_routes"], 4)
        self.assertEqual(data_quality["total_price_routes"], 5)
        self.assertEqual(data_quality["complete_price_routes_pct"], 80.0)
        self.assertEqual(report["coverage"]["reliable_locations"], 1)

    def test_quality_dimensions_do_not_use_legacy_confidence_as_main_kpi(self):
        report = build_quality_report(self.sample_dataset())
        self.assertNotIn("confidence", report)
        dimensions = report["quality_dimensions"]
        self.assertIn("profile_source_quality", dimensions)
        self.assertIn("profile_price_specificity", dimensions)
        self.assertIn("quote_cost_completeness", dimensions)
        self.assertIn("quote_decision_grade", dimensions)

    def test_reports_unmodelled_cost_blocker(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["decision_blockers"][0]["count"], 1)
        self.assertIn("roamingtoeslag", report["decision_blockers"][0]["reason"].lower())

    def test_blockers_count_connector_profiles_separately_from_quote_occurrences(self):
        dataset = self.sample_dataset()
        profile = dataset["locations"][2]["connector_options"][0]
        profile["pricing"]["second_route"] = self.quote(
            "official_cpo_msp_rate", "exclude", "partial", "network", "high",
            ["Voorbeeld roamingtoeslag onbekend"],
        )
        report = build_quality_report(dataset)
        blocker = next(row for row in report["decision_blockers"] if "roamingtoeslag" in row["reason"].lower())
        self.assertEqual(blocker["connector_profiles"], 1)
        self.assertEqual(blocker["quote_occurrences"], 2)
        self.assertEqual(blocker["count"], 1)

    def test_flags_stale_possible_operator_transition(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["freshness"]["older_than_7d"], 1)
        self.assertEqual(report["potential_duplicates"]["group_count"], 1)
        self.assertEqual(report["potential_duplicates"]["possible_operator_transition_groups"], 1)
        self.assertEqual(report["potential_duplicates"]["groups"][0]["reason"], "possible_operator_transition")
        attention = next(item for item in report["attention"] if item["category"] == "multi_record")
        self.assertIn("geen automatische deduplicatie", attention["title"].lower())

    def test_reports_source_and_rule_failures(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["official_sources"]["failed"], 1)
        self.assertEqual(report["official_sources"]["failed_sources"][0]["id"], "source_bad")
        self.assertEqual(report["pricing_rule_verification"]["disabled_rules"], ["vattenfall"])
        categories = {item["category"] for item in report["attention"]}
        self.assertIn("pricing_rule_verification", categories)
        self.assertIn("official_source_harvest", categories)

    def test_detects_embedded_stats_mismatch(self):
        dataset = self.sample_dataset()
        dataset["stats"]["comparison_ready"] = 99
        report = build_quality_report(dataset)
        self.assertFalse(report["consistency"]["ok"])
        self.assertEqual(report["consistency"]["mismatches"][0]["field"], "comparison_ready")

    def test_reports_explainable_decision_reasons_separately_from_blockers(self):
        dataset = self.sample_dataset()
        dataset["locations"][1]["connector_options"][0]["pricing"]["tap_light"]["quality"]["reasons"] = [
            "specificity_regional", "bounded_price_uncertainty"
        ]
        report = build_quality_report(dataset)
        reasons = {row["reason"]: row["count"] for row in report["decision_reasons"]}
        self.assertEqual(reasons["specificity_regional"], 1)
        self.assertEqual(reasons["bounded_price_uncertainty"], 1)
        self.assertNotIn("specificity_regional", {row["reason"] for row in report["decision_blockers"]})

    def test_github_summary_contains_new_quality_kpis(self):
        summary = build_github_summary(build_quality_report(self.sample_dataset()))
        self.assertIn("Pricing & Data Quality v2", summary)
        self.assertIn("Betrouwbaar vergelijkbare locaties", summary)
        self.assertIn("Direct/QR rangschikbaar", summary)
        self.assertIn("Direct/QR numeriek geprijsd", summary)
        self.assertIn("Connector-/netwerkspecifieke basisprijs", summary)
        self.assertIn("Gap CPO", summary)

    def test_schema4_data_is_never_upgraded_to_reliable(self):
        legacy = {
            "schema_version": 4,
            "generated_at": "2026-08-27T10:00:00+00:00",
            "locations": [{
                "id": "legacy", "address": "A 1, Huizen", "operator": "CPO", "party_id": "AAA",
                "available": True, "last_updated": "2026-08-27T09:00:00Z", "pricing_source": "ndw", "cpo_rate": 0.4,
                "pricing": {
                    "anwb_free": {"kwh": 0.4, "confidence": "high", "basis": "ndw"},
                    "tap_light": {"kwh": 0.4, "confidence": "high", "basis": "ndw"},
                },
            }],
        }
        report = build_quality_report(legacy)
        self.assertTrue(report["legacy_profile_fallback"])
        self.assertEqual(report["coverage"]["reliable_locations"], 0)
        self.assertEqual(report["coverage"]["indicative_locations"], 1)


if __name__ == "__main__":
    unittest.main()
