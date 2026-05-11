#!/usr/bin/env python3
"""
NDW charging point preprocessor for Huizen, NL.

Downloads the two public NDW open data files:
  - charging_point_locations_ocpi.json.gz  (OCPI 2.2.1 locations)
  - charging_point_tariffs_ocpi.json.gz    (OCPI 2.2.1 tariffs)

Filters to a bounding box around Huizen, joins tariffs onto connectors,
and writes huizen-data.json to be served statically by Github Pages.

No API key required. Files are updated daily by NDW.
Run: python3 process.py
"""

import gzip
import json
import math
import urllib.request
import urllib.error
import sys
import os
from datetime import datetime, timezone
from typing import Optional

# ── CONFIG ────────────────────────────────────────────────────────────────────
NDW_BASE = "https://opendata.ndw.nu"
LOCATIONS_URL = f"{NDW_BASE}/charging_point_locations_ocpi.json.gz"
TARIFFS_URL   = f"{NDW_BASE}/charging_point_tariffs_ocpi.json.gz"

OUTPUT_FILE = "huizen-data.json"

# Bounding box: used for fast pre-filter before precise polygon check
LAT_MIN, LAT_MAX = 52.260, 52.325
LNG_MIN, LNG_MAX = 5.175, 5.305

# Municipality boundary polygon (from PDOK/CBS wijkenbuurten 2024)
BOUNDARY_FILE = os.path.join(os.path.dirname(__file__) or ".", "huizen-boundary.geojson")

HEADERS = {
    "User-Agent": "laadpalenhuizen/1.0 (github.com/rubenwoudsma/laadpalenhuizen)",
    "Accept-Encoding": "identity",
}

# ── INDICATIVE MSP RATES [fallback / layered on top of CPO rate] ─────────────
# Used when a tariff_id is present but no usable CPO rate can be extracted from
# the NDW tariffs file, or for MSPs that use a fixed/estimated price model.
#
# IMPORTANT:
# These values are indicative estimates for comparison only. Exact prices can
# differ per charge point, CPO, MSP, roaming agreement, region, start fee,
# blocking fee and subscription. Always verify the actual price in the MSP app.
#
# All prices are €/kWh incl. VAT, AC charging, simplified for map comparison.
# Updated: May 2026.
#
# Notes:
# - NDW CPO tariffs are preferred whenever available.
# - Vattenfall: own-pole and roaming prices can differ by location. The actual
#   price should be checked in the Vattenfall InCharge app or tariffs page.
# - Shell Recharge: uses a fixed-price model in NL, plus possible transaction
#   costs per session. The kWh value below is an estimate for comparison.
# - Allego: prices vary by location and may include overstay/blocking fees.
# - Chargemap: generally applies a service markup on partner-network tariffs.
# - Laadkompas with subscription is modelled as CPO rate without start fee.

PASSES = [
    {"id": "vattenfall", "name": "Vattenfall",     "color": "#16a34a", "monthly": 0},
    {"id": "laadkompas", "name": "Laadkompas",     "color": "#2563eb", "monthly": 4.78},
    {"id": "allego",     "name": "Allego",         "color": "#d97706", "monthly": 0},
    {"id": "shell",      "name": "Shell Recharge", "color": "#e11d48", "monthly": 0},
    {"id": "chargemap",  "name": "Chargemap",      "color": "#7c3aed", "monthly": 0},
]

# Allego own-network AC rate can vary per location. Use a conservative estimate.
ALLEGO_OWN_AC = 0.62

# Shell Recharge fixed AC estimate for NL roaming comparison.
# Shell also charges transaction costs per session, which are not included here.
SHELL_FIXED_OTHER_AC = 0.55

# Chargemap service markup estimate.
CHARGEMAP_MARKUP = 0.10

# Operators where national median can be misleading due to regional/concession rates.
SKIP_OPERATOR_MEDIAN = {"vattenfall incharge", "vattenfall", "nuon"}

