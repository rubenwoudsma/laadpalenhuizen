import unittest

import process


class PricingRulesTest(unittest.TestCase):
    def test_unknown_cpo_does_not_create_false_comparison(self):
        pricing = process.build_pricing(None, "unknown", "50five", "AC")
        self.assertEqual(set(pricing), {"shell_basic"})
        self.assertEqual(pricing["shell_basic"]["confidence"], "low")

    def test_anwb_free_uses_cpo_plus_session_fee(self):
        quote = process.build_pricing(0.40, "ndw", "50five", "AC")["anwb_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["session"], 0.89)
        self.assertEqual(quote["confidence"], "high")

    def test_anwb_discount_network_is_not_invented(self):
        quote = process.build_pricing(0.40, "ndw", "TotalEnergies", "AC")["anwb_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["confidence"], "medium")
        self.assertIn("korting", quote["note"].lower())
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")

    def test_tap_light_uses_cpo_plus_five_percent_transaction_fee(self):
        quote = process.build_pricing(0.40, "ndw", "TotalEnergies", "AC")["tap_light"]
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
        quote = process.build_pricing(0.42, "ndw", "Vattenfall InCharge", "AC")["vattenfall"]
        self.assertEqual(quote["session"], 0.0)
        self.assertEqual(quote["confidence"], "high")

    def test_vattenfall_generic_roaming_fails_closed_without_numeric_start_fee(self):
        pricing = process.build_pricing(0.42, "ndw", "Ubitricity", "AC")
        self.assertNotIn("vattenfall", pricing)

    def test_eflux_flex_own_network_has_no_kwh_markup(self):
        quote = process.build_pricing(0.45, "ndw", "E-Flux by Road", "AC")["eflux_flex"]
        self.assertEqual(quote["kwh"], 0.45)
        self.assertEqual(quote["session"], 0.31)

    def test_eflux_flex_roaming_adds_kwh_markup(self):
        quote = process.build_pricing(0.45, "ndw", "Ubitricity", "AC")["eflux_flex"]
        self.assertEqual(quote["kwh"], 0.474)
        self.assertEqual(quote["session"], 0.31)
        self.assertIn("0,48", quote["note"])

    def test_shell_ac_price_band_is_explicit_estimate(self):
        quote = process.build_pricing(0.40, "ndw", "Ubitricity", "AC")["shell_basic"]
        self.assertEqual(quote["kwh"], 0.55)
        self.assertEqual(quote["session"], 0.35)
        self.assertEqual(quote["range"], [0.5, 0.6])
        self.assertEqual(quote["confidence"], "low")
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")
        self.assertIn("potential_msp_blocking_fee", quote["quality"]["reasons"])

    def test_shell_dc_uses_dc_band(self):
        quote = process.build_pricing(0.55, "ndw", "Fastcharge", "DC")["shell_basic"]
        self.assertEqual(quote["kwh"], 0.82)
        self.assertEqual(quote["range"], [0.79, 0.85])
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")
        self.assertIn("potential_msp_blocking_fee", quote["quality"]["reasons"])

    def test_shell_own_dc_is_indicative_because_blocking_fees_are_location_specific(self):
        quote = process.build_pricing(0.55, "ndw", "Shell Recharge", "DC", party_id="TNM")["shell_basic"]
        self.assertEqual(quote["kwh"], 0.78)
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")
        self.assertIn("potential_msp_blocking_fee", quote["quality"]["reasons"])

    def test_shell_own_ac_is_not_invented_from_roaming_band(self):
        pricing = process.build_pricing(0.42, "ndw", "Shell Recharge", "AC", party_id="TNM")
        self.assertNotIn("shell_basic", pricing)

    def test_laadkompas_free_uses_cpo_plus_session_fee(self):
        quote = process.build_pricing(0.40, "ndw", "50five", "AC")["laadkompas_free"]
        self.assertEqual(quote["kwh"], 0.40)
        self.assertEqual(quote["session"], 0.47)

    def test_regular_lookup_ignores_profile_tariffs(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "cheap",
            "type": "PROFILE_CHEAP",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.10}]}],
        }]
        index = process.build_tariff_index(tariffs)
        self.assertIsNone(process.get_cpo_price_info("cheap", index, "NL", "AAA"))
        self.assertEqual(process.get_cpo_rates("cheap", index, "NL", "AAA"), [])

    def test_expired_and_future_tariffs_are_not_used(self):
        tariffs = [
            {
                "country_code": "NL", "party_id": "AAA", "id": "expired",
                "type": "REGULAR", "end_date_time": "2025-01-01T00:00:00Z",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.20}]}],
            },
            {
                "country_code": "NL", "party_id": "AAA", "id": "future",
                "type": "REGULAR", "start_date_time": "2099-01-01T00:00:00Z",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.25}]}],
            },
            {
                "country_code": "NL", "party_id": "AAA", "id": "active",
                "type": "REGULAR",
                "start_date_time": "2020-01-01T00:00:00Z", "end_date_time": "2099-01-01T00:00:00Z",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}],
            },
        ]
        index = process.build_tariff_index(tariffs)
        self.assertIsNone(process.get_cpo_price_info("expired", index, "NL", "AAA"))
        self.assertIsNone(process.get_cpo_price_info("future", index, "NL", "AAA"))
        self.assertEqual(process.get_cpo_price_info("active", index, "NL", "AAA")["rate"], 0.40)

    def test_invalid_tariff_validity_timestamp_fails_closed(self):
        tariff = {"start_date_time": "not-a-date"}
        self.assertFalse(process.tariff_is_active(tariff))

    def test_tariff_lookup_uses_ocpi_party_scope(self):
        tariffs = [
            {
                "country_code": "NL", "party_id": "AAA", "id": "shared", "currency": "EUR",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.30}]}],
            },
            {
                "country_code": "NL", "party_id": "BBB", "id": "shared", "currency": "EUR",
                "elements": [{"price_components": [{"type": "ENERGY", "price": 0.60}]}],
            },
        ]
        index = process.build_tariff_index(tariffs)
        self.assertEqual(process.get_cpo_rate("shared", index, "NL", "AAA"), 0.30)
        self.assertEqual(process.get_cpo_rate("shared", index, "NL", "BBB"), 0.60)
        self.assertIsNone(process.get_cpo_rate("shared", index))

    def test_unrestricted_fallback_elements_use_first_energy_dimension(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "dynamic", "currency": "EUR",
            "elements": [
                {"price_components": [{"type": "ENERGY", "price": 0.30, "step_size": 1}]},
                {"price_components": [{"type": "ENERGY", "price": 0.50, "step_size": 1}]},
            ],
        }]
        info = process.get_cpo_price_info("dynamic", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertEqual(info["rate"], 0.30)
        self.assertIsNone(info["range"])

    def test_totalenergies_mrae_ac_fallback_is_range(self):
        fallback = process.totalenergies_mrae_fallback("TotalEnergies   ", "AC")
        self.assertEqual(fallback["source"], "totalenergies_mrae")
        self.assertEqual(fallback["rate"], 0.41)
        self.assertEqual(fallback["range"], [0.34, 0.48])

    def test_totalenergies_mrae_ac_fallback_does_not_claim_concession_resolution(self):
        fallback = process.totalenergies_mrae_fallback("TotalEnergies", "AC")
        self.assertEqual(fallback["range"], [0.34, 0.48])
        self.assertIn("exacte concessie", fallback["note"].lower())
        self.assertIn("dynamische", fallback["note"].lower())
        self.assertEqual(process.TOTALENERGIES_MRAE_VERIFIED_AT, "2026-08-28")
        self.assertIn("niet als concessieheuristiek", process.TOTALENERGIES_MRAE_RESOLUTION_NOTE)
        self.assertIn("plaatsingsdatum", process.TOTALENERGIES_MRAE_RESOLUTION_NOTE)

    def test_totalenergies_mrae_dc_fallback_is_exact_regional_rate(self):
        fallback = process.totalenergies_mrae_fallback("TotalEnergies", "DC")
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
            "AC",
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

    def test_lidl_public_page_parser_extracts_direct_ac_dc_rates(self):
        page = process.normalize_public_page(
            "<h2>Opladen via Lidl.nl</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
        )
        self.assertEqual(process.parse_lidl_direct_rates(page), {"AC": 0.55, "DC": 0.60})

    def test_lidl_public_page_parser_fails_closed_when_one_rate_is_missing(self):
        page = process.normalize_public_page(
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
        )
        self.assertEqual(process.parse_lidl_direct_rates(page), {})

    def test_lidl_direct_parser_does_not_reuse_own_charge_card_section(self):
        page = process.normalize_public_page(
            "<h2>Opladen met eigen laadpas</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
            "<p>Let op: de abonnementskosten van uw laadpas aanbieder zijn van toepassing.</p>"
        )
        self.assertEqual(process.parse_lidl_direct_rates(page), {})
        self.assertEqual(process.parse_lidl_cpo_rates(page), {"AC": 0.55, "DC": 0.60})

    def test_lidl_cpo_parser_requires_own_charge_card_section(self):
        page = process.normalize_public_page(
            "<h2>Opladen via Lidl.nl</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
        )
        self.assertEqual(process.parse_lidl_cpo_rates(page), {})

    def test_lidl_cpo_parser_requires_provider_cost_qualifier(self):
        page = process.normalize_public_page(
            "<h2>Opladen met eigen laadpas</h2>"
            "<p>Lidl.nl-tarief regulier-laadstation: € 0.55 €/kWh (AC)</p>"
            "<p>Lidl.nl-tarief snel-laadstation: € 0.60 €/kWh (DC)</p>"
        )
        self.assertEqual(process.parse_lidl_cpo_rates(page), {})

    def test_lidl_current_type_direct_rule_uses_ac_or_dc_only(self):
        rule = {
            "LDL": {
                "mode": "by_current_type",
                "rates": {"AC": 0.55, "DC": 0.60},
                "basis": "official_cpo_adhoc",
                "source_id": "lidl_direct_payment",
                "source_url": process.LIDL_DIRECT_SOURCE_URL,
                "confidence": "high",
            }
        }
        ac = process.supplemental_direct_price_info("LDL", None, None, "unknown", rule, current_type="AC")
        dc = process.supplemental_direct_price_info("LDL", None, None, "unknown", rule, current_type="DC")
        unknown = process.supplemental_direct_price_info("LDL", None, None, "unknown", rule, current_type="UNKNOWN")
        self.assertEqual(ac["rate"], 0.55)
        self.assertEqual(dc["rate"], 0.60)
        self.assertIsNone(unknown)
        self.assertEqual(ac["basis"], "official_cpo_adhoc")

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
            process.LIDL_DIRECT_SOURCE_URL: (
                "opladen via lidl.nl lidl.nl-tarief regulier-laadstation: € 0.55 €/kwh (ac) "
                "lidl.nl-tarief snel-laadstation: € 0.60 €/kwh (dc) "
                "opladen met eigen laadpas lidl.nl-tarief regulier-laadstation: € 0.55 €/kwh (ac) "
                "lidl.nl-tarief snel-laadstation: € 0.60 €/kwh (dc) "
                "let op: de abonnementskosten van uw laadpas aanbieder zijn van toepassing"
            ),
        }
        harvest = process.harvest_official_pricing(fetcher=lambda url: pages[url])
        self.assertEqual(harvest["direct_by_party"]["UB2"]["rate"], 0.35)
        self.assertEqual(harvest["direct_by_party"]["GFX"]["mode"], "mirror_cpo")
        self.assertEqual(harvest["direct_by_party"]["LDL"]["rates"], {"AC": 0.55, "DC": 0.60})
        self.assertEqual(harvest["cpo_by_party"]["LDL"]["rates"], {"AC": 0.55, "DC": 0.60})
        self.assertEqual(harvest["msp_by_party"]["UB2"]["anwb_free"]["rate"], 0.35)
        self.assertEqual(harvest["msp_by_party"]["UB2"]["shell_basic"]["rate"], 0.55)
        self.assertTrue(all(source["status"] == "ok" for source in harvest["sources"]))

    def test_lidl_official_direct_rates_follow_connector_current_type(self):
        location = {
            "id": "lidl-huizen-test",
            "country_code": "NL",
            "party_id": "LDL",
            "operator": {"name": "Lidl"},
            "name": "Lidl test",
            "address": "Voorbaan 1",
            "city": "Huizen",
            "coordinates": {"latitude": "52.2955", "longitude": "5.2451"},
            "evses": [
                {
                    "evse_id": "NL*LDL*EAC",
                    "status": "AVAILABLE",
                    "connectors": [{
                        "id": "1",
                        "standard": "IEC_62196_T2",
                        "power_type": "AC_3_PHASE",
                        "max_electric_power": 22000,
                        "tariff_ids": [],
                    }],
                },
                {
                    "evse_id": "NL*LDL*EDC",
                    "status": "AVAILABLE",
                    "connectors": [{
                        "id": "1",
                        "standard": "IEC_62196_T2_COMBO",
                        "power_type": "DC",
                        "max_electric_power": 50000,
                        "tariff_ids": [],
                    }],
                },
            ],
        }
        official_direct = {
            "LDL": {
                "mode": "by_current_type",
                "rates": {"AC": 0.55, "DC": 0.60},
                "basis": "official_cpo_adhoc",
                "source_id": "lidl_direct_payment",
                "source_url": process.LIDL_DIRECT_SOURCE_URL,
                "source_checked_at": "2026-08-28T08:00:00+00:00",
            }
        }

        official_cpo = {
            "LDL": {
                "mode": "by_current_type",
                "rates": {"AC": 0.55, "DC": 0.60},
                "basis": "official_cpo_tariff",
                "source_id": "lidl_cpo_tariff",
                "source_url": process.LIDL_DIRECT_SOURCE_URL,
                "source_checked_at": "2026-08-28T08:00:00+00:00",
                "confidence": "high",
            }
        }
        point = process.process_location(
            location, process.build_tariff_index([]), official_direct=official_direct, official_cpo=official_cpo
        )
        self.assertIsNotNone(point)
        direct_rates = {
            option["current_type"]: option["pricing"]["direct_pay"]["kwh"]
            for option in point["connector_options"]
        }
        self.assertEqual(direct_rates, {"AC": 0.55, "DC": 0.60})
        base_rates = {
            option["current_type"]: option["tariff"]["rate"]
            for option in point["connector_options"]
        }
        self.assertEqual(base_rates, {"AC": 0.55, "DC": 0.60})
        self.assertTrue(all(option["tariff"]["source"] == "official_cpo_tariff" for option in point["connector_options"]))
        self.assertTrue(all(option["tariff"]["quality"]["source_quality"] == "high" for option in point["connector_options"]))
        self.assertTrue(all(option["tariff"]["quality"]["price_specificity"] == "network" for option in point["connector_options"]))
        self.assertTrue(all(
            option["direct_payment"]["reason"] == "official_operator_source"
            for option in point["connector_options"]
        ))

    def test_lidl_ndw_tariff_takes_precedence_over_official_cpo_fallback(self):
        location = {
            "id": "lidl-ndw-priority",
            "country_code": "NL",
            "party_id": "LDL",
            "operator": {"name": "Lidl"},
            "name": "Lidl NDW priority",
            "address": "Voorbaan 2",
            "city": "Huizen",
            "coordinates": {"latitude": "52.2955", "longitude": "5.2451"},
            "evses": [{
                "evse_id": "NL*LDL*ENDW",
                "status": "AVAILABLE",
                "connectors": [{
                    "id": "1",
                    "standard": "IEC_62196_T2",
                    "power_type": "AC_3_PHASE",
                    "max_electric_power": 22000,
                    "tariff_ids": ["lidl-ndw"],
                }],
            }],
        }
        tariffs = [{
            "country_code": "NL",
            "party_id": "LDL",
            "id": "lidl-ndw",
            "currency": "EUR",
            "type": "REGULAR",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.49, "step_size": 1}]}],
        }]
        official_cpo = {
            "LDL": {
                "mode": "by_current_type",
                "rates": {"AC": 0.55, "DC": 0.60},
                "basis": "official_cpo_tariff",
                "source_id": "lidl_cpo_tariff",
                "source_url": process.LIDL_DIRECT_SOURCE_URL,
                "source_checked_at": "2026-08-28T08:00:00+00:00",
                "confidence": "high",
            }
        }
        point = process.process_location(
            location, process.build_tariff_index(tariffs), official_cpo=official_cpo
        )
        option = point["connector_options"][0]
        self.assertEqual(option["tariff"]["rate"], 0.49)
        self.assertEqual(option["tariff"]["source"], "ndw")

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
        self.assertEqual(pricing["shell_basic"]["quality"]["decision_grade"], "indicative")
        self.assertIn("potential_msp_blocking_fee", pricing["shell_basic"]["quality"]["reasons"])
        self.assertEqual(pricing["vattenfall"]["kwh"], 0.55)
        self.assertEqual(pricing["vattenfall"]["session"], 0.0)
        self.assertEqual(pricing["vattenfall"]["basis"], "official_cpo_msp_rate")
        self.assertEqual(pricing["vattenfall"]["quality"]["decision_grade"], "exclude")
        self.assertIn("starttarief", pricing["vattenfall"]["quality"]["unmodelled_costs"][0].lower())

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


    def test_power_type_defines_dc_even_at_25kw(self):
        current_type, source = process.connector_current_type({
            "power_type": "DC", "standard": "IEC_62196_T2_COMBO", "max_electric_power": 25000,
        })
        self.assertEqual(current_type, "DC")
        self.assertEqual(source, "ocpi_power_type")

    def test_power_type_defines_ac_even_above_old_50kw_threshold(self):
        current_type, source = process.connector_current_type({
            "power_type": "AC_3_PHASE", "standard": "IEC_62196_T2", "max_electric_power": 100000,
        })
        self.assertEqual(current_type, "AC")
        self.assertEqual(source, "ocpi_power_type")

    def test_tesla_standard_without_power_type_is_not_guessed(self):
        current_type, source = process.connector_current_type({
            "standard": "TESLA_S", "max_electric_power": 120000,
        })
        self.assertEqual(current_type, "UNKNOWN")
        self.assertEqual(source, "unknown")

    def test_connector_power_is_derived_from_three_phase_voltage_and_current(self):
        power_kw = process.connector_power_kw({
            "power_type": "AC_3_PHASE", "max_voltage": 230, "max_amperage": 32,
        })
        self.assertEqual(power_kw, 22.1)

    def test_explicit_connector_power_overrides_voltage_current_derivation(self):
        power_kw = process.connector_power_kw({
            "power_type": "AC_3_PHASE", "max_electric_power": 11000,
            "max_voltage": 230, "max_amperage": 32,
        })
        self.assertEqual(power_kw, 11.0)

    def test_regular_flat_fee_is_carried_into_msp_session_total(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "regular", "type": "REGULAR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40},
                {"type": "FLAT", "price": 0.25},
            ]}],
        }]
        info = process.get_cpo_price_info("regular", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertEqual(info["session"], 0.25)
        pricing = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_session=info["session"], cpo_unmodelled_types=info["unmodelled_types"],
        )
        self.assertEqual(pricing["anwb_free"]["session"], 1.14)
        self.assertEqual(pricing["laadkompas_free"]["session"], 0.72)
        self.assertEqual(pricing["anwb_free"]["quality"]["decision_grade"], "reliable")

    def test_time_component_fails_closed_for_session_ranking(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "timed", "type": "REGULAR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40},
                {"type": "TIME", "price": 0.05},
            ]}],
        }]
        info = process.get_cpo_price_info("timed", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("TIME", info["unmodelled_types"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC", cpo_unmodelled_types=info["unmodelled_types"]
        )["anwb_free"]
        self.assertEqual(quote["quality"]["cost_completeness"], "partial")
        self.assertEqual(quote["quality"]["decision_grade"], "exclude")

    def test_minimum_or_maximum_tariff_price_fails_closed(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "bounded", "type": "REGULAR",
            "min_price": {"excl_vat": 2.0},
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}],
        }]
        info = process.get_cpo_price_info("bounded", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("MIN_PRICE", info["unmodelled_types"])


    def test_tariff_restrictions_block_hard_ranking_until_modelled(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "restricted",
            "elements": [{
                "restrictions": {"start_time": "22:00", "end_time": "06:00"},
                "price_components": [{"type": "ENERGY", "price": 0.30}],
            }],
        }]
        info = process.get_cpo_price_info(
            "restricted", process.build_tariff_index(tariffs), "NL", "AAA"
        )
        self.assertTrue(info["restricted"])
        pricing = process.build_pricing(
            info["rate"], "ndw", "50five", "AC", cpo_restricted=info["restricted"]
        )
        self.assertEqual(pricing["anwb_free"]["quality"]["decision_grade"], "exclude")
        self.assertIn("TARIFF_RESTRICTIONS", pricing["anwb_free"]["quality"]["unmodelled_costs"])

    def test_restricted_ad_hoc_tariff_is_not_ranked(self):
        pricing = process.build_pricing(
            0.40, "ndw", "50five", "AC",
            direct_price_info={
                "rate": 0.35, "session": 0.0, "basis": "ndw_ad_hoc",
                "restricted": True, "unmodelled_types": [],
            },
        )
        self.assertEqual(pricing["direct_pay"]["quality"]["decision_grade"], "exclude")
        self.assertIn("TARIFF_RESTRICTIONS", pricing["direct_pay"]["quality"]["unmodelled_costs"])

    def test_mixed_ac_dc_location_keeps_connector_profiles_separate(self):
        tariffs = [
            {"country_code": "NL", "party_id": "AAA", "id": "ac", "type": "REGULAR", "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}]},
            {"country_code": "NL", "party_id": "AAA", "id": "dc", "type": "REGULAR", "elements": [{"price_components": [{"type": "ENERGY", "price": 0.60}]}]},
        ]
        loc = {
            "id": "mixed", "country_code": "NL", "party_id": "AAA",
            "coordinates": {"latitude": "52.29", "longitude": "5.24"},
            "operator": {"name": "Mixed CPO"}, "address": "Mix 1", "city": "Huizen",
            "evses": [{"evse_id": "NL*AAA*1", "status": "AVAILABLE", "connectors": [
                {"id": "1", "standard": "IEC_62196_T2", "power_type": "AC_3_PHASE", "max_electric_power": 22000, "tariff_ids": ["ac"]},
                {"id": "2", "standard": "IEC_62196_T2_COMBO", "power_type": "DC", "max_electric_power": 50000, "tariff_ids": ["dc"]},
            ]}],
        }
        result = process.process_location(loc, process.build_tariff_index(tariffs), {})
        self.assertEqual(len(result["connector_options"]), 2)
        self.assertEqual({p["current_type"] for p in result["connector_options"]}, {"AC", "DC"})
        self.assertEqual(result["pricing"], {})
        self.assertEqual(result["pricing_source"], "mixed")

    def test_identical_connectors_are_grouped_into_one_profile(self):
        pricing = process.build_pricing(0.40, "ndw", "50five", "AC")
        row = {
            "connector_type": "Type 2", "standard": "IEC_62196_T2", "current_type": "AC",
            "current_type_source": "ocpi_power_type", "ocpi_power_type": "AC_3_PHASE", "power_kw": 22.0,
            "status": "AVAILABLE", "last_updated": "2026-08-27T10:00:00Z",
            "tariff": {"source": "ndw", "rate": 0.40}, "direct_payment": {}, "pricing": pricing,
        }
        rows = [{**row, "evse_id": "E1", "connector_id": "1"}, {**row, "evse_id": "E2", "connector_id": "1"}]
        grouped = process.group_connector_profiles(rows)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["count"], 2)
        self.assertEqual(grouped[0]["available_count"], 2)

    def test_verified_rules_fail_closed_per_pass(self):
        pricing = process.build_pricing(0.40, "ndw", "50five", "AC", verified_rules={"anwb_free"})
        self.assertEqual(set(pricing), {"anwb_free"})

    def test_eflux_roaming_models_possible_clearing_fee_as_bounded_range(self):
        quote = process.build_pricing(0.45, "ndw", "Ubitricity", "AC")["eflux_flex"]
        self.assertEqual(quote["session_range"], [0.31, 0.79])
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")

    def test_regional_source_is_high_quality_but_only_indicative(self):
        quote = process.build_pricing(
            0.41, "totalenergies_mrae", "TotalEnergies", "AC", cpo_rate_range=[0.34, 0.48]
        )["anwb_free"]
        self.assertEqual(quote["quality"]["source_quality"], "high")
        self.assertEqual(quote["quality"]["price_specificity"], "regional")
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")

    def test_operator_median_is_low_quality_operator_estimate(self):
        quote = process.build_pricing(0.41, "operator_median", "Example", "AC")["anwb_free"]
        self.assertEqual(quote["quality"]["source_quality"], "low")
        self.assertEqual(quote["quality"]["price_specificity"], "operator_estimate")
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")



