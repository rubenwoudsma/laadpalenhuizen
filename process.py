#!/usr/bin/env python3
"""
NDW charging point preprocessor for Huizen, NL.

Downloads public NDW OCPI location and tariff files, filters them to the
municipality of Huizen, and writes huizen-data.json for the static GitHub Pages
site.

Pricing philosophy:
- NDW CPO energy tariffs are the preferred base price.
- Tariff IDs are resolved in their OCPI country/party scope, with an ID-only
  fallback only when that tariff ID is unique in the national feed.
- OCPI AD_HOC_PAYMENT tariffs are kept separate from regular CPO/MSP tariffs.
  Direct QR/card payment is therefore a first-class price route, never an alias
  for a charge-pass tariff.
- If a regular connector tariff is missing, an operator median may be used as an
  explicitly labelled estimate when enough nationwide samples exist.
- TotalEnergies locations in Huizen use an official MRA-E regional price range
  when NDW does not expose a usable connector tariff. This is a targeted fallback
  based on public concession tariffs, not a generic invented price.
- Charge-pass fees are modelled separately as per-session fees, percentage fees
  and kWh markups, with explicit own-network versus roaming classification.
- The browser calculates session totals for the user's selected amount of energy.

No external Python dependencies are required.
Run: python3 process.py
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import statistics
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

# CONFIG
NDW_BASE = "https://opendata.ndw.nu"
LOCATIONS_URL = f"{NDW_BASE}/charging_point_locations_ocpi.json.gz"
TARIFFS_URL = f"{NDW_BASE}/charging_point_tariffs_ocpi.json.gz"
OUTPUT_FILE = "huizen-data.json"

# Fast pre-filter around municipality Huizen. Precise filtering uses GeoJSON.
LAT_MIN, LAT_MAX = 52.260, 52.325
LNG_MIN, LNG_MAX = 5.175, 5.305

BOUNDARY_FILE = os.path.join(os.path.dirname(__file__) or ".", "huizen-boundary.geojson")

HEADERS = {
    "User-Agent": "laadpalenhuizen (github.com/rubenwoudsma/laadpalenhuizen)",
    "Accept-Encoding": "identity",
}

# A median based on only a handful of samples creates false precision.
MIN_OPERATOR_MEDIAN_SAMPLES = 5

# Operators for which a nationwide median is especially likely to be misleading
# because concession and regional tariffs can differ materially.
SKIP_OPERATOR_MEDIAN = {
    "vattenfall incharge",
    "vattenfall",
    "nuon",
    "totalenergies",
    "total energies",
}

# Huizen participates in the joint public charging approach for Noord-Holland,
# Flevoland and Utrecht through Laadwerk/MRA-E. TotalEnergies publishes current
# concession tariffs for that region. When NDW does not expose a usable tariff
# for a TotalEnergies connector in Huizen, these official regional figures are
# used as an explicitly labelled fallback.
#
# AC: MRA-E 2-5 = EUR 0.48/kWh incl. VAT, MRA-E 6 = EUR 0.36/kWh,
#     MRA-E 6 dynamic = EUR 0.34-0.36/kWh. Without a reliable concession marker
#     per connector we retain the full official EUR 0.34-0.48 range.
# DC: MRA-E = EUR 0.54/kWh incl. VAT.
TOTALENERGIES_MRAE_AC_RANGE = (0.34, 0.48)
TOTALENERGIES_MRAE_DC_RATE = 0.54
TOTALENERGIES_MRAE_VERIFIED_AT = "2026-08-12"
TOTALENERGIES_MRAE_SOURCE_URL = "https://totalenergies.nl/elektrisch-rijden/vind-laadpunt"
HUIZEN_CHARGING_SOURCE_URL = "https://www.huizen.nl/elektrisch-laden"
LAADWERK_SOURCE_URL = "https://www.laadwerk.nl/diensten/laadinfra"

# Supplemental official CPO sources are harvested on every data run. They are
# deliberately limited to rules that can be verified from a public operator
# page without logging in or reverse engineering an app. NDW remains the first
# source for connector-specific OCPI tariffs.
UBITRICITY_MRAE_DIRECT_SOURCE_URL = "https://ubitricity.com/nl/bestuurder/mrae-laadprijzen/"
TOTALENERGIES_DIRECT_RULE_SOURCE_URL = (
    "https://totalenergies.nl/nieuwsoverzicht/blogs-klantverhalen/"
    "totalenergies-betreurt-onverwachte-toeslag-van-laaddienstverlener-voor-e"
)

# OCPI party IDs make CPO matching more reliable than operator-name matching.
# The list is deliberately limited to operators relevant to Huizen or current
# pricing rules. Unknown parties remain usable, they simply do not get an
# invented own-network relationship.
CPO_PARTY_NAMES = {
    "ALL": "Allego",
    "ENE": "Eneco",
    "EFL": "E-Flux by Road",
    "GFX": "TotalEnergies",
    "JEG": "JOLT Energy",
    "LDL": "Lidl",
    "LMS": "EQUANS",
    "LNT": "Laadnet",
    "NUO": "Vattenfall InCharge",
    "TNM": "Shell Recharge",
    "UB2": "Ubitricity",
}

# A corporate relationship is not enough to classify a tariff as own-network.
# For example, Ubitricity is part of Shell but its direct QR price can differ
# from the Shell Recharge app/card price. Only explicit OCPI party matches (or
# a conservative name fallback when party_id is unavailable) are treated as
# own-network.
MSP_HOME_CPO_PARTIES = {
    "vattenfall": {"NUO"},
    "eflux_flex": {"EFL"},
    "shell_basic": {"TNM"},
}

MSP_HOME_OPERATOR_TOKENS = {
    "vattenfall": ("vattenfall", "incharge", "nuon"),
    "eflux_flex": ("e-flux", "e flux"),
    "shell_basic": ("shell recharge",),
}

# Direct-payment support cannot always be inferred from an OCPI capability.
# Ubitricity publicly documents QR/NFC direct access. Other operators are only
# marked as direct-payment capable when NDW exposes an AD_HOC_PAYMENT tariff or
# a recognised payment capability.
KNOWN_DIRECT_PAYMENT_PARTIES = {"UB2"}
KNOWN_DIRECT_PAYMENT_OPERATOR_TOKENS = ("ubitricity",)
DIRECT_PAYMENT_CAPABILITIES = {
    "CREDIT_CARD_PAYABLE",
    "DEBIT_CARD_PAYABLE",
    "PED_TERMINAL",
    "CONTACTLESS_CARD_SUPPORT",
}

# Public charge-pass conditions verified on 2026-08-12.
# The site deliberately compares plans without a monthly subscription so that a
# single charging session can be compared without inventing an amortisation rule.
PASSES = [
    {
        "id": "direct_pay",
        "name": "Direct / QR",
        "plan": "Zonder laadpas",
        "color": "#15803d",
        "monthly_fee": 0.0,
        "summary": "Rechtstreeks betalen bij de laadpaal wanneer een ad-hoc tarief beschikbaar is",
        "verified_at": None,
        "source_url": "https://docs.ndw.nu/en/elektrisch-rijden/",
        "default_selected": True,
        "kind": "direct",
    },
    {
        "id": "anwb_free",
        "name": "ANWB",
        "plan": "Zonder abonnement",
        "color": "#d89b00",
        "monthly_fee": 0.0,
        "summary": "CPO-tarief + €0,89 per sessie",
        "verified_at": "2026-08-12",
        "source_url": "https://www.anwb.nl/auto/elektrisch-rijden/laadpas-abonnement",
        "default_selected": True,
        "kind": "msp",
    },
    {
        "id": "tap_light",
        "name": "Tap Electric",
        "plan": "Light",
        "color": "#0891b2",
        "monthly_fee": 0.0,
        "summary": "CPO-tarief + 5% transactiekosten per sessie",
        "verified_at": "2026-08-12",
        "source_url": "https://tapelectric.app/nl/laadpas/",
        "default_selected": True,
        "kind": "msp",
    },
    {
        "id": "vattenfall",
        "name": "Vattenfall InCharge",
        "plan": "Gratis laadpas",
        "color": "#16a34a",
        "monthly_fee": 0.0,
        "summary": "Eigen netwerk zonder starttarief; roaming + €0,35 per sessie",
        "verified_at": "2026-08-12",
        "source_url": "https://incharge.vattenfall.nl/onze-tarieven",
        "default_selected": True,
        "kind": "msp",
    },
    {
        "id": "eflux_flex",
        "name": "E-Flux by Road",
        "plan": "Flex",
        "color": "#2563eb",
        "monthly_fee": 0.0,
        "summary": "€0,31 per sessie + €0,024/kWh buiten E-Flux",
        "verified_at": "2026-08-12",
        "source_url": "https://www.e-flux.io/nl/tarieven-laadpassen",
        "default_selected": True,
        "kind": "msp",
    },
    {
        "id": "shell_basic",
        "name": "Shell Recharge",
        "plan": "Basic",
        "color": "#dc2626",
        "monthly_fee": 0.0,
        "summary": "Gepubliceerde prijsband + €0,35 per sessie",
        "verified_at": "2026-08-12",
        "source_url": "https://www.shell.nl/elektrisch-opladen/Tarieven.html",
        "default_selected": True,
        "kind": "msp",
    },
    {
        "id": "laadkompas_free",
        "name": "Laadkompas",
        "plan": "Zonder abonnement",
        "color": "#7c3aed",
        "monthly_fee": 0.0,
        "summary": "CPO-tarief + €0,47 per sessie",
        "verified_at": "2026-08-12",
        "source_url": "https://laadkompas.nl/laadpas/zonder-abonnement/",
        "default_selected": True,
        "kind": "msp",
    },
]

PASS_BY_ID = {p["id"]: p for p in PASSES}

# ANWB mentions special discounts on these networks but does not publish one
# universal tariff that can safely be applied to every connector.
ANWB_DISCOUNT_NETWORKS = ("totalenergies", "total energies", "ubitricity", "equans", "ionity")


def load_boundary() -> list:
    """Load municipality boundary from a Polygon or MultiPolygon GeoJSON Feature."""
    with open(BOUNDARY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    geom = data["geometry"]
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    raise ValueError(f"Unsupported geometry type: {geom['type']}")


def point_in_polygon(lng: float, lat: float, polygon: list) -> bool:
    """Ray-casting point-in-polygon test."""
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
    for polygon in boundary:
        if not point_in_polygon(lng, lat, polygon[0]):
            continue
        if any(point_in_polygon(lng, lat, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def fetch_gz(url: str) -> bytes:
    print(f"  Fetching {url} ...", end=" ", flush=True)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=90) as response:
        compressed = response.read()
    print(f"{len(compressed) / 1024:.0f} KB compressed")
    return gzip.decompress(compressed)


class VisibleTextExtractor(HTMLParser):
    """Extract visible text from a public tariff page without dependencies."""

    IGNORED_TAGS = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in self.IGNORED_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0 and data.strip():
            self.parts.append(data)


def normalize_public_page(raw_html: str) -> str:
    """Return stable lower-case visible text for operator tariff parsing."""
    parser = VisibleTextExtractor()
    parser.feed(raw_html)
    text = html.unescape(" ".join(parser.parts)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def fetch_public_page(url: str, timeout: int = 30) -> str:
    """Fetch one public CPO page and return normalized visible text."""
    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.6",
    }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read().decode(charset, errors="replace")
    return normalize_public_page(raw)


def parse_ubitricity_mrae_direct_rate(page_text: str) -> Optional[float]:
    """Extract the Ubitricity MRA-E ad-hoc QR price per kWh."""
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    patterns = [
        r"ad\s*hoc\s+opladen.{0,180}?per\s*kwh.{0,80}?([0-9]+[,.][0-9]{2,4})\s*€",
        r"ad\s*hoc\s+opladen.{0,220}?([0-9]+[,.][0-9]{2,4})\s*€",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return round(float(match.group(1).replace(",", ".")), 4)
    return None


def parse_ubitricity_mrae_msp_rates(page_text: str) -> dict[str, float]:
    """Extract selected MSP kWh rates from the Ubitricity MRA-E table.

    The public page lists providers first and their kWh prices in the same
    order. We only accept the table when all expected provider labels are
    present in order and enough euro amounts follow the last provider. This is
    intentionally strict, a layout change should disable the supplement rather
    than silently map a price to the wrong MSP.
    """
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    start = text.find("rfid / apps")
    if start < 0:
        return {}
    segment = text[start:]
    footnote = segment.find("de getoonde tarieven")
    if footnote >= 0:
        segment = segment[:footnote]

    providers = [
        ("anwb_free", "anwb"),
        ("greenchoice", "greenchoice"),
        ("tap_light", "tap electric"),
        ("essent", "essent"),
        ("movemove", "movemove"),
        ("green_caravan", "green caravan"),
        ("eneco", "eneco"),
        ("shell_basic", "shell recharge"),
        ("vattenfall", "vattenfall incharge"),
        ("mkb_brandstof", "mkb brandstof"),
    ]

    positions = []
    cursor = 0
    for _, label in providers:
        position = segment.find(label, cursor)
        if position < 0:
            return {}
        positions.append(position)
        cursor = position + len(label)

    price_text = segment[cursor:]
    raw_rates = re.findall(r"([0-9]+[,.][0-9]{2,4})\s*€", price_text)
    if len(raw_rates) < len(providers):
        return {}

    values = [round(float(value.replace(",", ".")), 4) for value in raw_rates[:len(providers)]]
    all_rates = {provider_id: rate for (provider_id, _), rate in zip(providers, values)}
    return {
        provider_id: all_rates[provider_id]
        for provider_id in ("anwb_free", "tap_light", "shell_basic", "vattenfall")
    }


def parse_totalenergies_direct_rule(page_text: str) -> bool:
    """Verify that TotalEnergies states CPO base price equals direct price."""
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    return bool(re.search(
        r"basisprijs.{0,100}cpo[- ]?prijs.{0,140}(?:ad[- ]?hoc|direct\s+payment).{0,70}prijs",
        text,
        flags=re.IGNORECASE,
    ))


def harvest_official_pricing(fetcher=fetch_public_page) -> dict:
    """Harvest public operator rules that can safely supplement NDW pricing.

    Failures are intentionally non-fatal. A transient CPO website problem must
    not block the daily NDW snapshot. A source is only applied when the expected
    wording or numeric value can be verified in the current run.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    direct_by_party: dict[str, dict] = {}
    msp_by_party: dict[str, dict] = {}
    results: list[dict] = []

    try:
        page = fetcher(UBITRICITY_MRAE_DIRECT_SOURCE_URL)
        rate = parse_ubitricity_mrae_direct_rate(page)
        msp_rates = parse_ubitricity_mrae_msp_rates(page)
        if rate is None:
            raise ValueError("ad-hoc kWh price not found")
        direct_by_party["UB2"] = {
            "mode": "fixed",
            "rate": rate,
            "session": 0.0,
            "basis": "official_cpo_adhoc",
            "source_id": "ubitricity_mrae_direct",
            "source_url": UBITRICITY_MRAE_DIRECT_SOURCE_URL,
            "source_checked_at": checked_at,
            "confidence": "high",
            "note": "Officieel Ubitricity MRA-E ad-hoc tarief via QR. Lokale parkeer- of tijdkosten zijn niet inbegrepen.",
        }
        if msp_rates:
            msp_by_party["UB2"] = {
                pass_id: {
                    "rate": msp_rate,
                    "basis": "official_cpo_msp_rate",
                    "source_id": "ubitricity_mrae_direct",
                    "source_url": UBITRICITY_MRAE_DIRECT_SOURCE_URL,
                    "source_checked_at": checked_at,
                    "confidence": "medium",
                    "note": (
                        "Ubitricity publiceert voor MRA-E een netwerk-specifiek kWh-tarief voor deze laadpas. "
                        "Eventuele aanvullende laadpas-, aansluit- of parkeerkosten zijn niet in dit kWh-bedrag opgenomen."
                    ),
                }
                for pass_id, msp_rate in msp_rates.items()
            }
        results.append({
            "id": "ubitricity_mrae_direct",
            "party_id": "UB2",
            "status": "ok",
            "rate": rate,
            "msp_rates": msp_rates,
            "msp_table_status": "ok" if msp_rates else "unavailable",
            "source_url": UBITRICITY_MRAE_DIRECT_SOURCE_URL,
        })
    except Exception as exc:
        results.append({
            "id": "ubitricity_mrae_direct",
            "party_id": "UB2",
            "status": "unavailable",
            "error": str(exc),
            "source_url": UBITRICITY_MRAE_DIRECT_SOURCE_URL,
        })

    try:
        page = fetcher(TOTALENERGIES_DIRECT_RULE_SOURCE_URL)
        if not parse_totalenergies_direct_rule(page):
            raise ValueError("direct-payment equals CPO-price rule not found")
        direct_by_party["GFX"] = {
            "mode": "mirror_cpo",
            "basis": "official_cpo_direct_rule",
            "source_id": "totalenergies_direct_payment",
            "source_url": TOTALENERGIES_DIRECT_RULE_SOURCE_URL,
            "source_checked_at": checked_at,
            "note": "TotalEnergies publiceert dat de CPO-basisprijs ook de ad-hoc/direct-payment prijs is.",
        }
        results.append({
            "id": "totalenergies_direct_payment",
            "party_id": "GFX",
            "status": "ok",
            "mode": "mirror_cpo",
            "source_url": TOTALENERGIES_DIRECT_RULE_SOURCE_URL,
        })
    except Exception as exc:
        results.append({
            "id": "totalenergies_direct_payment",
            "party_id": "GFX",
            "status": "unavailable",
            "error": str(exc),
            "source_url": TOTALENERGIES_DIRECT_RULE_SOURCE_URL,
        })

    return {
        "checked_at": checked_at,
        "direct_by_party": direct_by_party,
        "msp_by_party": msp_by_party,
        "sources": results,
    }