# Per-CPO pricing fallbacks when no usable CPO rate is available from NDW tariffs.
# Keyed by lowercase operator name substring.
#
# These are deliberately conservative estimates, not official tariffs.
# Prefer NDW tariff data whenever available.
CPO_FALLBACK = {
    "vattenfall": {
        "vattenfall": 0.39,
        "laadkompas": 0.39,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.43,
    },
    "nuon": {
        "vattenfall": 0.39,
        "laadkompas": 0.39,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.43,
    },
    "allego": {
        "vattenfall": 0.62,
        "laadkompas": 0.60,
        "allego": 0.62,
        "shell": 0.55,
        "chargemap": 0.68,
    },
    "shell": {
        "vattenfall": 0.58,
        "laadkompas": 0.56,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.62,
    },
    "e-flux": {
        "vattenfall": 0.42,
        "laadkompas": 0.40,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.46,
    },
    "road": {
        "vattenfall": 0.42,
        "laadkompas": 0.40,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.46,
    },
    "ev-box": {
        "vattenfall": 0.40,
        "laadkompas": 0.38,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.44,
    },
    "greenflux": {
        "vattenfall": 0.44,
        "laadkompas": 0.42,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.48,
    },
    "ecotap": {
        "vattenfall": 0.36,
        "laadkompas": 0.34,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.40,
    },
    "eneco": {
        "vattenfall": 0.42,
        "laadkompas": 0.40,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.46,
    },
    "last mile": {
        "vattenfall": 0.37,
        "laadkompas": 0.35,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.41,
    },
    "plugwise": {
        "vattenfall": 0.36,
        "laadkompas": 0.34,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.40,
    },
    "default": {
        "vattenfall": 0.42,
        "laadkompas": 0.40,
        "allego": 0.60,
        "shell": 0.55,
        "chargemap": 0.46,
    },
}

