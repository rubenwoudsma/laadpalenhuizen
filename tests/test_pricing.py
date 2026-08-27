import unittest

import process


class PricingRulesTest(unittest.TestCase):
    def test_unknown_cpo_does_not_create_false_comparison(self):
        pricing = process.build_pricing(None, "unknown", "50five", 22)
        self.assertEqual(set(pricing), {"shell_basic"})
        self.assertEqual(pricing["shell_basic"]["confidence"], "low")

    def test_anwb_free_uses_cpo_plus_session_fee(self):
        quote = process.build_pricing(0.40, "ndw", "50five", 22)["anwb_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["session"], 0.89)
        self.assertEqual(quote["confidence"], "high")

    def test_anwb_discount_network_is_not_invented(self):
        quote = process.build_pricing(0.40, "ndw", "TotalEnergies", 22)["anwb_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["confidence"], "medium")
        self.assertIn("korting", quote["note"].lower())

    def test_tap_light_uses_cpo_plus_five_percent_transaction_fee(self):
        quote = process.build_pricing(0.40, "ndw", "TotalEnergies", 22)["tap_light"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["session"], 0.0)
        self.assertEqual(quote["percentage"], 0.05)
        self.assertEqual(quote["confidence"], "high")

    def test_tap_light_preserves_cpo_range_for_frontend_percentage_calculation(self):
        quote = process.build_pricing(
            0.41,
            "totalenergies_mrae",
            "TotalEnergies",
            22,
            cpo_rate_range=[0.34, 0.48],
        )["tap_light"]
        self.assertEqual(quote["range"], [0.34, 0.48])
        self.assertEqual(quote["percentage"], 0.05)
        self.assertEqual(quote["confidence"], "low")

    def test_vattenfall_own_network_has_no_session_fee(self):
        quote = process.build_pricing(0.42, "ndw", "Vattenfall InCharge", 22)["vattenfall"]
        self.assertEqual(quote["session"], 0.0)
        self.assertEqual(quote["confidence"], "high")

    def test_vattenfall_roaming_has_session_fee_and_lower_confidence(self):
        quote = process.build_pricing(0.42, "ndw", "Ubitricity", 22)["vattenfall"]
        self.assertEqual(quote["session"], 0.35)
        self.assertEqual(quote["confidence"], "medium")

    def test_eflux_flex_own_network_has_no_kwh_markup(self):
        quote = process.build_pricing(0.45, "ndw", "E-Flux by Road", 22)["eflux_flex"]
        self.assertEqual(quote["kwh"], 0.45)
        self.assertEqual(quote["session"], 0.31)

    def test_eflux_flex_roaming_adds_kwh_markup(self):
        quote = process.build_pricing(0.45, "ndw", "Ubitricity", 22)["eflux_flex"]
        self.assertEqual(quote["kwh"], 0.474)
        self.assertEqual(quote["session"], 0.31)
        self.assertIn("0,48", quote["note"])

    def test_shell_ac_price_band_is_explicit_estimate(self):
        quote = process.build_pricing(0.40, "ndw", "Ubitricity", 22)["shell_basic"]
        self.assertEqual(quote["kwh"], 0.55)
        self.assertEqual(quote["session"], 0.35)
        self.assertEqual(quote["range"], [0.5, 0.6])
        self.assertEqual(quote["confidence"], "low")

    def test_shell_dc_uses_dc_band(self):
        quote = process.build_pricing(0.55, "ndw", "Fastcharge", 150)["shell_basic"]
        self.assertEqual(quote["kwh"], 0.82)
        self.assertEqual(quote["range"], [0.79, 0.85])

    def test_laadkompas_free_uses_cpo_plus_session_fee(self):
        quote = process.build_pricing(0.40, "ndw", "50five", 22)["laadkompas_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["session"], 0.47)

    def test_tariff_lookup_uses_ocpi_party_scope(self):
        tariffs = [
            {
                "country_code": "NL", "party_id": "AAA", "id": "shared",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.30}]}],
            },
            {
                "country_code": "NL", "party_id": "BBB", "id": "shared",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.60}]}],
            },
        ]
        index = process.build_tariff_index(tariffs)
        self.assertEqual(process.get_cpo_rate("shared", index, "NL", "AAA"), 0.30)
        self.assertEqual(process.get_cpo_rate("shared", index, "NL", "BBB"), 0.60)
        self.assertIsNone(process.get_cpo_rate("shared", index))

    def test_multi_component_ndw_tariff_becomes_range(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "dynamic",
            "elements": [
                {"price_components": [{"type": "ENERGY", "price": 0.30}]},
                {"price_components": [{"type": "ENERGY", "price": 0.50}]},
            ],
        }]
        info = process.get_cpo_price_info("dynamic", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertEqual(info["rate"], 0.40)
        self.assertEqual(info["range"], [0.30, 0.50])

    def test_totalenergies_mrae_ac_fallback_is_range(self):
        fallback = process.totalenergies_mrae_fallback("TotalEnergies   ", 17)
        self.assertEqual(fallback["source"], "totalenergies_mrae")
        self.assertEqual(fallback["rate"], 0.41)
        self.assertEqual(fallback["range"], [0.34, 0.48])

    def test_totalenergies_mrae_dc_fallback_is_exact_regional_rate(self):
        fallback = process.totalenergies_mrae_fallback("TotalEnergies", 150)
        self.assertEqual(fallback["source"], "totalenergies_mrae_dc")
        self.assertEqual(fallback["rate"], 0.54)
        self.assertIsNone(fallback["range"])

    def test_totalenergies_is_excluded_from_operator_median(self):
        self.assertIsNone(process.find_operator_median("TotalEnergies", {"totalenergies": 0.42}))

    def test_mrae_range_is_propagated_to_charge_pass_quotes(self):
        pricing = process.build_pricing(
            0.41,
            "totalenergies_mrae",
            "TotalEnergies",
            17,
            cpo_rate_range=[0.34, 0.48],
            cpo_note="MRA-E range",
        )
        self.assertEqual(pricing["anwb_free"]["range"], [0.34, 0.48])
        self.assertEqual(pricing["eflux_flex"]["range"], [0.364, 0.504])
        self.assertEqual(pricing["laadkompas_free"]["range"], [0.34, 0.48])
        self.assertEqual(pricing["anwb_free"]["confidence"], "low")
        self.assertEqual(pricing["laadkompas_free"]["confidence"], "low")

    def test_totalenergies_direct_ndw_tariff_takes_precedence_over_mrae(self):
        tariffs = [{
            "country_code": "NL", "party_id": "TEN", "id": "direct",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.37}]}],
        }]
        loc = {
            "id": "te-direct",
            "country_code": "NL",
            "party_id": "TEN",
            "coordinates": {"latitude": "52.29", "longitude": "5.24"},
            "operator": {"name": "TotalEnergies"},
            "address": "Teststraat 2",
            "city": "Huizen",
            "evses": [{
                "status": "AVAILABLE",
                "connectors": [{
                    "standard": "IEC_62196_T2",
                    "max_electric_power": 17000,
                    "tariff_ids": ["direct"],
                }],
            }],
        }
        result = process.process_location(loc, process.build_tariff_index(tariffs), {})
        self.assertEqual(result["pricing_source"], "ndw")
        self.assertEqual(result["cpo_rate"], 0.37)
        self.assertIsNone(result["cpo_rate_range"])

    def test_process_location_uses_mrae_fallback_after_missing_ndw_tariff(self):
        loc = {
            "id": "te-huizen",
            "country_code": "NL",
            "party_id": "TEN",
            "coordinates": {"latitude": "52.29", "longitude": "5.24"},
            "operator": {"name": "TotalEnergies  "},
            "address": "Teststraat 1",
            "city": "Huizen",
            "evses": [{
                "status": "AVAILABLE",
                "connectors": [{
                    "standard": "IEC_62196_T2",
                    "max_electric_power": 17000,
                    "tariff_ids": ["missing"],
                }],
            }],
        }
        result = process.process_location(loc, process.build_tariff_index([]), {})
        self.assertEqual(result["pricing_source"], "totalenergies_mrae")
        self.assertEqual(result["cpo_rate_range"], [0.34, 0.48])
        self.assertEqual(result["pricing"]["anwb_free"]["range"], [0.34, 0.48])


    def test_ad_hoc_tariff_is_separate_from_regular_tariff_and_keeps_flat_fee(self):
        tariffs = [
            {
                "country_code": "NL", "party_id": "UB2", "id": "regular",
                "type": "REGULAR",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}],
            },
            {
                "country_code": "NL", "party_id": "UB2", "id": "adhoc",
                "type": "AD_HOC_PAYMENT",
                "elements": [{"price_components": [
                    {"type": "ENERGY", "price": 0.35},
                    {"type": "FLAT", "price": 0.45},
                ]}],
            },
        ]
        index = process.build_tariff_index(tariffs)
        self.assertEqual(process.get_cpo_price_info("regular", index, "NL", "UB2")["rate"], 0.40)
        self.assertIsNone(process.get_cpo_price_info("adhoc", index, "NL", "UB2"))
        adhoc = process.get_ad_hoc_price_info("adhoc", index, "NL", "UB2")
        self.assertEqual(adhoc["rate"], 0.35)
        self.assertEqual(adhoc["session"], 0.45)
        self.assertEqual(adhoc["tariff_type"], "AD_HOC_PAYMENT")

    def test_explicit_ad_hoc_tariff_becomes_direct_payment_quote(self):
        pricing = process.build_pricing(
            0.40,
            "ndw",
            "Ubitricity",
            22,
            party_id="UB2",
            direct_price_info={
                "rate": 0.35,
                "range": None,
                "session": 0.45,
                "session_range": None,
                "unmodelled_types": [],
            },
        )
        quote = pricing["direct_pay"]
        self.assertEqual(quote["kwh"], 0.35)
        self.assertEqual(quote["session"], 0.45)
        self.assertEqual(quote["route"], "ad_hoc")
        self.assertEqual(quote["relation"], "cpo_direct")

    def test_party_id_controls_home_network_matching(self):
        self.assertTrue(process.is_msp_home_network("vattenfall", "Other label", "NUO"))
        self.assertTrue(process.is_msp_home_network("eflux_flex", "Other label", "EFL"))
        self.assertTrue(process.is_msp_home_network("shell_basic", "Other label", "TNM"))
        self.assertFalse(process.is_msp_home_network("shell_basic", "Shell / Ubitricity", "UB2"))

    def test_shell_does_not_treat_ubitricity_party_as_home_network(self):
        quote = process.build_pricing(
            0.35,
            "ndw",
            "Shell Ubitricity",
            22,
            party_id="UB2",
        )["shell_basic"]
        self.assertEqual(quote["route"], "msp_roaming")
        self.assertEqual(quote["range"], [0.50, 0.60])
        self.assertEqual(quote["confidence"], "low")

    def test_process_location_exposes_party_evse_id_and_direct_payment_metadata(self):
        tariffs = [
            {
                "country_code": "NL", "party_id": "UB2", "id": "regular",
                "type": "REGULAR",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}],
            },
            {
                "country_code": "NL", "party_id": "UB2", "id": "adhoc",
                "type": "AD_HOC_PAYMENT",
                "elements": [{"price_components": [
                    {"type": "ENERGY", "price": 0.36},
                    {"type": "FLAT", "price": 0.35},
                ]}],
            },
        ]
        loc = {
            "id": "ub2-huizen",
            "country_code": "NL",
            "party_id": "UB2",
            "coordinates": {"latitude": "52.29", "longitude": "5.24"},
            "operator": {"name": "Ubitricity"},
            "address": "Teststraat 3",
            "city": "Huizen",
            "evses": [{
                "evse_id": "NL*UB2*E12345",
                "status": "AVAILABLE",
                "connectors": [{
                    "standard": "IEC_62196_T2",
                    "max_electric_power": 22000,
                    "tariff_ids": ["regular", "adhoc"],
                }],
            }],
        }
        result = process.process_location(loc, process.build_tariff_index(tariffs), {})
        self.assertEqual(result["party_id"], "UB2")
        self.assertEqual(result["evse_ids"], ["NL*UB2*E12345"])
        self.assertTrue(result["direct_payment"]["supported"])
        self.assertTrue(result["direct_payment"]["priced"])
        self.assertEqual(result["pricing"]["direct_pay"]["kwh"], 0.36)
        self.assertEqual(result["pricing"]["direct_pay"]["session"], 0.35)

    def test_ubitricity_public_page_parser_extracts_ad_hoc_rate(self):
        page = process.normalize_public_page(
            "<main><h2>Ad Hoc Opladen</h2><p>via QR-code op scherm</p>"
            "<span>Per kWh</span><strong>0,35€</strong></main>"
        )
        self.assertEqual(process.parse_ubitricity_mrae_direct_rate(page), 0.35)

    def test_ubitricity_public_page_parser_extracts_selected_msp_rates(self):
        page = process.normalize_public_page(
            "<section><h2>RFID / Apps*</h2><span>Per kWh</span>"
            "<ol><li>ANWB</li><li>Greenchoice</li><li>Tap Electric</li><li>Essent</li>"
            "<li>MoveMove</li><li>Green Caravan</li><li>Eneco</li>"
            "<li>Shell Recharge (App)</li><li>Vattenfall Incharge</li><li>MKB Brandstof</li></ol>"
            "<ul><li>0,35€</li><li>0,35€</li><li>0,35€</li><li>0,35€</li><li>0,35€</li>"
            "<li>0,36€</li><li>0,54€</li><li>0,55€</li><li>0,55€</li><li>0,69€</li></ul>"
            "<p>De getoonde tarieven zijn gebaseerd op de laadtarieven per kWh.</p></section>"
        )
        self.assertEqual(process.parse_ubitricity_mrae_msp_rates(page), {
            "anwb_free": 0.35,
            "tap_light": 0.35,
            "shell_basic": 0.55,
            "vattenfall": 0.55,
        })

    def test_totalenergies_public_page_parser_confirms_direct_rule(self):
        page = process.normalize_public_page(
            "<p>Het laadtarief bestaat uit een basisprijs (CPO-prijs), "
            "dit is ook de ad-hoc of direct payment prijs.</p>"
        )
        self.assertTrue(process.parse_totalenergies_direct_rule(page))

    def test_official_source_harvest_builds_party_rules(self):
        pages = {
            process.UBITRICITY_MRAE_DIRECT_SOURCE_URL: (
                "ad hoc opladen via qr-code op scherm per kwh 0,35€ "
                "rfid / apps per kwh 1. anwb 2. greenchoice 3. tap electric 4. essent "
                "5. movemove 6. green caravan 7. eneco 8. shell recharge (app) "
                "9. vattenfall incharge 10. mkb brandstof "
                "0,35€ 0,35€ 0,35€ 0,35€ 0,35€ 0,36€ 0,54€ 0,55€ 0,55€ 0,69€ "
                "de getoonde tarieven zijn gebaseerd op de laadtarieven per kwh"
            ),
            process.TOTALENERGIES_DIRECT_RULE_SOURCE_URL: (
                "het laadtarief bestaat uit een basisprijs (cpo-prijs), "
                "dit is ook de ad-hoc of direct payment prijs"
            ),
        }
        harvest = process.harvest_official_pricing(fetcher=lambda url: pages[url])
        self.assertEqual(harvest["direct_by_party"]["UB2"]["rate"], 0.35)
        self.assertEqual(harvest["direct_by_party"]["GFX"]["mode"], "mirror_cpo")
        self.assertEqual(harvest["msp_by_party"]["UB2"]["anwb_free"]["rate"], 0.35)
        self.assertEqual(harvest["msp_by_party"]["UB2"]["shell_basic"]["rate"], 0.55)
        self.assertTrue(all(source["status"] == "ok" for source in harvest["sources"]))

    def test_ubitricity_direct_survives_msp_table_layout_change(self):
        pages = {
            process.UBITRICITY_MRAE_DIRECT_SOURCE_URL: "ad hoc opladen via qr-code op scherm per kwh 0,35€",
            process.TOTALENERGIES_DIRECT_RULE_SOURCE_URL: (
                "het laadtarief bestaat uit een basisprijs (cpo-prijs), "
                "dit is ook de ad-hoc of direct payment prijs"
            ),
        }
        harvest = process.harvest_official_pricing(fetcher=lambda url: pages[url])
        self.assertEqual(harvest["direct_by_party"]["UB2"]["rate"], 0.35)
        self.assertNotIn("UB2", harvest["msp_by_party"])
        source = next(item for item in harvest["sources"] if item["party_id"] == "UB2")
        self.assertEqual(source["status"], "ok")
        self.assertEqual(source["msp_table_status"], "unavailable")

    def test_ubitricity_official_source_creates_direct_quote_when_ndw_has_none(self):
        info = process.supplemental_direct_price_info(
            "UB2",
            0.335,
            [0.32, 0.35],
            "ndw",
            {
                "UB2": {
                    "mode": "fixed",
                    "rate": 0.35,
                    "session": 0.0,
                    "basis": "official_cpo_adhoc",
                    "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                    "confidence": "high",
                }
            },
        )
        pricing = process.build_pricing(
            0.335,
            "ndw",
            "Ubitricity",
            22,
            party_id="UB2",
            direct_price_info=info,
        )
        self.assertEqual(pricing["direct_pay"]["kwh"], 0.35)
        self.assertEqual(pricing["direct_pay"]["basis"], "official_cpo_adhoc")
        self.assertEqual(pricing["direct_pay"]["source_url"], process.UBITRICITY_MRAE_DIRECT_SOURCE_URL)

    def test_ubitricity_official_msp_rates_override_generic_roaming_estimates(self):
        overrides = {
            "anwb_free": {
                "rate": 0.35,
                "basis": "official_cpo_msp_rate",
                "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                "confidence": "medium",
            },
            "tap_light": {
                "rate": 0.35,
                "basis": "official_cpo_msp_rate",
                "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                "confidence": "medium",
            },
            "shell_basic": {
                "rate": 0.55,
                "basis": "official_cpo_msp_rate",
                "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                "confidence": "medium",
            },
            "vattenfall": {
                "rate": 0.55,
                "basis": "official_cpo_msp_rate",
                "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                "confidence": "medium",
            },
        }
        pricing = process.build_pricing(
            0.335,
            "ndw",
            "Ubitricity",
            22,
            party_id="UB2",
            msp_price_overrides=overrides,
        )
        self.assertEqual(pricing["anwb_free"]["kwh"], 0.35)
        self.assertEqual(pricing["anwb_free"]["session"], 0.89)
        self.assertEqual(pricing["tap_light"]["kwh"], 0.35)
        self.assertEqual(pricing["tap_light"]["percentage"], 0.05)
        self.assertEqual(pricing["shell_basic"]["kwh"], 0.55)
        self.assertNotIn("range", pricing["shell_basic"])
        self.assertEqual(pricing["vattenfall"]["kwh"], 0.55)
        self.assertEqual(pricing["vattenfall"]["session"], 0.35)
        self.assertEqual(pricing["vattenfall"]["basis"], "official_cpo_msp_rate")

    def test_totalenergies_official_direct_rule_mirrors_cpo_range(self):
        info = process.supplemental_direct_price_info(
            "GFX",
            0.41,
            [0.34, 0.48],
            "totalenergies_mrae",
            {
                "GFX": {
                    "mode": "mirror_cpo",
                    "basis": "official_cpo_direct_rule",
                    "source_url": process.TOTALENERGIES_DIRECT_RULE_SOURCE_URL,
                }
            },
        )
        self.assertEqual(info["rate"], 0.41)
        self.assertEqual(info["range"], [0.34, 0.48])
        self.assertEqual(info["confidence"], "low")

    def test_process_location_uses_official_direct_rule_only_after_ndw_lookup(self):
        loc = {
            "id": "te-official-direct",
            "country_code": "NL",
            "party_id": "GFX",
            "coordinates": {"latitude": "52.29", "longitude": "5.24"},
            "operator": {"name": "TotalEnergies"},
            "address": "Teststraat 4",
            "city": "Huizen",
            "evses": [{
                "status": "AVAILABLE",
                "connectors": [{
                    "standard": "IEC_62196_T2",
                    "max_electric_power": 17000,
                    "tariff_ids": [],
                }],
            }],
        }
        official = {
            "GFX": {
                "mode": "mirror_cpo",
                "basis": "official_cpo_direct_rule",
                "source_url": process.TOTALENERGIES_DIRECT_RULE_SOURCE_URL,
            }
        }
        result = process.process_location(
            loc, process.build_tariff_index([]), {}, official_direct=official
        )
        self.assertTrue(result["direct_payment"]["priced"])
        self.assertEqual(result["direct_payment"]["reason"], "official_operator_source")
        self.assertEqual(result["pricing"]["direct_pay"]["range"], [0.34, 0.48])
        self.assertEqual(result["pricing"]["direct_pay"]["basis"], "official_cpo_direct_rule")



if __name__ == "__main__":
    unittest.main()