# Backwards-compatible name for tests or scripts created before MSP supplements
# were added. New code should use harvest_official_pricing().
def harvest_official_direct_pricing(fetcher=fetch_public_page) -> dict:
    return harvest_official_pricing(fetcher=fetcher)


def supplemental_direct_price_info(
    party_id: str,
    cpo_rate: Optional[float],
    cpo_rate_range: Optional[list[float]],
    cpo_source: str,
    official_direct: Optional[dict],
) -> Optional[dict]:
    """Build an ad-hoc price from a verified public CPO source."""
    source = (official_direct or {}).get((party_id or "").upper())
    if not source:
        return None

    if source.get("mode") == "fixed":
        rate = source.get("rate")
        if rate is None:
            return None
        return {
            "rate": float(rate),
            "range": source.get("range"),
            "session": float(source.get("session", 0.0)),
            "session_range": source.get("session_range"),
            "unmodelled_types": [],
            "basis": source.get("basis", "official_cpo_adhoc"),
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "source_checked_at": source.get("source_checked_at"),
            "confidence": source.get("confidence", "high"),
            "note": source.get("note"),
        }

    if source.get("mode") == "mirror_cpo" and cpo_rate is not None:
        confidence = confidence_for_source(cpo_source)
        if cpo_rate_range and len(cpo_rate_range) == 2 and abs(cpo_rate_range[1] - cpo_rate_range[0]) > 1e-9:
            confidence = downgrade_confidence(confidence)
        return {
            "rate": float(cpo_rate),
            "range": cpo_rate_range,
            "session": 0.0,
            "session_range": None,
            "unmodelled_types": [],
            "basis": source.get("basis", "official_cpo_direct_rule"),
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "source_checked_at": source.get("source_checked_at"),
            "confidence": confidence,
            "note": source.get("note"),
        }

    return None