def load_boundary() -> list:
    """
    Load gemeente boundary from GeoJSON file.

    Returns a list of polygons. Each polygon is a list of rings.
    The first ring is the outer boundary, optional later rings are holes.
    Supports both Polygon and MultiPolygon GeoJSON.
    """
    with open(BOUNDARY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    geom = data["geometry"]
    geom_type = geom["type"]

    if geom_type == "Polygon":
        return [geom["coordinates"]]

    if geom_type == "MultiPolygon":
        return geom["coordinates"]

    raise ValueError(f"Unsupported geometry type: {geom_type}")


def point_in_polygon(lng: float, lat: float, polygon: list) -> bool:
    """Ray casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside

def point_in_boundary(lng: float, lat: float, boundary: list) -> bool:
    """
    Check whether a point is inside a Polygon or MultiPolygon boundary.

    boundary format:
      [
        [outer_ring, optional_hole_ring, ...],
        [outer_ring, optional_hole_ring, ...],
        ...
      ]
    """
    for polygon in boundary:
        outer_ring = polygon[0]

        if not point_in_polygon(lng, lat, outer_ring):
            continue

        # If the polygon has holes, exclude points inside those holes
        holes = polygon[1:]
        if any(point_in_polygon(lng, lat, hole) for hole in holes):
            continue

        return True

    return False

def fetch_gz(url: str) -> bytes:
    print(f"  Fetching {url} ...", end=" ", flush=True)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        compressed = r.read()
    print(f"{len(compressed) / 1024:.0f} KB compressed")
    return gzip.decompress(compressed)


def get_cpo_rate(tariff_id: str, tariff_map: dict) -> Optional[float]:
    """
    Extract energy price (€/kWh) from an OCPI 2.2.1 tariff object.
    Returns None if not found or not an energy-priced tariff.
    """
    tariff = tariff_map.get(tariff_id)
    if not tariff:
        return None
    for element in tariff.get("elements", []):
        for pc in element.get("price_components", []):
            if pc.get("type") == "ENERGY" and "price" in pc:
                price = float(pc["price"])
                # Apply VAT if not already included
                # OCPI prices are typically excl. VAT; NDW may vary
                # We check for a vat field; if absent, apply 21%
                vat = pc.get("vat")
                if vat is not None:
                    price = round(price * (1 + float(vat) / 100), 4)
                else:
                    price = round(price * 1.21, 4)
                return price
    return None


def get_fallback_pricing(operator_name: str) -> dict:
    lo = (operator_name or "").lower()
    for key, pricing in CPO_FALLBACK.items():
        if key == "default":
            continue
        if key in lo:
            return pricing
    return CPO_FALLBACK["default"]


def build_pricing(cpo_rate: Optional[float], operator_name: str) -> dict:
    """
    Build the 5-pass pricing dict for a connector.

    If we have a real CPO rate from NDW tariffs:
      - vattenfall: CPO rate (concessie; own poles, no start fee)
      - laadkompas: CPO rate (with subscription, no start fee)
      - allego:     ALLEGO_OWN_AC on Allego poles, CPO rate elsewhere
      - shell:      fixed SHELL_FIXED_OTHER_AC (unless own Shell pole)
      - chargemap:  CPO rate * (1 + CHARGEMAP_MARKUP)

    Otherwise fall back to CPO_FALLBACK table.
    """
    if cpo_rate is None:
        return get_fallback_pricing(operator_name)

    op_lower = (operator_name or "").lower()

    # Detect Shell-own poles to use their own rate
    is_shell_pole = "shell" in op_lower
    shell_price = SHELL_FIXED_OTHER_AC

    # Allego uses fixed rate on own network, CPO rate elsewhere
    is_allego_pole = "allego" in op_lower
    allego_price = ALLEGO_OWN_AC if is_allego_pole else round(cpo_rate, 4)

    return {
        "vattenfall":  round(cpo_rate, 4),
        "laadkompas":  round(cpo_rate, 4),
        "allego":      allego_price,
        "shell":       shell_price,
        "chargemap":   round(cpo_rate * (1 + CHARGEMAP_MARKUP), 4),
        "_source":     "ndw",
        "_cpo_rate":   round(cpo_rate, 4),
    }


def best_pass(pricing: dict) -> dict:
    best_id, best_price = None, float("inf")
    for p in PASSES:
        price = pricing.get(p["id"])
        if price is not None and price < best_price:
            best_price = price
            best_id = p["id"]
    return {"pass_id": best_id, "price": best_price}


def connector_type_label(conn: dict) -> str:
    standard = conn.get("standard", "")
    label_map = {
        "IEC_62196_T2":       "Type 2",
        "IEC_62196_T2_COMBO": "CCS",
        "CHADEMO":            "CHAdeMO",
        "DOMESTIC_F":         "Schuko",
        "IEC_62196_T1":       "Type 1",
        "IEC_62196_T1_COMBO": "CCS (T1)",
        "TESLA_S":            "Tesla",
    }
    return label_map.get(standard, standard)


def process_location(
    loc: dict,
    tariff_map: dict,
    operator_median: Optional[dict] = None,
    boundary: Optional[list] = None,
) -> Optional[dict]:
    coords = loc.get("coordinates", {})
    lat = float(coords.get("latitude", 0))
    lng = float(coords.get("longitude", 0))

    # Fast bbox pre-filter
    if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
        return None
    # Precise polygon check
    if boundary and not point_in_boundary(lng, lat, boundary):
        return None

    operator = (loc.get("operator") or {}).get("name", "Onbekend")
    name      = loc.get("name") or loc.get("address") or "Laadpunt"
    address   = loc.get("address", "")
    city      = loc.get("city", "")

    # Flatten all connectors from all EVSEs
    connectors = []
    for evse in loc.get("evses", []):
        evse_id    = evse.get("evse_id", "")
        status     = evse.get("status", "UNKNOWN")  # AVAILABLE, CHARGING, etc.
        for conn in evse.get("connectors", []):
            tariff_ids = conn.get("tariff_ids") or []
            # Find the first tariff that yields a rate
            cpo_rate = None
            used_tariff_id = None
            for tid in tariff_ids:
                rate = get_cpo_rate(tid, tariff_map)
                if rate is not None:
                    cpo_rate = rate
                    used_tariff_id = tid
                    break

            # If no direct tariff, try operator median from nationwide data
            # Skip for operators with known regional pricing (e.g. Vattenfall
            # concessie rates differ per province, so national median is wrong)
            pricing_source_override = None
            if cpo_rate is None and operator_median:
                op_lower = operator.lower()
                if op_lower not in SKIP_OPERATOR_MEDIAN:
                    median = operator_median.get(op_lower)
                    if median is None:
                        for op_key, op_rate in operator_median.items():
                            if op_key in op_lower or op_lower in op_key:
                                median = op_rate
                                break
                    if median is not None:
                        cpo_rate = median
                        pricing_source_override = "operator_median"

            pricing = build_pricing(cpo_rate, operator)
            if pricing_source_override:
                pricing["_source"] = pricing_source_override
            best    = best_pass(pricing)

            connectors.append({
                "evse_id":    evse_id,
                "status":     status,
                "type":       connector_type_label(conn),
                "power_kw":   conn.get("max_electric_power", 0) / 1000 if conn.get("max_electric_power") else conn.get("max_electric_power", 0),
                "tariff_id":  used_tariff_id,
                "pricing":    {k: v for k, v in pricing.items() if not k.startswith("_")},
                "pricing_source": pricing.get("_source", "fallback"),
                "cpo_rate":   pricing.get("_cpo_rate"),
                "best":       best,
            })

    if not connectors:
        return None

    # Location-level availability: available if any connector is AVAILABLE
    statuses = [c["status"] for c in connectors]
    available = "AVAILABLE" in statuses

    # Pick the "best" pass for the location (cheapest across all connectors)
    all_best = [(c["best"]["price"], c["best"]["pass_id"]) for c in connectors if c["best"]["pass_id"]]
    loc_best_price, loc_best_pass = min(all_best) if all_best else (0.39, "vattenfall")

    # Deduplicate connector types for display
    conn_types = list(dict.fromkeys(c["type"] for c in connectors))
    max_power  = max((c["power_kw"] or 0 for c in connectors), default=0)

    return {
        "id":         loc.get("id", ""),
        "name":       name,
        "address":    f"{address}, {city}".strip(", "),
        "lat":        lat,
        "lng":        lng,
        "operator":   operator,
        "connectors": conn_types,
        "max_power":  max_power,
        "num_evses":  len(loc.get("evses", [])),
        "available":  available,
        "statuses":   list(set(statuses)),
        "best": {
            "pass_id": loc_best_pass,
            "price":   loc_best_price,
        },
        # Pricing from the first available connector (representative)
        "pricing":    connectors[0]["pricing"] if connectors else {},
        "pricing_source": connectors[0].get("pricing_source", "fallback") if connectors else "fallback",
        "cpo_rate":   connectors[0].get("cpo_rate") if connectors else None,
    }


def main():
    print("=== NDW Huizen preprocessor ===")

    # ── Download ────────────────────────────────────────────────────────────
    print("\n[1/3] Downloading NDW data files...")
    try:
        locations_raw = fetch_gz(LOCATIONS_URL)
        tariffs_raw   = fetch_gz(TARIFFS_URL)
    except urllib.error.URLError as e:
        print(f"\nERROR: Could not download NDW data: {e}")
        sys.exit(1)

    # ── Parse ────────────────────────────────────────────────────────────────
    print("\n[2/3] Parsing OCPI data...")
    locations_data = json.loads(locations_raw)
    tariffs_data   = json.loads(tariffs_raw)

    # OCPI 2.2.1 response wraps in {"data": [...], "status_code": 1000, ...}
    locations = locations_data.get("data", locations_data) if isinstance(locations_data, dict) else locations_data
    tariffs   = tariffs_data.get("data",   tariffs_data)   if isinstance(tariffs_data,   dict) else tariffs_data

    if not isinstance(locations, list):
        # Some OCPI responses nest further
        locations = locations_data.get("locations", [])
    if not isinstance(tariffs, list):
        tariffs = tariffs_data.get("tariffs", [])

    print(f"  Total NL locations: {len(locations):,}")
    print(f"  Total NL tariffs:   {len(tariffs):,}")

    # Build tariff lookup map: id → tariff object
    tariff_map = {t["id"]: t for t in tariffs if "id" in t}
    print(f"  Tariff IDs indexed: {len(tariff_map):,}")

    # ── Pass 1: collect real CPO rates per operator (nationwide) ────────────
    print("\n[3/4] Collecting per-operator CPO rates from NDW tariffs...")
    operator_rates = {}  # operator_name_lower -> list of rates
    for loc in locations:
        operator = ((loc.get("operator") or {}).get("name") or "").lower()
        if not operator:
            continue
        for evse in loc.get("evses", []):
            for conn in evse.get("connectors", []):
                for tid in (conn.get("tariff_ids") or []):
                    rate = get_cpo_rate(tid, tariff_map)
                    if rate is not None:
                        operator_rates.setdefault(operator, []).append(rate)
                        break

    # Compute median rate per operator
    operator_median = {}
    for op, rates in operator_rates.items():
        sorted_rates = sorted(rates)
        mid = len(sorted_rates) // 2
        operator_median[op] = sorted_rates[mid]
    print(f"  Operators with known rates: {len(operator_median)}")
    for op, rate in sorted(operator_median.items()):
        print(f"    {op}: €{rate:.4f}/kWh ({len(operator_rates[op])} samples)")

    # ── Load municipality boundary ──────────────────────────────────────────
    boundary = None
    try:
        boundary = load_boundary()
        num_polygons = len(boundary)
        num_vertices = sum(len(polygon[0]) for polygon in boundary)
        print(f"  Municipality boundary loaded ({num_polygons} polygons, {num_vertices} outer-ring vertices)")
    except FileNotFoundError:
        print("  WARNING: huizen-boundary.geojson not found, using bbox only")

    # ── Pass 2: filter + process with operator median as extra fallback ────
    print(f"\n[4/4] Filtering to gemeente Huizen...")
    results = []
    ndw_priced = 0
    fallback_priced = 0

    for loc in locations:
        processed = process_location(loc, tariff_map, operator_median, boundary)
        if processed:
            results.append(processed)
            if processed["pricing_source"] in ("ndw", "operator_median"):
                ndw_priced += 1
            else:
                fallback_priced += 1

    print(f"  Locations in area:  {len(results)}")
    print(f"  Real NDW tariffs:   {ndw_priced}  ({ndw_priced/max(len(results),1)*100:.0f}%)")
    print(f"  Fallback pricing:   {fallback_priced}")

    # Operator breakdown
    ops = {}
    for r in results:
        op = r["operator"]
        ops[op] = ops.get(op, 0) + 1
    print("\n  Operators found:")
    for op, count in sorted(ops.items(), key=lambda x: -x[1]):
        print(f"    {op}: {count}")

    # ── Write output ────────────────────────────────────────────────────────
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "NDW open data (opendata.ndw.nu)",
        "bbox": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lng_min": LNG_MIN, "lng_max": LNG_MAX},
        "passes": PASSES,
        "stats": {
            "total": len(results),
            "available": sum(1 for r in results if r["available"]),
            "ndw_priced": ndw_priced,
            "fallback_priced": fallback_priced,
        },
        "locations": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\n✓ Written {OUTPUT_FILE} ({size_kb:.1f} KB)")
    print(f"  {len(results)} locations · {ndw_priced} with real CPO rates")


if __name__ == "__main__":
    main()