if __name__ == "__main__":
    unittest.main()


class OcpiBillingSemanticsP0Test(unittest.TestCase):
    def test_missing_vat_means_no_vat_under_ocpi_221(self):
        component = {"type": "ENERGY", "price": 0.40, "step_size": 1}
        self.assertEqual(process.price_component_including_vat(component), 0.40)

    def test_explicit_vat_is_added_to_consumer_price(self):
        component = {"type": "ENERGY", "price": 0.40, "vat": 21, "step_size": 1}
        self.assertEqual(process.price_component_including_vat(component), 0.484)

    def test_malformed_explicit_vat_fails_closed(self):
        component = {"type": "ENERGY", "price": 0.40, "vat": "unknown", "step_size": 1}
        self.assertIsNone(process.price_component_including_vat(component))

    def test_energy_step_size_is_preserved(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "stepped", "type": "REGULAR",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40, "step_size": 250}]}],
        }]
        info = process.get_cpo_price_info("stepped", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertEqual(info["energy_step_size_wh"], 250)
        self.assertNotIn("energy_step_size_not_explicit", info["quality_reasons"])

    def test_missing_energy_step_size_blocks_reliable_grade_but_keeps_indication(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "nostep", "type": "REGULAR",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40}]}],
        }]
        info = process.get_cpo_price_info("nostep", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("energy_step_size_not_explicit", info["quality_reasons"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_quality_reasons=info["quality_reasons"],
        )["anwb_free"]
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")
        self.assertIn("energy_step_size_not_explicit", quote["quality"]["reasons"])

    def test_multiple_energy_step_sizes_fail_closed(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "multistep", "type": "REGULAR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 100},
                {"type": "ENERGY", "price": 0.40, "step_size": 250},
            ]}],
        }]
        info = process.get_cpo_price_info("multistep", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("ENERGY_STEP_SIZE_VARIANTS", info["unmodelled_types"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_unmodelled_types=info["unmodelled_types"],
        )["anwb_free"]
        self.assertEqual(quote["quality"]["decision_grade"], "exclude")

    def test_unrestricted_tariff_uses_first_element_per_dimension(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "fallback", "type": "REGULAR", "currency": "EUR",
            "elements": [
                {"price_components": [{"type": "ENERGY", "price": 0.40, "step_size": 1}]},
                {"price_components": [{"type": "ENERGY", "price": 0.99, "step_size": 1}]},
            ],
        }]
        info = process.get_cpo_price_info("fallback", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertEqual(info["rate"], 0.40)
        self.assertIsNone(info["range"])

    def test_invalid_flat_component_blocks_ranking_instead_of_disappearing(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "bad-flat", "type": "REGULAR", "currency": "EUR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 1},
                {"type": "FLAT", "price": "unknown", "step_size": 1},
            ]}],
        }]
        info = process.get_cpo_price_info("bad-flat", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("INVALID_PRICE_COMPONENT", info["unmodelled_types"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_unmodelled_types=info["unmodelled_types"],
            cpo_energy_step_size_wh=info["energy_step_size_wh"],
        )["anwb_free"]
        self.assertEqual(quote["quality"]["decision_grade"], "exclude")

    def test_malformed_time_component_still_blocks_ranking(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "bad-time", "type": "REGULAR", "currency": "EUR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 1},
                {"type": "TIME", "price": "broken", "step_size": 60},
            ]}],
        }]
        info = process.get_cpo_price_info("bad-time", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("TIME", info["unmodelled_types"])
        self.assertIn("INVALID_PRICE_COMPONENT", info["unmodelled_types"])

    def test_unknown_price_dimension_fails_closed(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "custom", "type": "REGULAR", "currency": "EUR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 1},
                {"type": "CUSTOM_FEE", "price": 1.25, "step_size": 1},
            ]}],
        }]
        info = process.get_cpo_price_info("custom", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("UNSUPPORTED_PRICE_COMPONENT", info["unmodelled_types"])

    def test_non_eur_tariff_is_not_relabelled_as_euro(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "usd", "type": "REGULAR", "currency": "USD",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40, "step_size": 1}]}],
        }]
        index = process.build_tariff_index(tariffs)
        self.assertIsNone(process.get_cpo_price_info("usd", index, "NL", "AAA"))
        self.assertEqual(process.get_cpo_rates("usd", index, "NL", "AAA"), [])

    def test_missing_currency_keeps_numeric_indication_but_not_reliable_grade(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "nocurrency", "type": "REGULAR",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.40, "step_size": 1}]}],
        }]
        info = process.get_cpo_price_info("nocurrency", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("currency_not_explicit", info["quality_reasons"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_quality_reasons=info["quality_reasons"],
            cpo_energy_step_size_wh=info["energy_step_size_wh"],
        )["anwb_free"]
        self.assertEqual(quote["quality"]["decision_grade"], "indicative")

    def test_flat_only_free_tariff_is_a_valid_zero_energy_price(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "free", "type": "REGULAR", "currency": "EUR",
            "elements": [{"price_components": [{"type": "FLAT", "price": 0.0, "step_size": 1}]}],
        }]
        info = process.get_cpo_price_info("free", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIsNotNone(info)
        self.assertEqual(info["rate"], 0.0)
        self.assertEqual(info["session"], 0.0)
        self.assertNotIn("energy_step_size_not_explicit", info["quality_reasons"])

    def test_duplicate_dimension_in_same_unrestricted_element_fails_closed(self):
        tariffs = [{
            "country_code": "NL", "party_id": "AAA", "id": "duplicate", "type": "REGULAR", "currency": "EUR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 1},
                {"type": "ENERGY", "price": 0.45, "step_size": 1},
            ]}],
        }]
        info = process.get_cpo_price_info("duplicate", process.build_tariff_index(tariffs), "NL", "AAA")
        self.assertIn("DUPLICATE_PRICE_DIMENSION", info["unmodelled_types"])
        quote = process.build_pricing(
            info["rate"], "ndw", "50five", "AC",
            cpo_rate_range=info["range"], cpo_unmodelled_types=info["unmodelled_types"],
            cpo_energy_step_size_wh=info["energy_step_size_wh"],
        )["anwb_free"]
        self.assertEqual(quote["quality"]["decision_grade"], "exclude")

    def test_cpo_step_size_propagates_to_derived_msp_quotes(self):
        pricing = process.build_pricing(
            0.40, "ndw", "50five", "AC", cpo_energy_step_size_wh=250
        )
        self.assertEqual(pricing["anwb_free"]["energy_step_size_wh"], 250)
        self.assertEqual(pricing["tap_light"]["energy_step_size_wh"], 250)
        self.assertEqual(pricing["laadkompas_free"]["energy_step_size_wh"], 250)

    def test_eflux_keeps_cpo_billing_step_separate_from_msp_kwh_markup(self):
        quote = process.build_pricing(
            0.40, "ndw", "Ubitricity", "AC", cpo_energy_step_size_wh=250
        )["eflux_flex"]
        self.assertEqual(quote["energy_step_size_wh"], 250)
        self.assertEqual(quote["cpo_kwh"], 0.40)
        self.assertEqual(quote["msp_kwh"], 0.024)

