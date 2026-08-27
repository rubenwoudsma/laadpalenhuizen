#!/usr/bin/env python3
"""Build a compact pricing and data-quality report from huizen-data.json."""

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
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


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
    normalized = value.casefold().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*,\s*", ", ", normalized)
    return normalized


def operator_rows(counter: Counter[tuple[str, str]], examples: dict[tuple[str, str], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = []
    for (party_id, operator), count in sorted(counter.items(), key=lambda item: (-item[1], item[0][1])):
        rows.append(
            {
                "party_id": party_id or "unknown",
                "operator": operator or "Onbekend",
                "count": count,
                "examples": examples.get((party_id, operator), [])[:5],
            }
        )
    return rows


def build_quality_report(dataset: dict[str, Any]) -> dict[str, Any]:
    locations = dataset.get("locations", [])
    total = len(locations)
    generated_at = parse_timestamp(dataset.get("generated_at"))

    pricing_sources: Counter[str] = Counter()
    quote_bases: Counter[str] = Counter()
    confidence: Counter[str] = Counter()
    routes: Counter[str] = Counter()

    direct_gap_counter: Counter[tuple[str, str]] = Counter()
    direct_gap_examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    unknown_base_counter: Counter[tuple[str, str]] = Counter()
    unknown_base_examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    address_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    freshness_24h = 0
    freshness_7d = 0
    freshness_30d = 0
    freshness_invalid = 0
    stale_locations: list[dict[str, Any]] = []

    direct_known = 0
    direct_priced = 0
    direct_priced_ndw = 0
    direct_priced_official = 0
    available_snapshot = 0
    comparison_ready = 0

    for location in locations:
        pricing_source = location.get("pricing_source") or "unknown"
        if location.get("available"):
            available_snapshot += 1
        if len(location.get("pricing", {})) >= 2:
            comparison_ready += 1
        pricing_sources[pricing_source] += 1

        for quote in location.get("pricing", {}).values():
            confidence[quote.get("confidence") or "unknown"] += 1
            quote_bases[quote.get("basis") or "unknown"] += 1
            routes[quote.get("route") or "unknown"] += 1

        direct = location.get("direct_payment", {})
        if direct.get("supported"):
            direct_known += 1
            if direct.get("priced"):
                direct_priced += 1
                direct_quote = location.get("pricing", {}).get("direct_pay", {})
                if direct_quote.get("basis") == "ndw_ad_hoc":
                    direct_priced_ndw += 1
                if direct_quote.get("basis") in {"official_cpo_adhoc", "official_cpo_direct_rule"}:
                    direct_priced_official += 1
            else:
                key = (location.get("party_id") or "unknown", location.get("operator") or "Onbekend")
                direct_gap_counter[key] += 1
                direct_gap_examples[key].append(
                    {
                        "id": location.get("id"),
                        "name": location.get("name"),
                        "address": location.get("address"),
                        "evse_ids": location.get("evse_ids", [])[:4],
                    }
                )

        if pricing_source == "unknown" or location.get("cpo_rate") is None:
            key = (location.get("party_id") or "unknown", location.get("operator") or "Onbekend")
            unknown_base_counter[key] += 1
            unknown_base_examples[key].append(
                {
                    "id": location.get("id"),
                    "name": location.get("name"),
                    "address": location.get("address"),
                    "direct_supported": bool(direct.get("supported")),
                }
            )

        address_key = normalize_address(location.get("address"))
        if address_key:
            address_groups[address_key].append(location)

        updated = parse_timestamp(location.get("last_updated"))
        if generated_at is None or updated is None:
            freshness_invalid += 1
            continue

        age_days = max(0.0, (generated_at - updated).total_seconds() / 86400)
        if age_days > 1:
            freshness_24h += 1
        if age_days > 7:
            freshness_7d += 1
            stale_locations.append(
                {
                    "id": location.get("id"),
                    "address": location.get("address"),
                    "operator": location.get("operator"),
                    "party_id": location.get("party_id"),
                    "statuses": location.get("statuses", []),
                    "last_updated": location.get("last_updated"),
                    "age_days": round(age_days, 1),
                }
            )
        if age_days > 30:
            freshness_30d += 1

    duplicate_groups: list[dict[str, Any]] = []
    possible_transition_count = 0
    duplicate_location_count = 0

    for normalized, group in address_groups.items():
        if len(group) < 2:
            continue

        duplicate_location_count += len(group)
        operators = sorted({item.get("operator") or "Onbekend" for item in group})
        freshness = []
        for item in group:
            updated = parse_timestamp(item.get("last_updated"))
            age_days = None
            if generated_at is not None and updated is not None:
                age_days = max(0.0, (generated_at - updated).total_seconds() / 86400)
            freshness.append(age_days)

        has_stale = any(age is not None and age > 7 for age in freshness)
        has_recent = any(age is not None and age <= 1 for age in freshness)
        possible_transition = len(operators) > 1 and has_stale and has_recent
        if possible_transition:
            possible_transition_count += 1

        reason = "same_operator_multiple_records"
        if possible_transition:
            reason = "possible_operator_transition"
        elif len(operators) > 1:
            reason = "multiple_operators_same_address"

        duplicate_groups.append(
            {
                "address": group[0].get("address"),
                "normalized_address": normalized,
                "count": len(group),
                "operators": operators,
                "reason": reason,
                "locations": [
                    {
                        "id": item.get("id"),
                        "name": item.get("name"),
                        "operator": item.get("operator"),
                        "party_id": item.get("party_id"),
                        "statuses": item.get("statuses", []),
                        "available": item.get("available"),
                        "last_updated": item.get("last_updated"),
                        "evse_ids": item.get("evse_ids", [])[:6],
                    }
                    for item in group
                ],
            }
        )

    duplicate_groups.sort(
        key=lambda row: (
            row["reason"] != "possible_operator_transition",
            -row["count"],
            (row.get("address") or "").casefold(),
        )
    )
    stale_locations.sort(key=lambda row: (-row["age_days"], row.get("address") or ""))

    stats = dataset.get("stats", {})
    unknown_base = sum(unknown_base_counter.values())
    base_priced = max(0, total - unknown_base)

    expected_stats = {
        "total": total,
        "available_snapshot": available_snapshot,
        "comparison_ready": comparison_ready,
        "unknown_base_rate": unknown_base,
        "adhoc_priced": direct_priced,
        "adhoc_priced_ndw": direct_priced_ndw,
        "adhoc_priced_official": direct_priced_official,
        "direct_payment_known": direct_known,
        "msp_quotes_official": quote_bases.get("official_cpo_msp_rate", 0),
    }
    stat_mismatches = []
    for key, computed in expected_stats.items():
        embedded = stats.get(key)
        if embedded is not None and embedded != computed:
            stat_mismatches.append({"field": key, "embedded": embedded, "computed": computed})

    official_sources = dataset.get("official_pricing_harvest", {}).get("sources", [])
    failed_sources = [
        {
            "id": source.get("id"),
            "party_id": source.get("party_id"),
            "status": source.get("status"),
            "error": source.get("error"),
            "source_url": source.get("source_url"),
        }
        for source in official_sources
        if source.get("status") != "ok"
    ]

    quote_total = sum(confidence.values())
    direct_unpriced = max(0, direct_known - direct_priced)

    attention: list[dict[str, Any]] = []
    if stat_mismatches:
        attention.append(
            {
                "severity": "high",
                "category": "dataset_consistency",
                "title": "Ingebouwde stats wijken af van de berekende dataset",
                "count": len(stat_mismatches),
            }
        )
    if failed_sources:
        attention.append(
            {
                "severity": "high",
                "category": "source_verification",
                "title": "Officiele prijsbron kon niet worden geverifieerd",
                "count": len(failed_sources),
            }
        )
    if direct_unpriced:
        attention.append(
            {
                "severity": "medium",
                "category": "direct_pricing",
                "title": "Direct betalen bekend, maar nog zonder prijs",
                "count": direct_unpriced,
            }
        )
    if unknown_base:
        attention.append(
            {
                "severity": "medium",
                "category": "base_tariff",
                "title": "Geen bruikbaar CPO-basistarief",
                "count": unknown_base,
            }
        )
    if freshness_7d:
        attention.append(
            {
                "severity": "medium",
                "category": "freshness",
                "title": "Locatiestatus ouder dan zeven dagen",
                "count": freshness_7d,
            }
        )
    if possible_transition_count:
        attention.append(
            {
                "severity": "medium",
                "category": "duplicates",
                "title": "Mogelijke CPO-wissel of superseded locatie",
                "count": possible_transition_count,
            }
        )
    if confidence.get("low", 0):
        attention.append(
            {
                "severity": "info",
                "category": "confidence",
                "title": "Prijsregels met low confidence",
                "count": confidence.get("low", 0),
            }
        )

    return {
        "schema_version": 1,
        "generated_at": dataset.get("generated_at"),
        "source_dataset": DEFAULT_INPUT,
        "coverage": {
            "total_locations": total,
            "available_snapshot": available_snapshot,
            "comparison_ready": comparison_ready,
            "comparison_ready_pct": percentage(comparison_ready, total),
            "base_tariff_known": base_priced,
            "base_tariff_known_pct": percentage(base_priced, total),
            "unknown_base_tariff": unknown_base,
        },
        "direct_payment": {
            "known": direct_known,
            "priced": direct_priced,
            "priced_pct_of_known": percentage(direct_priced, direct_known),
            "unpriced": direct_unpriced,
            "priced_ndw": direct_priced_ndw,
            "priced_official": direct_priced_official,
            "unpriced_by_operator": operator_rows(direct_gap_counter, direct_gap_examples),
        },
        "confidence": {
            "total_quotes": quote_total,
            "high": confidence.get("high", 0),
            "high_pct": percentage(confidence.get("high", 0), quote_total),
            "medium": confidence.get("medium", 0),
            "medium_pct": percentage(confidence.get("medium", 0), quote_total),
            "low": confidence.get("low", 0),
            "low_pct": percentage(confidence.get("low", 0), quote_total),
            "unknown": confidence.get("unknown", 0),
            "unknown_pct": percentage(confidence.get("unknown", 0), quote_total),
        },
        "pricing_sources": [
            {"id": source, "locations": count, "pct": percentage(count, total)}
            for source, count in sorted(pricing_sources.items(), key=lambda item: (-item[1], item[0]))
        ],
        "quote_bases": [
            {"id": basis, "quotes": count, "pct": percentage(count, quote_total)}
            for basis, count in sorted(quote_bases.items(), key=lambda item: (-item[1], item[0]))
        ],
        "routes": [
            {"id": route, "quotes": count, "pct": percentage(count, quote_total)}
            for route, count in sorted(routes.items(), key=lambda item: (-item[1], item[0]))
        ],
        "unknown_base_tariff": {
            "count": unknown_base,
            "by_operator": operator_rows(unknown_base_counter, unknown_base_examples),
        },
        "freshness": {
            "older_than_24h": freshness_24h,
            "older_than_24h_pct": percentage(freshness_24h, total),
            "older_than_7d": freshness_7d,
            "older_than_7d_pct": percentage(freshness_7d, total),
            "older_than_30d": freshness_30d,
            "missing_or_invalid_timestamp": freshness_invalid,
            "stale_locations_over_7d": stale_locations,
        },
        "potential_duplicates": {
            "group_count": len(duplicate_groups),
            "location_count": duplicate_location_count,
            "possible_operator_transition_groups": possible_transition_count,
            "groups": duplicate_groups,
        },
        "consistency": {
            "embedded_stats_checked": len(expected_stats),
            "mismatch_count": len(stat_mismatches),
            "mismatches": stat_mismatches,
        },
        "official_sources": {
            "total": len(official_sources),
            "ok": len(official_sources) - len(failed_sources),
            "failed": len(failed_sources),
            "failures": failed_sources,
        },
        "attention": attention,
    }


def markdown_summary(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    direct = report["direct_payment"]
    confidence = report["confidence"]
    freshness = report["freshness"]
    duplicates = report["potential_duplicates"]

    lines = [
        "## Pricing & data quality",
        "",
        "| KPI | Resultaat |",
        "| --- | ---: |",
        f"| Vergelijkbare locaties | {coverage['comparison_ready']} / {coverage['total_locations']} ({coverage['comparison_ready_pct']:.1f}%) |",
        f"| CPO-basistarief bekend | {coverage['base_tariff_known']} / {coverage['total_locations']} ({coverage['base_tariff_known_pct']:.1f}%) |",
        f"| Direct / QR geprijsd | {direct['priced']} / {direct['known']} ({direct['priced_pct_of_known']:.1f}%) |",
        f"| Status ouder dan 7 dagen | {freshness['older_than_7d']} |",
        f"| Mogelijke dubbele adressen | {duplicates['group_count']} groepen |",
        f"| Mogelijke CPO-wissels | {duplicates['possible_operator_transition_groups']} groepen |",
        f"| Dataset consistency | {report['consistency']['mismatch_count']} afwijkingen |",
        "",
        "### Quote confidence",
        "",
        f"High: **{confidence['high']}** ({confidence['high_pct']:.1f}%)  ",
        f"Medium: **{confidence['medium']}** ({confidence['medium_pct']:.1f}%)  ",
        f"Low: **{confidence['low']}** ({confidence['low_pct']:.1f}%)",
        "",
    ]

    if direct["unpriced_by_operator"]:
        lines.extend(["### Direct payment known but unpriced", "", "| CPO | party_id | Locaties |", "| --- | --- | ---: |"])
        for row in direct["unpriced_by_operator"]:
            lines.append(f"| {row['operator']} | `{row['party_id']}` | {row['count']} |")
        lines.append("")

    unknown_rows = report["unknown_base_tariff"]["by_operator"]
    if unknown_rows:
        lines.extend(["### Unknown CPO base tariff", "", "| CPO | party_id | Locaties |", "| --- | --- | ---: |"])
        for row in unknown_rows:
            lines.append(f"| {row['operator']} | `{row['party_id']}` | {row['count']} |")
        lines.append("")

    if report["official_sources"]["failed"]:
        lines.append(f"> [!WARNING]\n> {report['official_sources']['failed']} official pricing source(s) could not be verified during this run.\n")

    lines.append("Volledig rapport: `pricing-quality.json` en `kwaliteit.html`.")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input charging dataset")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output quality JSON")
    parser.add_argument("--summary", help="Optional markdown summary file")
    parser.add_argument("--github-summary", action="store_true", help="Append markdown to GITHUB_STEP_SUMMARY when available")
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as handle:
        dataset = json.load(handle)

    report = build_quality_report(dataset)
    output_path = Path(args.output)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, separators=(",", ":"))

    summary = markdown_summary(report)
    if args.summary:
        Path(args.summary).write_text(summary, encoding="utf-8")

    if args.github_summary:
        github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if github_summary:
            with open(github_summary, "a", encoding="utf-8") as handle:
                handle.write(summary)
        else:
            print("GITHUB_STEP_SUMMARY is not set; printing summary instead.")
            print(summary)

    print(
        "Quality report: "
        f"{report['coverage']['comparison_ready_pct']:.1f}% comparison ready, "
        f"{report['direct_payment']['priced_pct_of_known']:.1f}% direct-payment priced, "
        f"{report['freshness']['older_than_7d']} stale >7d, "
        f"{report['potential_duplicates']['group_count']} duplicate-address groups."
    )


if __name__ == "__main__":
    main()
