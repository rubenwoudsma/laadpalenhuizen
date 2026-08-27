import unittest

from scripts.generate_quality_report import build_quality_report, markdown_summary


class QualityReportTests(unittest.TestCase):
    def sample_dataset(self):
        return {
            "generated_at": "2026-08-27T10:00:00+00:00",
            "stats": {
                "comparison_ready": 2,
                "available_snapshot": 2,
                "unknown_base_rate": 1,
                "adhoc_priced_ndw": 0,
                "adhoc_priced_official": 1,
            },
            "official_pricing_harvest": {
                "sources": [
                    {"id": "source_ok", "party_id": "AAA", "status": "ok"},
                    {"id": "source_bad", "party_id": "BBB", "status": "unavailable", "error": "changed"},
                ]
            },
            "locations": [
                {
                    "id": "old",
                    "name": "Old operator",
                    "address": "Example 1, Huizen",
                    "operator": "Old CPO",
                    "party_id": "OLD",
                    "available": False,
                    "statuses": ["UNKNOWN"],
                    "last_updated": "2026-08-01T10:00:00Z",
                    "direct_payment": {"supported": False, "priced": False},
                    "pricing_source": "ndw",
                    "cpo_rate": 0.40,
                    "pricing": {
                        "anwb_free": {"confidence": "high", "basis": "ndw", "route": "msp_roaming"},
                        "tap_light": {"confidence": "medium", "basis": "ndw", "route": "msp_roaming"},
                    },
                    "evse_ids": ["NL*OLD*1"],
                },
                {
                    "id": "new",
                    "name": "New operator",
                    "address": "Example 1, Huizen",
                    "operator": "New CPO",
                    "party_id": "NEW",
                    "available": True,
                    "statuses": ["AVAILABLE"],
                    "last_updated": "2026-08-27T09:30:00Z",
                    "direct_payment": {"supported": True, "priced": True},
                    "pricing_source": "regional",
                    "cpo_rate": 0.35,
                    "pricing": {
                        "direct_pay": {"confidence": "high", "basis": "official_cpo_adhoc", "route": "ad_hoc"},
                        "tap_light": {"confidence": "low", "basis": "regional", "route": "msp_roaming"},
                    },
                    "evse_ids": ["NL*NEW*1"],
                },
                {
                    "id": "gap",
                    "name": "Gap",
                    "address": "Other 2, Huizen",
                    "operator": "Gap CPO",
                    "party_id": "GAP",
                    "available": True,
                    "statuses": ["AVAILABLE"],
                    "last_updated": "2026-08-27T08:00:00Z",
                    "direct_payment": {"supported": True, "priced": False},
                    "pricing_source": "unknown",
                    "cpo_rate": None,
                    "pricing": {"shell_basic": {"confidence": "low", "basis": "published_band", "route": "msp_roaming"}},
                    "evse_ids": ["NL*GAP*1"],
                },
            ],
        }

    def test_builds_core_coverage_metrics(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["coverage"]["total_locations"], 3)
        self.assertEqual(report["coverage"]["comparison_ready"], 2)
        self.assertEqual(report["coverage"]["comparison_ready_pct"], 66.7)
        self.assertEqual(report["coverage"]["unknown_base_tariff"], 1)

    def test_reports_direct_payment_gap_by_operator(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["direct_payment"]["known"], 2)
        self.assertEqual(report["direct_payment"]["priced"], 1)
        self.assertEqual(report["direct_payment"]["unpriced"], 1)
        row = report["direct_payment"]["unpriced_by_operator"][0]
        self.assertEqual(row["party_id"], "GAP")
        self.assertEqual(row["count"], 1)

    def test_flags_stale_possible_operator_transition(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["freshness"]["older_than_7d"], 1)
        self.assertEqual(report["potential_duplicates"]["group_count"], 1)
        self.assertEqual(report["potential_duplicates"]["possible_operator_transition_groups"], 1)
        self.assertEqual(report["potential_duplicates"]["groups"][0]["reason"], "possible_operator_transition")

    def test_counts_quote_confidence(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["confidence"]["total_quotes"], 5)
        self.assertEqual(report["confidence"]["high"], 2)
        self.assertEqual(report["confidence"]["medium"], 1)
        self.assertEqual(report["confidence"]["low"], 2)

    def test_reports_failed_official_sources(self):
        report = build_quality_report(self.sample_dataset())
        self.assertEqual(report["official_sources"]["failed"], 1)
        self.assertEqual(report["official_sources"]["failures"][0]["id"], "source_bad")
        self.assertEqual(report["attention"][0]["severity"], "high")


    def test_detects_embedded_stats_mismatch(self):
        dataset = self.sample_dataset()
        dataset["stats"]["comparison_ready"] = 99
        report = build_quality_report(dataset)
        self.assertEqual(report["consistency"]["mismatch_count"], 1)
        self.assertEqual(report["consistency"]["mismatches"][0]["field"], "comparison_ready")
        self.assertEqual(report["attention"][0]["category"], "dataset_consistency")

    def test_markdown_summary_contains_key_kpis(self):
        report = build_quality_report(self.sample_dataset())
        summary = markdown_summary(report)
        self.assertIn("Pricing & data quality", summary)
        self.assertIn("Direct / QR geprijsd", summary)
        self.assertIn("Gap CPO", summary)
        self.assertIn("pricing-quality.json", summary)


if __name__ == "__main__":
    unittest.main()