class OperatorMedianSafetyP0Test(unittest.TestCase):
    def test_operator_median_is_visible_but_never_ranked_as_complete_local_tariff(self):
        location = {
            "id": "median-only",
            "country_code": "NL",
            "party_id": "FFF",
            "coordinates": {"latitude": "52.30", "longitude": "5.24"},
            "operator": {"name": "50five"},
            "address": "Voorbeeldstraat 1",
            "city": "Huizen",
            "evses": [{
                "uid": "evse-1",
                "evse_id": "NL*FFF*E1",
                "status": "AVAILABLE",
                "connectors": [{
                    "id": "1",
                    "standard": "IEC_62196_T2",
                    "power_type": "AC_3_PHASE",
                    "max_voltage": 230,
                    "max_amperage": 32,
                    "tariff_ids": [],
                }],
            }],
        }
        result = process.process_location(location, process.build_tariff_index([]), {"50five": 0.40})
        profile = result["connector_options"][0]
        self.assertEqual(profile["tariff"]["source"], "operator_median")
        self.assertIn("LOCATION_TARIFF_COMPONENTS_UNKNOWN", profile["tariff"]["unmodelled_types"])
        self.assertEqual(profile["pricing"]["anwb_free"]["quality"]["decision_grade"], "exclude")
        self.assertEqual(profile["decision_status"], "insufficient")  # One independent Shell indication is not enough for a comparison.