def price_component_including_vat(component: dict, expected_type: Optional[str] = None) -> Optional[float]:
    """Return one OCPI price component including explicitly supplied VAT.

    OCPI 2.2.1 defines PriceComponent.price excluding VAT and has an optional
    VAT percentage. When VAT is omitted we keep the published value instead of
    inventing a Dutch VAT rate, because the feed itself remains authoritative.
    """
    component_type = str(component.get("type") or "").upper()
    if expected_type and component_type != expected_type.upper():
        return None
    if "price" not in component:
        return None

    try:
        price = float(component["price"])
    except (TypeError, ValueError):
        return None

    vat = component.get("vat")
    if vat is not None:
        try:
            price *= 1 + float(vat) / 100
        except (TypeError, ValueError):
            pass

    return round(price, 4)


def energy_price_including_vat(component: dict) -> Optional[float]:
    """Compatibility wrapper that returns an OCPI ENERGY component price."""
    return price_component_including_vat(component, "ENERGY")


def build_tariff_index(tariffs: list) -> dict:
    """Index OCPI tariffs by country/party/id and by globally unique ID.

    OCPI tariff IDs only need to be unique within a CPO platform. A plain
    ``{id: tariff}`` map can therefore silently associate a connector with the
    wrong tariff when two parties use the same ID. Location country_code and
    party_id provide the correct scope.
    """
    scoped = {}
    by_id: dict[str, list[dict]] = {}

    for tariff in tariffs:
        if not isinstance(tariff, dict) or not tariff.get("id"):
            continue
        tariff_id = str(tariff["id"])
        country = str(tariff.get("country_code") or "").upper()
        party = str(tariff.get("party_id") or "").upper()
        if country and party:
            scoped[(country, party, tariff_id)] = tariff
        by_id.setdefault(tariff_id, []).append(tariff)

    unique = {tariff_id: rows[0] for tariff_id, rows in by_id.items() if len(rows) == 1}
    return {
        "scoped": scoped,
        "unique": unique,
        "id_collisions": sum(1 for rows in by_id.values() if len(rows) > 1),
        "total": len(tariffs),
    }


