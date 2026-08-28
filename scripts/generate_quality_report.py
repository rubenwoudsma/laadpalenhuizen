#!/usr/bin/env python3
"""Build Pricing & Data Quality Report v2 from huizen-data.json.

Quality v2 separates four questions that were previously collapsed into a
single confidence label:
  * source quality: how authoritative is the source?
  * price specificity: how specific is the base price to this connector?
  * cost completeness: are all known cost components modelled?
  * decision grade: may this quote support a reliable comparison?

The report is deliberately transparent and does not calculate an arbitrary
single health score.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_INPUT = "huizen-data.json"
DEFAULT_OUTPUT = "pricing-quality.json"


def percentage(part: int, total: int) -> float:
    return round((part / total) * 100, 1) if total else 0.0


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value.casefold().strip())
    return re.sub(r"\s*,\s*", ", ", value)


def operator_rows(counter: Counter[tuple[str, str]], examples: dict[tuple[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for (party_id, operator), count in sorted(counter.items(), key=lambda item: (-item[1], item[0][1])):
        rows.append({
            "party_id": party_id or "unknown",
            "operator": operator or "Onbekend",
            "count": count,
            "examples": examples.get((party_id, operator), [])[:5],
        })
    return rows


def legacy_profile(location: dict[str, Any]) -> dict[str, Any]:
    """Compatibility only until the first schema-v5 Action run.

    Old generated data cannot be upgraded to true connector-level pricing
    without re-reading NDW. Treat it as indicative, never reliable.
    """
    pricing = {}
    for route_id, raw in (location.get("pricing") or {}).items():
        quote = dict(raw)
        old_confidence = quote.get("confidence") or "low"
        quote["quality"] = {
            "source_quality": old_confidence if old_confidence in {"high", "medium", "low"} else "low",
            "price_specificity": "unknown",
            "cost_completeness": "complete" if quote.get("kwh") is not None else "partial",
            "decision_grade": "indicative" if quote.get("kwh") is not None else "exclude",
            "reasons": ["legacy schema: connectorniveau wordt bij de volgende datarun opgebouwd"],
            "unmodelled_costs": [],
        }
        pricing[route_id] = quote
    decision = "indicative" if sum(1 for q in pricing.values() if q["quality"]["decision_grade"] == "indicative") >= 2 else "insufficient"
    return {
        "id": "legacy-location-profile",
        "connector_type": "/".join(location.get("connectors") or []) or "Onbekend",
        "current_type": "UNKNOWN",
        "power_kw": location.get("max_power"),
        "count": max(1, int(location.get("num_evses") or 1)),
        "available_count": 1 if location.get("available") else 0,
        "statuses": location.get("statuses") or [],
        "evse_ids": location.get("evse_ids") or [],
        "last_updated": location.get("last_updated"),
        "tariff": {
            "source": location.get("pricing_source") or "unknown",
            "rate": location.get("cpo_rate"),
            "rate_range": location.get("cpo_rate_range"),
            "quality": {
                "source_quality": "unknown",
                "price_specificity": "unknown",
            },
        },
        "direct_payment": location.get("direct_payment") or {},
        "pricing": pricing,
        "decision_status": decision,
        "legacy": True,
    }


def connector_profiles(location: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = location.get("connector_options")
    if isinstance(profiles, list) and profiles:
        return profiles
    return [legacy_profile(location)]


def profile_example(location: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": location.get("id"),
        "name": location.get("name"),
        "address": location.get("address"),
        "connector": profile.get("connector_type"),
        "current_type": profile.get("current_type"),
        "power_kw": profile.get("power_kw"),
        "evse_ids": (profile.get("evse_ids") or [])[:4],
    }


def dimension_rows(counter: Counter[str], order: list[str]) -> list[dict[str, Any]]:
    total = sum(counter.values())
    keys = [key for key in order if counter.get(key)]
    keys += sorted(key for key in counter if key not in keys)
    return [{"id": key, "count": counter[key], "pct": percentage(counter[key], total)} for key in keys]


def build_quality_report(dataset: dict[str, Any]) -> dict[str, Any]:
    locations = dataset.get("locations") or []
    total_locations = len(locations)
    generated_at = parse_timestamp(dataset.get("generated_at"))
    input_schema = int(dataset.get("schema_version") or 0)
    legacy_mode = input_schema < 5

    profile_source = Counter()
    profile_source_quality = Counter()
    profile_specificity = Counter()
    connector_decision = Counter()
    quote_completeness = Counter()
    quote_decision = Counter()
    quote_bases = Counter()

    direct_gap_counter: Counter[tuple[str, str]] = Counter()
    direct_gap_examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unknown_base_counter: Counter[tuple[str, str]] = Counter()
    unknown_base_examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    blocker_counter = Counter()
    blocker_profile_counter = Counter()
    decision_reason_counter = Counter()
    decision_reason_profile_counter = Counter()

    profile_count = 0
    specific_base_profiles = 0
    direct_known = 0
    direct_priced = 0
    direct_priced_ndw = 0
    direct_priced_official = 0
    direct_decision = Counter()
    msp_quotes_official = 0

    location_decision = Counter()
    available_snapshot = sum(1 for location in locations if location.get("available"))

    address_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    freshness_24h = freshness_7d = freshness_30d = freshness_invalid = 0
    stale_locations: list[dict[str, Any]] = []

    for location in locations:
        profiles = connector_profiles(location)
        profile_count += len(profiles)
        profile_grades = []

        for profile in profiles:
            decision = profile.get("decision_status") or "insufficient"
            if profile.get("legacy"):
                decision = "indicative" if decision != "insufficient" else "insufficient"
            connector_decision[decision] += 1
            profile_grades.append(decision)

            tariff = profile.get("tariff") or {}
            source = tariff.get("source") or "unknown"
            profile_source[source] += 1
            tariff_quality = tariff.get("quality") or {}
            profile_source_quality[tariff_quality.get("source_quality") or "unknown"] += 1
            specificity = tariff_quality.get("price_specificity") or "unknown"
            profile_specificity[specificity] += 1
            if tariff.get("rate") is not None and specificity in {"connector", "network"}:
                specific_base_profiles += 1

            key = (location.get("party_id") or "unknown", location.get("operator") or "Onbekend")
            if tariff.get("rate") is None:
                unknown_base_counter[key] += 1
                unknown_base_examples[key].append(profile_example(location, profile))

            direct = profile.get("direct_payment") or {}
            if direct.get("supported"):
                direct_known += 1
                if direct.get("priced"):
                    direct_priced += 1
                    dq = (profile.get("pricing") or {}).get("direct_pay") or {}
                    direct_grade = (dq.get("quality") or {}).get("decision_grade") or "exclude"
                    direct_decision[direct_grade] += 1
                    if dq.get("basis") in {"ndw_ad_hoc", "ndw_ad_hoc_compatible"}:
                        direct_priced_ndw += 1
                    if dq.get("basis") in {"official_cpo_adhoc", "official_cpo_direct_rule"}:
                        direct_priced_official += 1
                else:
                    direct_gap_counter[key] += 1
                    direct_gap_examples[key].append(profile_example(location, profile))

            profile_blockers: set[str] = set()
            profile_reasons: set[str] = set()
            for route_id, quote in (profile.get("pricing") or {}).items():
                quality = quote.get("quality") or {}
                complete = quality.get("cost_completeness") or "unknown"
                grade = quality.get("decision_grade") or "indicative"
                quote_completeness[complete] += 1
                quote_decision[grade] += 1
                quote_bases[quote.get("basis") or "unknown"] += 1
                if route_id != "direct_pay" and quote.get("basis") == "official_cpo_msp_rate":
                    msp_quotes_official += 1
                for reason in quality.get("reasons") or []:
                    reason = str(reason)
                    decision_reason_counter[reason] += 1
                    profile_reasons.add(reason)
                for reason in quality.get("unmodelled_costs") or []:
                    reason = str(reason)
                    blocker_counter[reason] += 1
                    profile_blockers.add(reason)
                if complete == "partial" and not quality.get("unmodelled_costs"):
                    blocker_counter["onvolledige kostencomponent"] += 1
                    profile_blockers.add("onvolledige kostencomponent")
            for reason in profile_reasons:
                decision_reason_profile_counter[reason] += 1
            for reason in profile_blockers:
                blocker_profile_counter[reason] += 1

        if "reliable" in profile_grades:
            loc_grade = "reliable"
        elif "indicative" in profile_grades:
            loc_grade = "indicative"
        else:
            loc_grade = "insufficient"
        # Recompute the location grade independently so the report validates the producer.
        location_decision[loc_grade] += 1

        address_key = normalize_address(location.get("address"))
        if address_key:
            address_groups[address_key].append(location)

        updated = parse_timestamp(location.get("last_updated"))
        if generated_at is None or updated is None:
            freshness_invalid += 1
        else:
            age_days = max(0.0, (generated_at - updated).total_seconds() / 86400)
            if age_days > 1:
                freshness_24h += 1
            if age_days > 7:
                freshness_7d += 1
                stale_locations.append({
                    "id": location.get("id"),
                    "address": location.get("address"),
                    "operator": location.get("operator"),
                    "party_id": location.get("party_id"),
                    "statuses": location.get("statuses") or [],
                    "last_updated": location.get("last_updated"),
                    "age_days": round(age_days, 1),
                })
            if age_days > 30:
                freshness_30d += 1

    duplicate_groups = []
    possible_transition_count = 0
    duplicate_location_count = 0
    for normalized, group in address_groups.items():
        if len(group) < 2:
            continue
        duplicate_location_count += len(group)
        operators = sorted({item.get("operator") or "Onbekend" for item in group})
        ages = []
        for item in group:
            updated = parse_timestamp(item.get("last_updated"))
            ages.append(max(0.0, (generated_at - updated).total_seconds() / 86400) if generated_at and updated else None)
        possible_transition = len(operators) > 1 and any(a is not None and a > 7 for a in ages) and any(a is not None and a <= 1 for a in ages)
        if possible_transition:
            possible_transition_count += 1
        reason = "possible_operator_transition" if possible_transition else ("multiple_operators_same_address" if len(operators) > 1 else "same_operator_multiple_records")
        duplicate_groups.append({
            "address": group[0].get("address"),
            "normalized_address": normalized,
            "count": len(group),
            "operators": operators,
            "reason": reason,
            "locations": [{
                "id": item.get("id"),
                "name": item.get("name"),
                "operator": item.get("operator"),
                "party_id": item.get("party_id"),
                "statuses": item.get("statuses") or [],
                "available": item.get("available"),
                "last_updated": item.get("last_updated"),
                "evse_ids": (item.get("evse_ids") or [])[:6],
            } for item in group],
        })
    duplicate_groups.sort(key=lambda row: (row["reason"] != "possible_operator_transition", -row["count"], (row.get("address") or "").casefold()))
    stale_locations.sort(key=lambda row: (-row["age_days"], row.get("address") or ""))

    reliable_locations = location_decision.get("reliable", 0)
    indicative_locations = location_decision.get("indicative", 0)
    insufficient_locations = total_locations - reliable_locations - indicative_locations
    comparison_ready = reliable_locations + indicative_locations
    fully_reliable = 0
    if input_schema >= 5:
        for location in locations:
            profiles = connector_profiles(location)
            if profiles and all((profile.get("decision_status") or "insufficient") == "reliable" for profile in profiles):
                fully_reliable += 1
    unknown_base = sum(unknown_base_counter.values())

    expected_stats = {
        "total": total_locations,
        "connector_profiles": profile_count,
        "available_snapshot": available_snapshot,
        "comparison_ready": comparison_ready,
        "decision_ready": reliable_locations,
        "indicative_only": indicative_locations,
        "insufficient": insufficient_locations,
        "fully_reliable": fully_reliable,
        "unknown_base_rate": unknown_base,
        "adhoc_priced": direct_priced,
        "adhoc_priced_ndw": direct_priced_ndw,
        "adhoc_priced_official": direct_priced_official,
        "direct_payment_known": direct_known,
        "msp_quotes_official": msp_quotes_official,
    }
    stat_mismatches = []
    embedded_stats = dataset.get("stats") or {}
    if input_schema >= 5:
        for key, computed in expected_stats.items():
            if key in embedded_stats and embedded_stats[key] != computed:
                stat_mismatches.append({"field": key, "embedded": embedded_stats[key], "computed": computed})

    official_sources = (dataset.get("official_pricing_harvest") or {}).get("sources") or []
    failed_sources = [{
        "id": s.get("id"), "party_id": s.get("party_id"), "status": s.get("status"),
        "error": s.get("error"), "source_url": s.get("source_url")
    } for s in official_sources if s.get("status") != "ok"]

    verification = dataset.get("pricing_rule_verification") or {}
    disabled_rules = verification.get("disabled_rules") or []

    attention = []
    if legacy_mode:
        attention.append({"severity": "medium", "category": "schema", "title": "Dataset wacht nog op eerste connector-level schema-v5 run", "count": 1})
    if stat_mismatches:
        attention.append({"severity": "high", "category": "dataset_consistency", "title": "Ingebouwde stats wijken af van herberekende dataset", "count": len(stat_mismatches)})
    if disabled_rules:
        attention.append({"severity": "high", "category": "pricing_rule_verification", "title": "Prijsregels fail-closed uitgeschakeld na broncontrole", "count": len(disabled_rules)})
    if failed_sources:
        attention.append({"severity": "high", "category": "official_source_harvest", "title": "Officiële CPO-bron kon niet volledig worden geverifieerd", "count": len(failed_sources)})
    if connector_decision.get("insufficient", 0):
        attention.append({"severity": "high", "category": "decision_quality", "title": "Connectorprofielen met onvoldoende informatie voor vergelijking", "count": connector_decision["insufficient"]})
    if direct_known > direct_priced:
        attention.append({"severity": "medium", "category": "direct_payment", "title": "Direct betalen bekend maar nog niet geprijsd", "count": direct_known - direct_priced})
    if direct_decision.get("exclude", 0):
        attention.append({"severity": "medium", "category": "direct_payment_quality", "title": "Direct prijs bekend maar niet rangschikbaar door ontbrekende kosten of voorwaarden", "count": direct_decision["exclude"]})
    if unknown_base:
        attention.append({"severity": "medium", "category": "base_tariff", "title": "Connectorprofielen zonder CPO-basistarief", "count": unknown_base})
    if quote_completeness.get("partial", 0):
        attention.append({"severity": "medium", "category": "cost_completeness", "title": "Prijsroutes met bekende maar nog niet volledig gemodelleerde kosten", "count": quote_completeness["partial"]})
    if freshness_7d:
        attention.append({"severity": "medium", "category": "freshness", "title": "Locatiestatus ouder dan zeven dagen", "count": freshness_7d})
    if possible_transition_count:
        attention.append({"severity": "medium", "category": "multi_record", "title": "Adresgroepen met een operatorovergangssignaal [geen automatische deduplicatie]", "count": possible_transition_count})

    source_rows = [{"id": k, "profiles": v, "pct": percentage(v, profile_count)} for k, v in sorted(profile_source.items(), key=lambda x: (-x[1], x[0]))]

    return {
        "schema_version": 2,
        "generated_at": dataset.get("generated_at"),
        "input_schema_version": input_schema,
        "legacy_profile_fallback": legacy_mode,
        "coverage": {
            "total_locations": total_locations,
            "connector_profiles": profile_count,
            "reliable_locations": reliable_locations,
            "reliable_locations_pct": percentage(reliable_locations, total_locations),
            "indicative_locations": indicative_locations,
            "indicative_locations_pct": percentage(indicative_locations, total_locations),
            "insufficient_locations": insufficient_locations,
            "insufficient_locations_pct": percentage(insufficient_locations, total_locations),
            "comparison_ready": comparison_ready,
            "comparison_ready_pct": percentage(comparison_ready, total_locations),
            "fully_reliable_locations": fully_reliable,
            "available_snapshot": available_snapshot,
        },
        "connector_coverage": {
            "total_profiles": profile_count,
            "reliable": connector_decision.get("reliable", 0),
            "indicative": connector_decision.get("indicative", 0),
            "insufficient": connector_decision.get("insufficient", 0),
            "reliable_pct": percentage(connector_decision.get("reliable", 0), profile_count),
            "indicative_pct": percentage(connector_decision.get("indicative", 0), profile_count),
            "insufficient_pct": percentage(connector_decision.get("insufficient", 0), profile_count),
        },
        "direct_payment": {
            "known_profiles": direct_known,
            "priced_profiles": direct_priced,
            "priced_pct_of_known": percentage(direct_priced, direct_known),
            "rankable_profiles": direct_decision.get("reliable", 0) + direct_decision.get("indicative", 0),
            "rankable_pct_of_known": percentage(direct_decision.get("reliable", 0) + direct_decision.get("indicative", 0), direct_known),
            "reliable_profiles": direct_decision.get("reliable", 0),
            "indicative_profiles": direct_decision.get("indicative", 0),
            "excluded_profiles": direct_decision.get("exclude", 0),
            "ndw_priced_profiles": direct_priced_ndw,
            "official_cpo_priced_profiles": direct_priced_official,
            "supported_unpriced_profiles": max(0, direct_known - direct_priced),
            "unpriced_by_operator": operator_rows(direct_gap_counter, direct_gap_examples),
        },
        "base_pricing": {
            "known_profiles": max(0, profile_count - unknown_base),
            "known_pct": percentage(max(0, profile_count - unknown_base), profile_count),
            "specific_profiles": specific_base_profiles,
            "specific_pct": percentage(specific_base_profiles, profile_count),
            "unknown_profiles": unknown_base,
            "unknown_by_operator": operator_rows(unknown_base_counter, unknown_base_examples),
            "sources": source_rows,
        },
        "data_quality": {
            "high_quality_source_profiles": profile_source_quality.get("high", 0),
            "high_quality_source_pct": percentage(profile_source_quality.get("high", 0), profile_count),
            "base_tariff_known_profiles": max(0, profile_count - unknown_base),
            "base_tariff_known_pct": percentage(max(0, profile_count - unknown_base), profile_count),
            "direct_priced_profiles": direct_priced,
            "direct_known_profiles": direct_known,
            "direct_priced_pct_of_known": percentage(direct_priced, direct_known),
            "complete_price_routes": quote_completeness.get("complete", 0),
            "total_price_routes": sum(quote_completeness.values()),
            "complete_price_routes_pct": percentage(
                quote_completeness.get("complete", 0), sum(quote_completeness.values())
            ),
        },
        "quality_dimensions": {
            "profile_source_quality": dimension_rows(profile_source_quality, ["high", "medium", "low", "unknown"]),
            "profile_price_specificity": dimension_rows(profile_specificity, ["connector", "network", "regional", "national", "operator_estimate", "unknown"]),
            "quote_cost_completeness": dimension_rows(quote_completeness, ["complete", "partial", "unknown"]),
            "quote_decision_grade": dimension_rows(quote_decision, ["reliable", "indicative", "exclude"]),
        },
        "decision_reasons": [{
            "reason": reason,
            "count": decision_reason_profile_counter.get(reason, 0),
            "connector_profiles": decision_reason_profile_counter.get(reason, 0),
            "quote_occurrences": count,
        } for reason, count in decision_reason_counter.most_common()],
        "decision_blockers": [{
            "reason": reason,
            "count": blocker_profile_counter.get(reason, 0),
            "connector_profiles": blocker_profile_counter.get(reason, 0),
            "quote_occurrences": count,
        } for reason, count in blocker_counter.most_common()],
        "official_sources": {
            "total": len(official_sources),
            "ok": len(official_sources) - len(failed_sources),
            "failed": len(failed_sources),
            "failed_sources": failed_sources,
        },
        "pricing_rule_verification": verification,
        "freshness": {
            "older_than_24h": freshness_24h,
            "older_than_7d": freshness_7d,
            "older_than_30d": freshness_30d,
            "missing_or_invalid_timestamp": freshness_invalid,
            "stale_locations_over_7d": stale_locations,
        },
        "potential_duplicates": {
            "group_count": len(duplicate_groups),
            "location_records_in_groups": duplicate_location_count,
            "possible_operator_transition_groups": possible_transition_count,
            "groups": duplicate_groups,
        },
        "consistency": {"ok": not stat_mismatches, "mismatches": stat_mismatches, "recomputed_stats": expected_stats},
        "attention": attention,
    }


def build_github_summary(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    profiles = report["connector_coverage"]
    direct = report["direct_payment"]
    base = report["base_pricing"]
    freshness = report["freshness"]
    verification = report.get("pricing_rule_verification") or {}
    lines = [
        "## Pricing & Data Quality v2",
        "",
        "| KPI | Resultaat |",
        "| --- | ---: |",
        f"| Betrouwbaar vergelijkbare locaties | {coverage['reliable_locations']}/{coverage['total_locations']} ({coverage['reliable_locations_pct']}%) |",
        f"| Indicatieve locaties | {coverage['indicative_locations']}/{coverage['total_locations']} ({coverage['indicative_locations_pct']}%) |",
        f"| Onvoldoende locaties | {coverage['insufficient_locations']}/{coverage['total_locations']} ({coverage['insufficient_locations_pct']}%) |",
        f"| Betrouwbare connectorprofielen | {profiles['reliable']}/{profiles['total_profiles']} ({profiles['reliable_pct']}%) |",
        f"| CPO-basisprijs beschikbaar | {base['known_profiles']}/{profiles['total_profiles']} ({base['known_pct']}%) |",
        f"| Connector-/netwerkspecifieke basisprijs | {base['specific_profiles']}/{profiles['total_profiles']} ({base['specific_pct']}%) |",
        f"| High-quality brondata | {report['data_quality']['high_quality_source_profiles']}/{profiles['total_profiles']} ({report['data_quality']['high_quality_source_pct']}%) |",
        f"| Complete prijsroutes | {report['data_quality']['complete_price_routes']}/{report['data_quality']['total_price_routes']} ({report['data_quality']['complete_price_routes_pct']}%) |",
        f"| Direct/QR rangschikbaar | {direct['rankable_profiles']}/{direct['known_profiles']} ({direct['rankable_pct_of_known']}%) |",
        f"| Direct/QR numeriek geprijsd | {direct['priced_profiles']}/{direct['known_profiles']} ({direct['priced_pct_of_known']}%) |",
        f"| Status ouder dan 7 dagen | {freshness['older_than_7d']} |",
        "",
    ]
    disabled = verification.get("disabled_rules") or []
    if disabled:
        lines += ["### Fail-closed prijsregels", "", ", ".join(f"`{rule}`" for rule in disabled), ""]
    if direct["unpriced_by_operator"]:
        lines += ["### Direct betalen bekend maar ongeprijsd", "", "| CPO | Profielen |", "| --- | ---: |"]
        lines += [f"| {row['operator']} | {row['count']} |" for row in direct["unpriced_by_operator"]]
        lines.append("")
    if base["unknown_by_operator"]:
        lines += ["### CPO-basistarief ontbreekt", "", "| CPO | Profielen |", "| --- | ---: |"]
        lines += [f"| {row['operator']} | {row['count']} |" for row in base["unknown_by_operator"]]
        lines.append("")
    reasons = report.get("decision_reasons") or []
    if reasons:
        lines += ["### Belangrijkste redenen voor indicatief/uitgesloten", "", "| Reden | Connectorprofielen | Prijsroutes |", "| --- | ---: | ---: |"]
        lines += [f"| `{row['reason']}` | {row.get('connector_profiles', row['count'])} | {row.get('quote_occurrences', row['count'])} |" for row in reasons[:8]]
        lines.append("")
    blockers = report.get("decision_blockers") or []
    if blockers:
        lines += ["### Niet-gemodelleerde kosten/voorwaarden", "", "| Blokkade | Connectorprofielen | Prijsroutes |", "| --- | ---: | ---: |"]
        lines += [f"| `{row['reason']}` | {row.get('connector_profiles', row['count'])} | {row.get('quote_occurrences', row['count'])} |" for row in blockers[:8]]
        lines.append("")
    if report["attention"]:
        lines += ["### Aandachtspunten", ""]
        lines += [f"- **{row['severity']}**: {row['title']} ({row['count']})" for row in report["attention"]]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path(DEFAULT_INPUT))
    parser.add_argument("--output", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--github-summary", action="store_true")
    args = parser.parse_args()

    dataset = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_quality_report(dataset)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = build_github_summary(report)
    print(summary)
    if args.github_summary and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")
    return 1 if not report["consistency"]["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