class OcpiUnspecifiedTariffTypeP0Test(unittest.TestCase):
    def base_tariff(self):
        return {
            "country_code": "NL", "party_id": "AAA", "id": "generic", "currency": "EUR",
            "elements": [{"price_components": [
                {"type": "ENERGY", "price": 0.40, "step_size": 1},
            ]}],
        }

    def test_unspecified_tariff_type_is_valid_for_regular_session(self):
        index = process.build_tariff_index([self.base_tariff()])
        info = process.get_cpo_price_info("generic", index, "NL", "AAA")
        self.assertIsNotNone(info)
        self.assertEqual(info["tariff_type"], "UNSPECIFIED")

    def test_unspecified_tariff_type_does_not_by_itself_prove_ad_hoc(self):
        index = process.build_tariff_index([self.base_tariff()])
        self.assertIsNone(process.get_ad_hoc_price_info("generic", index, "NL", "AAA"))
        self.assertIsNotNone(process.get_ad_hoc_price_info("generic", index, "NL", "AAA", allow_unspecified=True))

    def test_unspecified_tariff_can_price_direct_only_when_payment_support_is_independently_known(self):
        loc = {
            "id": "generic-direct",
            "country_code": "NL", "party_id": "AAA",
            "coordinates": {"latitude": "52.30", "longitude": "5.24"},
            "operator": {"name": "Test CPO"}, "address": "Test 1", "city": "Huizen",
            "evses": [{
                "uid": "1", "evse_id": "NL*AAA*E1", "status": "AVAILABLE",
                "capabilities": ["CREDIT_CARD_PAYABLE"],
                "connectors": [{
                    "id": "1", "standard": "IEC_62196_T2", "power_type": "AC_3_PHASE",
                    "max_voltage": 230, "max_amperage": 32, "tariff_ids": ["generic"],
                }],
            }],
        }
        index = process.build_tariff_index([self.base_tariff()])
        result = process.process_location(loc, index)
        profile = result["connector_options"][0]
        self.assertTrue(profile["direct_payment"]["supported"])
        self.assertEqual(profile["pricing"]["direct_pay"]["basis"], "ndw_ad_hoc_compatible")
        self.assertEqual(profile["pricing"]["direct_pay"]["quality"]["decision_grade"], "reliable")