def get_tariff(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> Optional[dict]:
    """Resolve a tariff in OCPI party scope, then by ID only if globally unique."""
    country = (country_code or "").upper()
    party = (party_id or "").upper()
    if country and party:
        tariff = tariff_index.get("scoped", {}).get((country, party, str(tariff_id)))
        if tariff:
            return tariff
    return tariff_index.get("unique", {}).get(str(tariff_id))


def normalized_tariff_type(tariff: dict) -> str:
    """Treat an omitted OCPI tariff type as a regular tariff."""
    return str(tariff.get("type") or "REGULAR").upper()


def get_tariff_price_info(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
    mode: str = "regular",
) -> Optional[dict]:
    """Return usable ENERGY/FLAT information for a regular or ad-hoc tariff.

    Multiple restricted tariff elements can expose different prices. Rather
    than picking one silently, the midpoint and the full observed range are
    retained so the browser can propagate pricing uncertainty to session cost.
    TIME and PARKING_TIME components are flagged but are not converted to an
    invented amount because their cost depends on session duration.
    """
    tariff = get_tariff(tariff_id, tariff_index, country_code, party_id)
    if not tariff:
        return None

    tariff_type = normalized_tariff_type(tariff)
    if mode == "ad_hoc" and tariff_type != "AD_HOC_PAYMENT":
        return None
    if mode == "regular" and tariff_type == "AD_HOC_PAYMENT":
        return None

    energy_rates: list[float] = []
    flat_fees: list[float] = []
    unmodelled_types: set[str] = set()

    for element in tariff.get("elements", []):
        for component in element.get("price_components", []):
            component_type = str(component.get("type") or "").upper()
            value = price_component_including_vat(component)
            if value is None:
                continue
            if component_type == "ENERGY":
                energy_rates.append(value)
            elif component_type == "FLAT":
                flat_fees.append(value)
            elif component_type in {"TIME", "PARKING_TIME"}:
                unmodelled_types.add(component_type)

    energy_rates = sorted(set(energy_rates))
    flat_fees = sorted(set(flat_fees))
    if not energy_rates:
        return None

    low_rate, high_rate = energy_rates[0], energy_rates[-1]
    low_flat = flat_fees[0] if flat_fees else 0.0
    high_flat = flat_fees[-1] if flat_fees else 0.0
    return {
        "rate": round((low_rate + high_rate) / 2, 4),
        "range": [low_rate, high_rate] if len(energy_rates) > 1 else None,
        "session": round((low_flat + high_flat) / 2, 4),
        "session_range": [low_flat, high_flat] if len(flat_fees) > 1 else None,
        "tariff_type": tariff_type,
        "tariff_id": str(tariff_id),
        "unmodelled_types": sorted(unmodelled_types),
    }


def get_cpo_rates(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> list[float]:
    """Return all distinct regular OCPI ENERGY prices for a resolved tariff."""
    tariff = get_tariff(tariff_id, tariff_index, country_code, party_id)
    if not tariff or normalized_tariff_type(tariff) == "AD_HOC_PAYMENT":
        return []

    rates = []
    for element in tariff.get("elements", []):
        for component in element.get("price_components", []):
            rate = energy_price_including_vat(component)
            if rate is not None:
                rates.append(rate)
    return sorted(set(rates))


def get_cpo_rate(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> Optional[float]:
    """Return one usable regular ENERGY price for median building and compatibility."""
    rates = get_cpo_rates(tariff_id, tariff_index, country_code, party_id)
    return rates[0] if rates else None


def get_cpo_price_info(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> Optional[dict]:
    """Return regular CPO tariff information, excluding AD_HOC_PAYMENT tariffs."""
    return get_tariff_price_info(tariff_id, tariff_index, country_code, party_id, mode="regular")


def get_ad_hoc_price_info(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> Optional[dict]:
    """Return only an explicit OCPI AD_HOC_PAYMENT tariff."""
    return get_tariff_price_info(tariff_id, tariff_index, country_code, party_id, mode="ad_hoc")


def operator_key(name: str) -> str:
    return " ".join((name or "").lower().split())


def is_msp_home_network(pass_id: str, operator_name: str, party_id: str = "") -> bool:
    """Return whether an MSP tariff is clearly on its own CPO network."""
    party = (party_id or "").upper()
    if party:
        return party in MSP_HOME_CPO_PARTIES.get(pass_id, set())
    op = operator_key(operator_name)
    return any(token in op for token in MSP_HOME_OPERATOR_TOKENS.get(pass_id, ()))


def direct_payment_supported(operator_name: str, party_id: str, capabilities: set[str], has_ad_hoc_tariff: bool) -> tuple[bool, str]:
    """Determine whether direct payment is known without inventing a live price."""
    if has_ad_hoc_tariff:
        return True, "ocpi_ad_hoc_tariff"
    if capabilities & DIRECT_PAYMENT_CAPABILITIES:
        return True, "ocpi_payment_capability"
    party = (party_id or "").upper()
    if party in KNOWN_DIRECT_PAYMENT_PARTIES:
        return True, "operator_documentation"
    op = operator_key(operator_name)
    if any(token in op for token in KNOWN_DIRECT_PAYMENT_OPERATOR_TOKENS):
        return True, "operator_documentation"
    return False, "not_confirmed"


def find_operator_median(operator_name: str, medians: dict) -> Optional[float]:
    key = operator_key(operator_name)
    if key in SKIP_OPERATOR_MEDIAN:
        return None
    if key in medians:
        return medians[key]

    # Conservative fuzzy match for small naming differences.
    for candidate, rate in medians.items():
        if candidate in key or key in candidate:
            return rate
    return None


def confidence_for_source(source: str) -> str:
    if source == "ndw":
        return "high"
    if source in {"operator_median", "totalenergies_mrae", "totalenergies_mrae_dc"}:
        return "medium"
    return "low"


def downgrade_confidence(value: str) -> str:
    return {"high": "medium", "medium": "low", "low": "low"}.get(value, "low")


def merge_notes(*notes: Optional[str]) -> Optional[str]:
    values = [note.strip() for note in notes if note and note.strip()]
    return " ".join(values) or None


def shifted_range(price_range: Optional[list[float] | tuple[float, float]], delta: float = 0.0) -> Optional[list[float]]:
    if not price_range or len(price_range) != 2:
        return None
    return [round(float(price_range[0]) + delta, 4), round(float(price_range[1]) + delta, 4)]


def totalenergies_mrae_fallback(operator_name: str, power_kw: float) -> Optional[dict]:
    """Return official MRA-E pricing for TotalEnergies when NDW is incomplete.

    The municipality of Huizen works through Laadwerk in the Noord-Holland,
    Flevoland and Utrecht public charging collaboration. TotalEnergies publishes
    different MRA-E AC tariffs by concession, but the NDW location does not
    reliably expose which concession applies. Therefore AC remains a range.
    """
    op = operator_key(operator_name)
    if "totalenergies" not in op and "total energies" not in op:
        return None

    if power_kw >= 50:
        return {
            "rate": TOTALENERGIES_MRAE_DC_RATE,
            "range": None,
            "source": "totalenergies_mrae_dc",
            "note": "Officieel TotalEnergies MRA-E DC-tarief voor Noord-Holland, Flevoland en Utrecht.",
        }

    low, high = TOTALENERGIES_MRAE_AC_RANGE
    return {
        "rate": round((low + high) / 2, 4),
        "range": [low, high],
        "source": "totalenergies_mrae",
        "note": "Officiële MRA-E prijsband van TotalEnergies; de exacte concessie en eventuele dynamische prijs zijn niet uit NDW af te leiden.",
    }


def make_quote(
    kwh: float,
    session: float,
    confidence: str,
    basis: str,
    note: Optional[str] = None,
    price_range: Optional[list[float]] = None,
    transaction_percentage: float = 0.0,
    session_range: Optional[list[float]] = None,
    route: Optional[str] = None,
    relation: Optional[str] = None,
) -> dict:
    quote = {
        "kwh": round(float(kwh), 4),
        "session": round(float(session), 4),
        "confidence": confidence,
        "basis": basis,
    }
    if note:
        quote["note"] = note
    if price_range:
        quote["range"] = [round(float(v), 4) for v in price_range]
    if transaction_percentage:
        quote["percentage"] = round(float(transaction_percentage), 4)
    if session_range:
        quote["session_range"] = [round(float(v), 4) for v in session_range]
    if route:
        quote["route"] = route
    if relation:
        quote["relation"] = relation
    return quote


def apply_source_metadata(quote: dict, source: Optional[dict]) -> dict:
    """Copy safe provenance fields from a harvested source to a quote."""
    for key in ("source_id", "source_url", "source_checked_at"):
        if source and source.get(key):
            quote[key] = source[key]
    return quote


def build_pricing(
    cpo_rate: Optional[float],
    cpo_source: str,
    operator_name: str,
    max_power_kw: float = 0,
    cpo_rate_range: Optional[list[float]] = None,
    cpo_note: Optional[str] = None,
    party_id: str = "",
    direct_price_info: Optional[dict] = None,
    msp_price_overrides: Optional[dict] = None,
) -> dict:
    """Build direct-payment and MSP price components for one location.

    The route is deliberately explicit. Direct/ad-hoc payment is sourced from
    an OCPI AD_HOC_PAYMENT tariff. MSP quotes are classified as own-network or
    roaming using CPO party IDs where possible. Corporate ownership alone does
    not make two tariff routes equivalent.
    """
    pricing: dict[str, dict] = {}
    op = operator_key(operator_name)
    msp_overrides = msp_price_overrides or {}
    base_confidence = confidence_for_source(cpo_source)
    if cpo_rate_range and len(cpo_rate_range) == 2 and abs(cpo_rate_range[1] - cpo_rate_range[0]) > 1e-9:
        base_confidence = downgrade_confidence(base_confidence)

    if direct_price_info:
        direct_confidence = direct_price_info.get("confidence") or "high"
        if direct_price_info.get("confidence") is None and (
            direct_price_info.get("range") or direct_price_info.get("session_range")
        ):
            direct_confidence = "medium"
        direct_note = direct_price_info.get("note")
        if direct_price_info.get("unmodelled_types"):
            direct_confidence = downgrade_confidence(direct_confidence)
            direct_note = merge_notes(
                direct_note,
                "Tijd- of parkeerkosten staan in het tarief maar zijn niet in het sessietotaal opgenomen.",
            )
        direct_quote = make_quote(
            direct_price_info["rate"],
            direct_price_info.get("session", 0.0),
            direct_confidence,
            direct_price_info.get("basis", "ndw_ad_hoc"),
            note=direct_note,
            price_range=direct_price_info.get("range"),
            session_range=direct_price_info.get("session_range"),
            route="ad_hoc",
            relation="cpo_direct",
        )
        pricing["direct_pay"] = apply_source_metadata(direct_quote, direct_price_info)

    # ANWB free plan: use a network-specific official MSP rate when a CPO
    # publishes one, otherwise fall back to CPO price + EUR 0.89/session.
    anwb_override = msp_overrides.get("anwb_free")
    if cpo_rate is not None or anwb_override:
        anwb_rate = float(anwb_override["rate"]) if anwb_override else cpo_rate
        anwb_confidence = anwb_override.get("confidence", "medium") if anwb_override else base_confidence
        anwb_note = anwb_override.get("note") if anwb_override else cpo_note
        anwb_basis = anwb_override.get("basis", cpo_source) if anwb_override else cpo_source
        anwb_range = anwb_override.get("range") if anwb_override else shifted_range(cpo_rate_range)
        if not anwb_override and any(token in op for token in ANWB_DISCOUNT_NETWORKS):
            anwb_confidence = downgrade_confidence(anwb_confidence)
            anwb_note = merge_notes(
                anwb_note,
                "ANWB noemt korting op dit netwerk; de app kan een lager tarief tonen.",
            )
        quote = make_quote(
            anwb_rate,
            0.89,
            anwb_confidence,
            anwb_basis,
            note=anwb_note,
            price_range=anwb_range,
            route="msp_roaming",
            relation="roaming",
        )
        pricing["anwb_free"] = apply_source_metadata(quote, anwb_override)

    # Tap Electric Light: official network-specific kWh rate when available,
    # plus Tap's published 5% transaction fee.
    tap_override = msp_overrides.get("tap_light")
    if cpo_rate is not None or tap_override:
        tap_rate = float(tap_override["rate"]) if tap_override else cpo_rate
        tap_confidence = tap_override.get("confidence", "medium") if tap_override else base_confidence
        tap_basis = tap_override.get("basis", cpo_source) if tap_override else cpo_source
        tap_note = merge_notes(
            tap_override.get("note") if tap_override else cpo_note,
            "Tap Light rekent 5% transactiekosten over het gemodelleerde kWh-tarief.",
        )
        quote = make_quote(
            tap_rate,
            0.0,
            tap_confidence,
            tap_basis,
            note=tap_note,
            price_range=tap_override.get("range") if tap_override else shifted_range(cpo_rate_range),
            transaction_percentage=0.05,
            route="msp_roaming",
            relation="roaming",
        )
        pricing["tap_light"] = apply_source_metadata(quote, tap_override)

    # Vattenfall: own InCharge network versus roaming is matched by OCPI party.
    # A verified CPO-published MSP rate can replace the generic roaming estimate.
    vf_override = msp_overrides.get("vattenfall")
    if cpo_rate is not None or vf_override:
        own_vattenfall = is_msp_home_network("vattenfall", operator_name, party_id)
        vf_rate = float(vf_override["rate"]) if vf_override else cpo_rate
        vf_confidence = vf_override.get("confidence", "medium") if vf_override else (
            base_confidence if own_vattenfall else downgrade_confidence(base_confidence)
        )
        vf_basis = vf_override.get("basis", cpo_source) if vf_override else cpo_source
        vf_note = vf_override.get("note") if vf_override else cpo_note
        if not own_vattenfall and not vf_override:
            vf_note = merge_notes(
                vf_note,
                "Roaming kWh-tarief kan in de InCharge-app afwijken van het CPO-basistarief.",
            )
        quote = make_quote(
            vf_rate,
            0.0 if own_vattenfall else 0.35,
            vf_confidence,
            vf_basis,
            note=vf_note,
            price_range=vf_override.get("range") if vf_override else shifted_range(cpo_rate_range),
            route="msp_home" if own_vattenfall else "msp_roaming",
            relation="own_network" if own_vattenfall else "roaming",
        )
        pricing["vattenfall"] = apply_source_metadata(quote, vf_override)

    # E-Flux Flex: EUR 0.31/session, plus EUR 0.024/kWh outside E-Flux.
    if cpo_rate is not None:
        own_eflux = is_msp_home_network("eflux_flex", operator_name, party_id)
        markup = 0.0 if own_eflux else 0.024
        ef_confidence = base_confidence if own_eflux else downgrade_confidence(base_confidence)
        ef_note = cpo_note if own_eflux else merge_notes(
            cpo_note,
            "Op sommige clearingnetwerken kan E-Flux nog €0,48 extra per sessie rekenen.",
        )
        pricing["eflux_flex"] = make_quote(
            cpo_rate + markup,
            0.31,
            ef_confidence,
            cpo_source,
            note=ef_note,
            price_range=shifted_range(cpo_rate_range, markup),
            route="msp_home" if own_eflux else "msp_roaming",
            relation="own_network" if own_eflux else "roaming",
        )

    # Shell Recharge Basic publishes fixed price bands. A verified CPO-published
    # Shell MSP rate takes precedence for that network. Shell/Ubitricity group
    # ownership still does not imply a home-network relationship.
    is_dc = max_power_kw >= 50
    own_shell = is_msp_home_network("shell_basic", operator_name, party_id)
    shell_override = msp_overrides.get("shell_basic") if not is_dc else None
    if shell_override:
        quote = make_quote(
            float(shell_override["rate"]),
            0.35,
            shell_override.get("confidence", "medium"),
            shell_override.get("basis", "official_cpo_msp_rate"),
            note=shell_override.get("note"),
            price_range=shell_override.get("range"),
            route="msp_home" if own_shell else "msp_roaming",
            relation="own_network" if own_shell else "roaming",
        )
        pricing["shell_basic"] = apply_source_metadata(quote, shell_override)
    elif is_dc:
        if own_shell:
            pricing["shell_basic"] = make_quote(
                0.78,
                0.35,
                "medium",
                "published_shell",
                note="Gepubliceerd Shell Recharge Basic snellaadtarief in Nederland.",
                route="msp_home",
                relation="own_network",
            )
        else:
            pricing["shell_basic"] = make_quote(
                0.82,
                0.35,
                "low",
                "published_band",
                note="Midden van Shells gepubliceerde DC-prijsband; exacte paalprijs staat in de Shell-app.",
                price_range=[0.79, 0.85],
                route="msp_roaming",
                relation="roaming",
            )
    else:
        pricing["shell_basic"] = make_quote(
            0.55,
            0.35,
            "low",
            "published_band",
            note=(
                "Shell publiceert voor reguliere AC-laders een prijsband; de exacte paalprijs staat in de Shell-app."
            ),
            price_range=[0.50, 0.60],
            route="msp_home" if own_shell else "msp_roaming",
            relation="own_network" if own_shell else "roaming",
        )

    # Laadkompas without subscription: CPO price + EUR 0.47/session.
    if cpo_rate is not None:
        pricing["laadkompas_free"] = make_quote(
            cpo_rate,
            0.47,
            base_confidence,
            cpo_source,
            note=cpo_note,
            price_range=shifted_range(cpo_rate_range),
            route="msp_roaming",
            relation="roaming",
        )

    return pricing


def connector_type_label(conn: dict) -> str:
    standard = conn.get("standard", "")
    return {
        "IEC_62196_T2": "Type 2",
        "IEC_62196_T2_COMBO": "CCS",
        "CHADEMO": "CHAdeMO",
        "DOMESTIC_F": "Schuko",
        "IEC_62196_T1": "Type 1",
        "IEC_62196_T1_COMBO": "CCS (T1)",
        "TESLA_S": "Tesla",
    }.get(standard, standard)


def connector_power_kw(conn: dict) -> float:
    value = conn.get("max_electric_power")
    if not value:
        return 0.0
    try:
        return round(float(value) / 1000, 1)
    except (TypeError, ValueError):
        return 0.0


def process_location(
    loc: dict,
    tariff_map: dict,
    operator_median: Optional[dict] = None,
    boundary: Optional[list] = None,
    official_direct: Optional[dict] = None,
    official_msp: Optional[dict] = None,
) -> Optional[dict]:
    coords = loc.get("coordinates", {})
    try:
        lat = float(coords.get("latitude", 0))
        lng = float(coords.get("longitude", 0))
    except (TypeError, ValueError):
        return None

    if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
        return None
    if boundary and not point_in_boundary(lng, lat, boundary):
        return None

    operator = (loc.get("operator") or {}).get("name", "Onbekend")
    country_code = str(loc.get("country_code") or "").upper()
    party_id = str(loc.get("party_id") or "").upper()
    name = loc.get("name") or loc.get("address") or "Laadpunt"
    address = loc.get("address", "")
    city = loc.get("city", "")

    evse_ids = []
    capabilities: set[str] = set()
    connectors = []
    for evse in loc.get("evses", []):
        evse_id = evse.get("evse_id")
        if evse_id:
            evse_ids.append(str(evse_id))
        capabilities.update(str(value).upper() for value in (evse.get("capabilities") or []) if value)
        status = evse.get("status", "UNKNOWN")

        for conn in evse.get("connectors", []):
            cpo_rate = None
            cpo_rate_range = None
            cpo_note = None
            used_tariff_id = None
            source = "unknown"
            power_kw = connector_power_kw(conn)
            direct_price_info = None

            tariff_ids = conn.get("tariff_ids") or []
            for tariff_id in tariff_ids:
                price_info = get_cpo_price_info(tariff_id, tariff_map, country_code, party_id)
                if price_info is not None:
                    cpo_rate = price_info["rate"]
                    cpo_rate_range = price_info["range"]
                    used_tariff_id = tariff_id
                    source = "ndw"
                    break

            for tariff_id in tariff_ids:
                ad_hoc_info = get_ad_hoc_price_info(tariff_id, tariff_map, country_code, party_id)
                if ad_hoc_info is not None:
                    direct_price_info = ad_hoc_info
                    break

            # Targeted official fallback for the dominant public CPO in Huizen.
            # It is used only after a direct regular NDW tariff lookup failed.
            if cpo_rate is None:
                regional = totalenergies_mrae_fallback(operator, power_kw)
                if regional:
                    cpo_rate = regional["rate"]
                    cpo_rate_range = regional["range"]
                    cpo_note = regional["note"]
                    source = regional["source"]

            if cpo_rate is None and operator_median:
                median = find_operator_median(operator, operator_median)
                if median is not None:
                    cpo_rate = median
                    source = "operator_median"

            connectors.append(
                {
                    "status": status,
                    "type": connector_type_label(conn),
                    "power_kw": power_kw,
                    "tariff_id": used_tariff_id,
                    "cpo_rate": cpo_rate,
                    "cpo_rate_range": cpo_rate_range,
                    "pricing_source": source,
                    "pricing_note": cpo_note,
                    "direct_price_info": direct_price_info,
                }
            )

    if not connectors:
        return None

    statuses = [c["status"] for c in connectors]
    available = "AVAILABLE" in statuses
    connector_types = list(dict.fromkeys(c["type"] for c in connectors if c["type"]))
    max_power = max((c["power_kw"] for c in connectors), default=0.0)

    # Prefer a connector with a regular NDW tariff, then regional/median data.
    source_rank = {
        "ndw": 4,
        "totalenergies_mrae_dc": 3,
        "totalenergies_mrae": 3,
        "operator_median": 2,
        "unknown": 0,
    }
    representative = max(connectors, key=lambda c: source_rank.get(c["pricing_source"], 0))
    cpo_rate = representative["cpo_rate"]
    pricing_source = representative["pricing_source"]
    pricing_note = representative.get("pricing_note")

    explicit_range = representative.get("cpo_rate_range")
    if explicit_range:
        cpo_range = explicit_range
    else:
        known_rates = sorted({
            round(c["cpo_rate"], 4)
            for c in connectors
            if c["cpo_rate"] is not None and c["pricing_source"] == pricing_source
        })
        cpo_range = [known_rates[0], known_rates[-1]] if len(known_rates) > 1 else None

    direct_candidates = [c["direct_price_info"] for c in connectors if c.get("direct_price_info")]
    direct_price_info = direct_candidates[0] if direct_candidates else None

    # Explicit OCPI ad-hoc tariffs remain authoritative. Only if NDW has no
    # connector-specific ad-hoc tariff do we use a currently verified official
    # operator source.
    if direct_price_info is None:
        direct_price_info = supplemental_direct_price_info(
            party_id,
            cpo_rate,
            cpo_range,
            pricing_source,
            official_direct,
        )

    direct_supported, direct_reason = direct_payment_supported(
        operator,
        party_id,
        capabilities,
        has_ad_hoc_tariff=direct_price_info is not None,
    )
    if direct_price_info and direct_price_info.get("basis") != "ndw_ad_hoc":
        direct_reason = "official_operator_source"

    pricing = build_pricing(
        cpo_rate,
        pricing_source,
        operator,
        max_power,
        cpo_rate_range=cpo_range,
        cpo_note=pricing_note,
        party_id=party_id,
        direct_price_info=direct_price_info,
        msp_price_overrides=(official_msp or {}).get(party_id),
    )

    last_updated_values = [
        value
        for value in [loc.get("last_updated"), *(evse.get("last_updated") for evse in loc.get("evses", []))]
        if value
    ]
    last_updated = max(last_updated_values) if last_updated_values else None

    direct_payment = {
        "supported": direct_supported,
        "reason": direct_reason,
        "priced": direct_price_info is not None,
    }
    if direct_price_info:
        direct_payment.update({
            "tariff_id": direct_price_info.get("tariff_id"),
            "rate": direct_price_info.get("rate"),
            "rate_range": direct_price_info.get("range"),
            "session": direct_price_info.get("session", 0.0),
            "session_range": direct_price_info.get("session_range"),
            "unmodelled_types": direct_price_info.get("unmodelled_types", []),
            "basis": direct_price_info.get("basis", "ndw_ad_hoc"),
            "source_id": direct_price_info.get("source_id"),
            "source_url": direct_price_info.get("source_url"),
            "source_checked_at": direct_price_info.get("source_checked_at"),
        })

    return {
        "id": loc.get("id", ""),
        "name": name,
        "address": f"{address}, {city}".strip(", "),
        "lat": lat,
        "lng": lng,
        "operator": operator,
        "country_code": country_code,
        "party_id": party_id,
        "cpo_label": CPO_PARTY_NAMES.get(party_id, operator),
        "evse_ids": sorted(set(evse_ids)),
        "connectors": connector_types,
        "max_power": max_power,
        "num_evses": len(loc.get("evses", [])),
        "available": available,
        "statuses": sorted(set(statuses)),
        "last_updated": last_updated,
        "direct_payment": direct_payment,
        "pricing": pricing,
        "pricing_source": pricing_source,
        "pricing_note": pricing_note,
        "cpo_rate": cpo_rate,
        "cpo_rate_range": cpo_range,
    }


def unwrap_ocpi_list(payload, fallback_key: str) -> list:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data
    value = payload.get(fallback_key, [])
    return value if isinstance(value, list) else []


def build_operator_medians(locations: list, tariff_map: dict) -> tuple[dict, dict]:
    operator_rates: dict[str, list[float]] = {}
    for loc in locations:
        operator = operator_key((loc.get("operator") or {}).get("name", ""))
        if not operator:
            continue
        country_code = str(loc.get("country_code") or "")
        party_id = str(loc.get("party_id") or "")
        for evse in loc.get("evses", []):
            for conn in evse.get("connectors", []):
                for tariff_id in conn.get("tariff_ids") or []:
                    rate = get_cpo_rate(tariff_id, tariff_map, country_code, party_id)
                    if rate is not None:
                        operator_rates.setdefault(operator, []).append(rate)
                        break

    medians = {}
    for operator, rates in operator_rates.items():
        if len(rates) >= MIN_OPERATOR_MEDIAN_SAMPLES and operator not in SKIP_OPERATOR_MEDIAN:
            medians[operator] = round(float(statistics.median(rates)), 4)
    return medians, operator_rates


def totalenergies_diagnostics(locations: list, tariff_map: dict, boundary: Optional[list]) -> dict:
    """Measure where TotalEnergies tariff resolution fails inside Huizen."""
    stats = {
        "locations": 0,
        "connectors": 0,
        "with_tariff_ids": 0,
        "resolved_energy_tariff": 0,
    }
    for loc in locations:
        operator = (loc.get("operator") or {}).get("name", "")
        if not totalenergies_mrae_fallback(operator, 0):
            continue
        coords = loc.get("coordinates", {})
        try:
            lat = float(coords.get("latitude", 0))
            lng = float(coords.get("longitude", 0))
        except (TypeError, ValueError):
            continue
        if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
            continue
        if boundary and not point_in_boundary(lng, lat, boundary):
            continue

        stats["locations"] += 1
        country_code = str(loc.get("country_code") or "")
        party_id = str(loc.get("party_id") or "")
        for evse in loc.get("evses", []):
            for conn in evse.get("connectors", []):
                stats["connectors"] += 1
                tariff_ids = conn.get("tariff_ids") or []
                if tariff_ids:
                    stats["with_tariff_ids"] += 1
                if any(get_cpo_rate(tid, tariff_map, country_code, party_id) is not None for tid in tariff_ids):
                    stats["resolved_energy_tariff"] += 1
    return stats


def main() -> None:
    print("=== NDW Huizen preprocessor ===")

    print("\n[1/5] Downloading NDW data files...")
    try:
        locations_raw = fetch_gz(LOCATIONS_URL)
        tariffs_raw = fetch_gz(TARIFFS_URL)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"\nERROR: Could not download NDW data: {exc}")
        sys.exit(1)

    print("\n[2/5] Parsing OCPI data...")
    locations_data = json.loads(locations_raw)
    tariffs_data = json.loads(tariffs_raw)
    locations = unwrap_ocpi_list(locations_data, "locations")
    tariffs = unwrap_ocpi_list(tariffs_data, "tariffs")

    print(f"  Total NL locations: {len(locations):,}")
    print(f"  Total NL tariffs:   {len(tariffs):,}")

    tariff_map = build_tariff_index(tariffs)
    print(f"  Scoped tariffs indexed: {len(tariff_map['scoped']):,}")
    print(f"  Globally unique IDs:    {len(tariff_map['unique']):,}")
    print(f"  Reused tariff IDs:      {tariff_map['id_collisions']:,}")

    print("\n[3/5] Building operator medians...")
    operator_median, operator_rates = build_operator_medians(locations, tariff_map)
    print(f"  Operators with median ({MIN_OPERATOR_MEDIAN_SAMPLES}+ samples): {len(operator_median)}")

    boundary = None
    try:
        boundary = load_boundary()
        vertices = sum(len(polygon[0]) for polygon in boundary)
        print(f"  Municipality boundary loaded ({len(boundary)} polygons, {vertices} outer vertices)")
    except FileNotFoundError:
        print("  WARNING: huizen-boundary.geojson not found, using bbox only")

    te_diag = totalenergies_diagnostics(locations, tariff_map, boundary)
    print("  TotalEnergies Huizen diagnostics:")
    print(f"    locations:              {te_diag['locations']}")
    print(f"    connectors:             {te_diag['connectors']}")
    print(f"    connectors tariff_ids:  {te_diag['with_tariff_ids']}")
    print(f"    resolved ENERGY tariff: {te_diag['resolved_energy_tariff']}")

    print("\n[4/5] Harvesting official CPO pricing sources...")
    official_harvest = harvest_official_pricing()
    for source in official_harvest["sources"]:
        if source["status"] == "ok":
            detail = f" rate={source['rate']:.4f}" if source.get("rate") is not None else ""
            msp_detail = ""
            if source.get("msp_table_status") == "ok":
                msp_detail = f" msp_rates={len(source.get('msp_rates', {}))}"
            elif source.get("msp_table_status") == "unavailable":
                msp_detail = " msp_rates=unavailable"
            print(f"  OK {source['id']} ({source['party_id']}){detail}{msp_detail}")
        else:
            print(f"  WARNING {source['id']}: {source.get('error', 'unavailable')}")

    print("\n[5/5] Filtering to gemeente Huizen...")
    results = []
    for loc in locations:
        processed = process_location(
            loc,
            tariff_map,
            operator_median,
            boundary,
            official_direct=official_harvest["direct_by_party"],
            official_msp=official_harvest["msp_by_party"],
        )
        if processed:
            results.append(processed)

    direct = sum(1 for r in results if r["pricing_source"] == "ndw")
    median = sum(1 for r in results if r["pricing_source"] == "operator_median")
    regional = sum(1 for r in results if r["pricing_source"] in {"totalenergies_mrae", "totalenergies_mrae_dc"})
    unknown = sum(1 for r in results if r["pricing_source"] == "unknown")
    comparison_ready = sum(1 for r in results if len(r["pricing"]) >= 2)
    adhoc_priced = sum(1 for r in results if r.get("direct_payment", {}).get("priced"))
    adhoc_priced_ndw = sum(
        1 for r in results
        if r.get("pricing", {}).get("direct_pay", {}).get("basis") == "ndw_ad_hoc"
    )
    adhoc_priced_official = sum(
        1 for r in results
        if r.get("pricing", {}).get("direct_pay", {}).get("basis") in {
            "official_cpo_adhoc", "official_cpo_direct_rule"
        }
    )
    direct_payment_known = sum(1 for r in results if r.get("direct_payment", {}).get("supported"))
    msp_quotes_official = sum(
        1
        for r in results
        for pass_id, quote in r.get("pricing", {}).items()
        if pass_id != "direct_pay" and quote.get("basis") == "official_cpo_msp_rate"
    )

    print(f"  Locations in area:       {len(results)}")
    print(f"  Direct NDW CPO tariff:   {direct}")
    print(f"  Operator-median tariff:  {median}")
    print(f"  Official MRA-E fallback: {regional}")
    print(f"  Unknown CPO base tariff: {unknown}")
    print(f"  2+ price estimates:      {comparison_ready}")
    print(f"  Priced ad-hoc routes:    {adhoc_priced}")
    print(f"    from NDW OCPI:         {adhoc_priced_ndw}")
    print(f"    from official CPO:     {adhoc_priced_official}")
    print(f"  Direct payment known:    {direct_payment_known}")
    print(f"  Official CPO MSP quotes: {msp_quotes_official}")

    operators = {}
    for result in results:
        operators[result["operator"]] = operators.get(result["operator"], 0) + 1
    print("\n  Operators found:")
    for operator, count in sorted(operators.items(), key=lambda item: -item[1]):
        print(f"    {operator}: {count}")

    output = {
        "schema_version": 4,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "NDW open data plus verified public CPO pricing sources",
        "official_pricing_harvest": {
            "checked_at": official_harvest["checked_at"],
            "sources": official_harvest["sources"],
        },
        "regional_pricing": {
            "totalenergies_mrae": {
                "ac_range": list(TOTALENERGIES_MRAE_AC_RANGE),
                "dc_rate": TOTALENERGIES_MRAE_DC_RATE,
                "verified_at": TOTALENERGIES_MRAE_VERIFIED_AT,
                "source_url": TOTALENERGIES_MRAE_SOURCE_URL,
                "municipality_source_url": HUIZEN_CHARGING_SOURCE_URL,
                "laadwerk_source_url": LAADWERK_SOURCE_URL,
            }
        },
        "bbox": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lng_min": LNG_MIN,
            "lng_max": LNG_MAX,
        },
        "passes": PASSES,
        "stats": {
            "total": len(results),
            "available_snapshot": sum(1 for r in results if r["available"]),
            "ndw_priced": direct,
            "median_priced": median,
            "regional_priced": regional,
            "unknown_base_rate": unknown,
            "comparison_ready": comparison_ready,
            "adhoc_priced": adhoc_priced,
            "adhoc_priced_ndw": adhoc_priced_ndw,
            "adhoc_priced_official": adhoc_priced_official,
            "direct_payment_known": direct_payment_known,
            "msp_quotes_official": msp_quotes_official,
        },
        "locations": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"\nWritten {OUTPUT_FILE} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