class P01SourcePrecedenceTest(unittest.TestCase):
    def ubitricity_location(self, tariff_ids):
        return {
            "id": "2378",
            "country_code": "NL", "party_id": "UB2",
            "coordinates": {"latitude": "52.2990918", "longitude": "5.2606067"},
            "operator": {"name": "Ubitricity"}, "address": "Moeflon 31", "city": "Huizen",
            "evses": [{
                "uid": "1", "evse_id": "NL*UB2*E10025806", "status": "AVAILABLE",
                "connectors": [{
                    "id": "1", "standard": "IEC_62196_T2", "power_type": "AC_3_PHASE",
                    "max_voltage": 230, "max_amperage": 32, "tariff_ids": tariff_ids,
                }],
            }],
        }

    def official_direct(self):
        return {
            "UB2": {
                "mode": "fixed", "rate": 0.35, "session": 0.0,
                "basis": "official_cpo_adhoc", "source_id": "ubitricity_mrae_direct",
                "source_url": process.UBITRICITY_MRAE_DIRECT_SOURCE_URL, "confidence": "high",
            }
        }

    def official_msp(self):
        return {"UB2": {
            "anwb_free": {"rate": 0.35, "basis": "official_cpo_msp_rate", "confidence": "medium"},
            "tap_light": {"rate": 0.35, "basis": "official_cpo_msp_rate", "confidence": "medium"},
            "shell_basic": {"rate": 0.55, "basis": "official_cpo_msp_rate", "confidence": "medium"},
            "vattenfall": {"rate": 0.55, "basis": "official_cpo_msp_rate", "confidence": "medium"},
        }}

    def test_official_direct_beats_generic_unspecified_ocpi_tariff(self):
        tariff = {
            "country_code": "NL", "party_id": "UB2", "id": "generic", "currency": "EUR",
            "elements": [{
                "restrictions": {"start_time": "22:00", "end_time": "06:00"},
                "price_components": [{"type": "ENERGY", "price": 0.3352, "step_size": 1}],
            }],
        }
        result = process.process_location(
            self.ubitricity_location(["generic"]), process.build_tariff_index([tariff]), {},
            official_direct=self.official_direct(), official_msp=self.official_msp(),
        )
        profile = result["connector_options"][0]
        direct = profile["pricing"]["direct_pay"]
        self.assertEqual(direct["basis"], "official_cpo_adhoc")
        self.assertEqual(direct["kwh"], 0.35)
        self.assertEqual(direct["quality"]["decision_grade"], "reliable")
        self.assertNotIn("TARIFF_RESTRICTIONS", direct["quality"]["unmodelled_costs"])

    def test_official_ubitricity_msp_rates_do_not_inherit_unrelated_ndw_restrictions(self):
        tariff = {
            "country_code": "NL", "party_id": "UB2", "id": "generic", "currency": "EUR",
            "elements": [{
                "restrictions": {"start_time": "22:00", "end_time": "06:00"},
                "price_components": [{"type": "ENERGY", "price": 0.3352, "step_size": 1}],
            }],
        }
        result = process.process_location(
            self.ubitricity_location(["generic"]), process.build_tariff_index([tariff]), {},
            official_direct=self.official_direct(), official_msp=self.official_msp(),
        )
        pricing = result["connector_options"][0]["pricing"]
        self.assertEqual(pricing["anwb_free"]["quality"]["decision_grade"], "reliable")
        self.assertEqual(pricing["tap_light"]["quality"]["decision_grade"], "reliable")
        self.assertEqual(pricing["anwb_free"]["quality"]["unmodelled_costs"], [])
        self.assertEqual(pricing["tap_light"]["quality"]["unmodelled_costs"], [])
        self.assertEqual(pricing["shell_basic"]["quality"]["decision_grade"], "indicative")
        self.assertEqual(pricing["vattenfall"]["quality"]["decision_grade"], "exclude")
        self.assertEqual(result["connector_options"][0]["decision_status"], "reliable")

    def test_explicit_ndw_ad_hoc_still_beats_official_cpo_fallback(self):
        tariff = {
            "country_code": "NL", "party_id": "UB2", "id": "adhoc", "currency": "EUR",
            "type": "AD_HOC_PAYMENT",
            "elements": [{"price_components": [{"type": "ENERGY", "price": 0.36, "step_size": 1}]}],
        }
        result = process.process_location(
            self.ubitricity_location(["adhoc"]), process.build_tariff_index([tariff]), {},
            official_direct=self.official_direct(), official_msp=self.official_msp(),
        )
        direct = result["connector_options"][0]["pricing"]["direct_pay"]
        self.assertEqual(direct["basis"], "ndw_ad_hoc")
        self.assertEqual(direct["kwh"], 0.36)
