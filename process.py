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
- If a regular connector tariff is missing, an operator median may be exposed as
  a diagnostic estimate when enough nationwide samples exist, but it is never
  treated as a complete session price or ranking input.
- TotalEnergies and Vattenfall locations in Huizen can use an official MRA-E
  regional price range when NDW does not expose a usable connector tariff. These
  are targeted fallbacks based on public concession tariffs, not generic
  operator averages.
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
TOTALENERGIES_MRAE_VERIFIED_AT = "2026-08-28"
TOTALENERGIES_MRAE_SOURCE_URL = "https://totalenergies.nl/elektrisch-rijden/vind-laadpunt"
TOTALENERGIES_MRAE_HISTORY_SOURCE_URL = "https://totalenergies.nl/historische-laadtarieven-concessie"
TOTALENERGIES_MRAE_DYNAMIC_SOURCE_URL = "https://totalenergies.nl/elektrisch-rijden/dynamische-tarieven"
LAADWERK_TARIFF_SOURCE_URL = "https://www.laadwerk.nl/veelgestelde-vragen"
TOTALENERGIES_MRAE_RESOLUTION_NOTE = (
    "Geen officiële machineleesbare koppeling gevonden tussen TNLP/PP-/EVSE-ID, vermogen of "
    "last_updated en MRA-E 2-5, MRA-E 6 of MRA-E 6 Dynamic. Laadwerk publiceert wel een "
    "plaatsingsgrens voor oude versus nieuwe concessietarieven, maar NDW last_updated is geen "
    "plaatsingsdatum en nieuwe palen kunnen dynamische prijzen hebben. Deze kenmerken worden "
    "daarom niet als concessieheuristiek gebruikt; dynamische tarieven zijn tijdsblokafhankelijk."
)
HUIZEN_CHARGING_SOURCE_URL = "https://www.huizen.nl/elektrisch-laden"
LAADWERK_SOURCE_URL = "https://www.laadwerk.nl/diensten/laadinfra"

# Vattenfall publishes the exact current MRA 2021 and MRA 2024 peak/off-peak
# tariffs. Laadwerk independently documents the old/new concession split and
# explicitly warns that physical replacement of an older pole does not change
# its tariff group. The pipeline therefore never infers a concession from
# hardware age, last_updated, address or proximity. When no connector tariff is
# available and no verified concession mapping exists, the complete official
# MRA price envelope is exposed as a regional range.
VATTENFALL_PUBLIC_TARIFF_SOURCE_URL = "https://incharge.vattenfall.nl/onze-tarieven?to-id=publiektarief"
VATTENFALL_CHARGE_CARD_SOURCE_URL = "https://incharge.vattenfall.nl/laadpas"
VATTENFALL_ROAMING_FEE_SOURCE_URL = "https://incharge.vattenfall.nl/en/our-network/our-rates"
VATTENFALL_ROAMING_SESSION_FEE = 0.35
VATTENFALL_MRAE_VERIFIED_AT = "2026-08-28"
VATTENFALL_MRAE_RESOLUTION_NOTE = (
    "Een publiek vindbare gemeentelijke ArcGIS-mirror van Laadwerk charge stations bevat onder meer "
    "id, location_code en concession_id, maar geen EVSE-ID of tarief en is geen aangetoonde regionale "
    "Huizen-feed. Daardoor is geen duurzame stationkoppeling naar NDW plus tariefgroep bewezen. Adres, "
    "coördinaat, hardwareouderdom en NDW last_updated worden niet gebruikt om MRA 2021 versus MRA 2024 te gokken."
)

# Supplemental official CPO sources are harvested on every data run. They are
# deliberately limited to rules that can be verified from a public operator
# page without logging in or reverse engineering an app. NDW remains the first
# source for connector-specific OCPI tariffs.
UBITRICITY_MRAE_DIRECT_SOURCE_URL = "https://ubitricity.com/nl/bestuurder/mrae-laadprijzen/"
TOTALENERGIES_DIRECT_RULE_SOURCE_URL = (
    "https://totalenergies.nl/nieuwsoverzicht/blogs-klantverhalen/"
    "totalenergies-betreurt-onverwachte-toeslag-van-laaddienstverlener-voor-e"
)
LIDL_DIRECT_SOURCE_URL = "https://www.lidl.nl/c/laadpalen/s10015078"

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

# Public charge-pass conditions are verified by the pricing-source monitor.
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
        "verified_at": "2026-08-28",
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
        "verified_at": "2026-08-28",
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
        "summary": "Eigen netwerk zonder extra starttarief; bij roaming €0,35 starttarief bovenop het laadpunttarief",
        "verified_at": "2026-08-28",
        "source_url": VATTENFALL_CHARGE_CARD_SOURCE_URL,
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
        "verified_at": "2026-08-28",
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
        "verified_at": "2026-08-28",
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
        "verified_at": "2026-08-28",
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


def parse_vattenfall_mrae_rates(page_text: str) -> Optional[dict]:
    """Extract exact Vattenfall MRA 2021 and MRA 2024 public tariffs.

    The official page currently publishes one MRA 2021 rate and separate peak
    and off-peak rates for MRA 2024. All three values are required. A partial
    parse fails closed so a redesigned page cannot silently shrink the range.
    """
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    old_match = re.search(
        r"metropoolregio\s+amsterdam\s*\(mra\s*2021\).{0,120}?€?\s*([0-9]+[,.][0-9]{4})",
        text,
        flags=re.IGNORECASE,
    )
    new_match = re.search(
        r"metropoolregio\s+amsterdam\s*\(mra\s*2024\).{0,160}?€?\s*([0-9]+[,.][0-9]{4}).{0,100}?€?\s*([0-9]+[,.][0-9]{4})",
        text,
        flags=re.IGNORECASE,
    )
    if not old_match or not new_match:
        return None
    mra_2021 = round(float(old_match.group(1).replace(",", ".")), 4)
    mra_2024_peak = round(float(new_match.group(1).replace(",", ".")), 4)
    mra_2024_off_peak = round(float(new_match.group(2).replace(",", ".")), 4)
    if min(mra_2021, mra_2024_peak, mra_2024_off_peak) <= 0:
        return None
    return {
        "mra_2021": mra_2021,
        "mra_2024_peak": mra_2024_peak,
        "mra_2024_off_peak": mra_2024_off_peak,
    }


def parse_laadwerk_vattenfall_context(page_text: str) -> Optional[dict]:
    """Verify Laadwerk's Vattenfall old/new concession context.

    Rounded Laadwerk prices are used as a cross-check, not as a replacement for
    Vattenfall's more precise published values. The physical-replacement caveat
    is mandatory because it is the reason hardware age cannot be a safe mapping
    heuristic.
    """
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    new_section = re.search(
        r"nieuwe\s+laadpalen.{0,120}vanaf\s+1\s+juli\s+2024.{0,500}?vattenfall\s+incharge\s*:\s*€?\s*([0-9]+[,.][0-9]{2})",
        text,
        flags=re.IGNORECASE,
    )
    old_section = re.search(
        r"laadpalen\s+geplaatst\s+v[oó][oó]?r\s+1\s+juli\s+2024.{0,500}?vattenfall\s+incharge\s*:\s*€?\s*([0-9]+[,.][0-9]{2})",
        text,
        flags=re.IGNORECASE,
    )
    replacement_caveat = bool(re.search(
        r"oude\s+tarief.{0,180}(?:vervangen|nieuwe\s+laadpaal).{0,180}(?:digitaal\s+scherm|laadpaal)",
        text,
        flags=re.IGNORECASE,
    ))
    if not new_section or not old_section or not replacement_caveat:
        return None
    return {
        "new_from_2024_07_01": round(float(new_section.group(1).replace(",", ".")), 2),
        "old_before_2024_07_01": round(float(old_section.group(1).replace(",", ".")), 2),
        "replacement_keeps_old_tariff": True,
    }


def _parse_lidl_rates_in_section(page_text: str, heading: str, stop_heading: Optional[str] = None) -> dict[str, float]:
    """Extract AC/DC Lidl.nl rates only from the named visible page section."""
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    start = text.find(heading.lower())
    if start < 0:
        return {}
    end = len(text)
    if stop_heading:
        candidate = text.find(stop_heading.lower(), start + len(heading))
        if candidate >= 0:
            end = candidate
    section = text[start:end]
    patterns = {
        "AC": r"lidl\.nl[- ]tarief\s+regulier[- ]laadstation.{0,100}?€?\s*([0-9]+[,.][0-9]{2,4}).{0,30}?/\s*kwh.{0,30}?\(ac\)",
        "DC": r"lidl\.nl[- ]tarief\s+snel[- ]laadstation.{0,100}?€?\s*([0-9]+[,.][0-9]{2,4}).{0,30}?/\s*kwh.{0,30}?\(dc\)",
    }
    result: dict[str, float] = {}
    for current_type, pattern in patterns.items():
        match = re.search(pattern, section, flags=re.IGNORECASE)
        if not match:
            return {}
        result[current_type] = round(float(match.group(1).replace(",", ".")), 4)
    return result


def parse_lidl_direct_rates(page_text: str) -> dict[str, float]:
    """Extract Lidl.nl Direct/QR AC and DC rates from the Direct section only."""
    return _parse_lidl_rates_in_section(page_text, "opladen via lidl.nl", "opladen met eigen laadpas")


def parse_lidl_cpo_rates(page_text: str) -> dict[str, float]:
    """Extract Lidl.nl AC/DC rates explicitly published for use with a charge card.

    The own-charge-card section must also state that the charge-card provider's
    own costs can apply. Without that qualifier the numerical CPO base would be
    easy to misread as a complete MSP session price, so the fallback fails closed.
    """
    text = re.sub(r"\s+", " ", (page_text or "").lower())
    start = text.find("opladen met eigen laadpas")
    if start < 0:
        return {}
    section = text[start:]
    if not re.search(r"abonnementskosten.{0,120}laadpas\s+aanbieder", section, flags=re.IGNORECASE):
        return {}
    return _parse_lidl_rates_in_section(page_text, "opladen met eigen laadpas")


def harvest_official_pricing(fetcher=fetch_public_page) -> dict:
    """Harvest public operator rules that can safely supplement NDW pricing.

    Failures are intentionally non-fatal. A transient CPO website problem must
    not block the daily NDW snapshot. A source is only applied when the expected
    wording or numeric value can be verified in the current run.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    direct_by_party: dict[str, dict] = {}
    cpo_by_party: dict[str, dict] = {}
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
            "note": "Officieel Ubitricity MRA-E Direct/QR-tarief. De laadtransactie wordt per kWh afgerekend; eventuele lokale parkeerkosten vallen buiten deze laadtransactie.",
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
                    "note": "Ubitricity publiceert voor MRA-E een netwerk-specifiek kWh-tarief voor deze laadpas.",
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

    # Vattenfall MRA-E regional fallback. Both sources must agree in the same
    # run: Vattenfall supplies the exact concession rates, Laadwerk confirms
    # the old/new regional split and the non-obvious replacement caveat.
    try:
        vattenfall_page = fetcher(VATTENFALL_PUBLIC_TARIFF_SOURCE_URL)
        laadwerk_page = fetcher(LAADWERK_TARIFF_SOURCE_URL)
        exact_rates = parse_vattenfall_mrae_rates(vattenfall_page)
        laadwerk_context = parse_laadwerk_vattenfall_context(laadwerk_page)
        if not exact_rates:
            raise ValueError("Vattenfall MRA 2021/2024 tariffs not found")
        if not laadwerk_context:
            raise ValueError("Laadwerk Vattenfall concession context not found")
        if round(exact_rates["mra_2021"], 2) != laadwerk_context["old_before_2024_07_01"]:
            raise ValueError("Vattenfall MRA 2021 rate conflicts with Laadwerk old-concession rate")
        if round(exact_rates["mra_2024_peak"], 2) != laadwerk_context["new_from_2024_07_01"]:
            raise ValueError("Vattenfall MRA 2024 peak rate conflicts with Laadwerk new-concession rate")

        low = min(exact_rates["mra_2024_off_peak"], exact_rates["mra_2024_peak"], exact_rates["mra_2021"])
        high = max(exact_rates["mra_2024_off_peak"], exact_rates["mra_2024_peak"], exact_rates["mra_2021"])
        cpo_by_party["NUO"] = {
            "mode": "regional_range",
            "current_type": "AC",
            "rate": round((low + high) / 2, 4),
            "range": [low, high],
            "session": 0.0,
            "basis": "vattenfall_mrae",
            "source_id": "vattenfall_mrae",
            "source_url": VATTENFALL_PUBLIC_TARIFF_SOURCE_URL,
            "context_source_url": LAADWERK_TARIFF_SOURCE_URL,
            "source_checked_at": checked_at,
            "verification_rule_ids": ["vattenfall_mrae", "laadwerk_vattenfall_context"],
            "confidence": "medium",
            "concession_rates": exact_rates,
            "note": (
                "Officiële Vattenfall MRA-E prijsband. De exacte concessie voor dit laadpunt is niet "
                "vastgesteld; daarom wordt MRA 2024 dal/piek tot en met MRA 2021 als band getoond."
            ),
        }
        results.append({
            "id": "vattenfall_mrae",
            "party_id": "NUO",
            "status": "ok",
            "range": [low, high],
            "concession_rates": exact_rates,
            "laadwerk_context": laadwerk_context,
            "source_url": VATTENFALL_PUBLIC_TARIFF_SOURCE_URL,
            "context_source_url": LAADWERK_TARIFF_SOURCE_URL,
        })
    except Exception as exc:
        results.append({
            "id": "vattenfall_mrae",
            "party_id": "NUO",
            "status": "unavailable",
            "error": str(exc),
            "source_url": VATTENFALL_PUBLIC_TARIFF_SOURCE_URL,
            "context_source_url": LAADWERK_TARIFF_SOURCE_URL,
        })

    try:
        page = fetcher(LIDL_DIRECT_SOURCE_URL)
    except Exception as exc:
        for source_id in ("lidl_direct_payment", "lidl_cpo_tariff"):
            results.append({
                "id": source_id,
                "party_id": "LDL",
                "status": "unavailable",
                "error": str(exc),
                "source_url": LIDL_DIRECT_SOURCE_URL,
            })
    else:
        direct_rates = parse_lidl_direct_rates(page)
        if set(direct_rates) == {"AC", "DC"}:
            direct_by_party["LDL"] = {
                "mode": "by_current_type",
                "rates": direct_rates,
                "session": 0.0,
                "basis": "official_cpo_adhoc",
                "source_id": "lidl_direct_payment",
                "source_url": LIDL_DIRECT_SOURCE_URL,
                "source_checked_at": checked_at,
                "confidence": "high",
                "note": "Officieel Lidl.nl Direct/QR-tarief, met een afzonderlijk kWh-tarief voor AC en DC.",
            }
            results.append({
                "id": "lidl_direct_payment",
                "party_id": "LDL",
                "status": "ok",
                "rates": direct_rates,
                "source_url": LIDL_DIRECT_SOURCE_URL,
            })
        else:
            results.append({
                "id": "lidl_direct_payment",
                "party_id": "LDL",
                "status": "unavailable",
                "error": "Lidl.nl AC/DC Direct/QR-tarieven niet gevonden in de Direct-sectie",
                "source_url": LIDL_DIRECT_SOURCE_URL,
            })

        cpo_rates = parse_lidl_cpo_rates(page)
        if set(cpo_rates) == {"AC", "DC"}:
            cpo_by_party["LDL"] = {
                "mode": "by_current_type",
                "rates": cpo_rates,
                "session": 0.0,
                "basis": "official_cpo_tariff",
                "source_id": "lidl_cpo_tariff",
                "source_url": LIDL_DIRECT_SOURCE_URL,
                "source_checked_at": checked_at,
                "confidence": "high",
                "note": "Officieel Lidl.nl CPO-tarief bij gebruik van een eigen laadpas, afzonderlijk voor AC en DC; laadpasaanbieders kunnen eigen kosten toevoegen.",
            }
            results.append({
                "id": "lidl_cpo_tariff",
                "party_id": "LDL",
                "status": "ok",
                "rates": cpo_rates,
                "source_url": LIDL_DIRECT_SOURCE_URL,
            })
        else:
            results.append({
                "id": "lidl_cpo_tariff",
                "party_id": "LDL",
                "status": "unavailable",
                "error": "Lidl.nl AC/DC CPO-tarieven niet gevonden in de eigen-laadpas-sectie",
                "source_url": LIDL_DIRECT_SOURCE_URL,
            })

    return {
        "checked_at": checked_at,
        "direct_by_party": direct_by_party,
        "cpo_by_party": cpo_by_party,
        "msp_by_party": msp_by_party,
        "sources": results,
    }


# Backwards-compatible name for tests or scripts created before MSP supplements
# were added. New code should use harvest_official_pricing().
def harvest_official_direct_pricing(fetcher=fetch_public_page) -> dict:
    return harvest_official_pricing(fetcher=fetcher)


def supplemental_cpo_price_info(
    party_id: str,
    current_type: Optional[str],
    official_cpo: Optional[dict],
) -> Optional[dict]:
    """Return a verified network CPO base tariff when NDW has none."""
    source = (official_cpo or {}).get((party_id or "").upper())
    if not source:
        return None
    if source.get("mode") == "regional_range":
        expected_type = str(source.get("current_type") or "").upper()
        actual_type = str(current_type or "").upper()
        if expected_type and actual_type != expected_type:
            return None
        return {
            "rate": float(source["rate"]),
            "range": list(source.get("range") or []) or None,
            "session": float(source.get("session", 0.0)),
            "session_range": None,
            "unmodelled_types": [],
            "quality_reasons": [],
            "energy_step_size_wh": None,
            "tariff_id": None,
            "restricted": False,
            "basis": source.get("basis", "regional_official"),
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "context_source_url": source.get("context_source_url"),
            "source_checked_at": source.get("source_checked_at"),
            "verification_rule_ids": list(source.get("verification_rule_ids") or []),
            "note": source.get("note"),
        }
    if source.get("mode") != "by_current_type":
        return None
    rate = (source.get("rates") or {}).get(str(current_type or "").upper())
    if rate is None:
        return None
    return {
        "rate": float(rate),
        "range": None,
        "session": float(source.get("session", 0.0)),
        "session_range": None,
        "unmodelled_types": [],
        "quality_reasons": [],
        "energy_step_size_wh": None,
        "tariff_id": None,
        "restricted": False,
        "basis": source.get("basis", "official_cpo_tariff"),
        "source_id": source.get("source_id"),
        "source_url": source.get("source_url"),
        "source_checked_at": source.get("source_checked_at"),
        "confidence": source.get("confidence", "high"),
        "note": source.get("note"),
    }


def supplemental_direct_price_info(
    party_id: str,
    cpo_rate: Optional[float],
    cpo_rate_range: Optional[list[float]],
    cpo_source: str,
    official_direct: Optional[dict],
    cpo_session: float = 0.0,
    cpo_session_range: Optional[list[float]] = None,
    cpo_unmodelled_types: Optional[list[str]] = None,
    cpo_restricted: bool = False,
    cpo_energy_step_size_wh: Optional[int] = None,
    cpo_quality_reasons: Optional[list[str]] = None,
    current_type: Optional[str] = None,
) -> Optional[dict]:
    """Build an ad-hoc price from a verified public CPO source.

    A mirror rule inherits all modelled CPO components, including a FLAT fee.
    Time- and parking-based components remain explicit as unmodelled costs so
    the frontend can fail closed instead of presenting an incomplete total.
    """
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
            "unmodelled_types": list(source.get("unmodelled_types") or []),
            "restricted": bool(source.get("restricted")),
            "energy_step_size_wh": source.get("energy_step_size_wh"),
            "quality_reasons": list(source.get("quality_reasons") or []),
            "basis": source.get("basis", "official_cpo_adhoc"),
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "source_checked_at": source.get("source_checked_at"),
            "confidence": source.get("confidence", "high"),
            "note": source.get("note"),
        }

    if source.get("mode") == "by_current_type":
        rate = (source.get("rates") or {}).get(str(current_type or "").upper())
        if rate is None:
            return None
        return {
            "rate": float(rate),
            "range": None,
            "session": float(source.get("session", 0.0)),
            "session_range": None,
            "unmodelled_types": [],
            "restricted": False,
            "energy_step_size_wh": None,
            "quality_reasons": [],
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
            "session": float(cpo_session or 0.0),
            "session_range": cpo_session_range,
            "unmodelled_types": list(cpo_unmodelled_types or []),
            "restricted": bool(cpo_restricted),
            "energy_step_size_wh": cpo_energy_step_size_wh,
            "quality_reasons": list(cpo_quality_reasons or []),
            "basis": source.get("basis", "official_cpo_direct_rule"),
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "source_checked_at": source.get("source_checked_at"),
            "confidence": confidence,
            "note": source.get("note"),
            "inherited_base_source": cpo_source,
        }

    return None

def price_component_including_vat(component: dict, expected_type: Optional[str] = None) -> Optional[float]:
    """Return one OCPI 2.2.1 price component including explicitly supplied VAT.

    ``PriceComponent.price`` is exclusive of VAT. OCPI explicitly states that
    when ``vat`` is omitted no VAT is applicable, so we must *not* invent a
    Dutch VAT percentage. If a VAT field is present but malformed, the
    component fails closed instead of silently returning a net price.
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
            return None

    return round(price, 4)


def energy_step_size_wh(component: dict) -> Optional[int]:
    """Return a valid OCPI ENERGY billing step in Wh, if explicitly supplied."""
    if str(component.get("type") or "").upper() != "ENERGY":
        return None
    try:
        step = int(component.get("step_size"))
    except (TypeError, ValueError):
        return None
    return step if step > 0 else None


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


def parse_ocpi_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def tariff_is_active(tariff: dict, at_time: Optional[datetime] = None) -> bool:
    """Return whether a tariff is active at the requested instant.

    OCPI allows multiple tariffs of the same type on a connector only when
    their tariff-level validity periods do not overlap. Ignoring these fields
    can therefore select a future or expired price. Invalid validity timestamps
    fail closed rather than being treated as active.
    """
    now = at_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    raw_start = tariff.get("start_date_time")
    raw_end = tariff.get("end_date_time")
    start = parse_ocpi_datetime(raw_start)
    end = parse_ocpi_datetime(raw_end)
    if raw_start and start is None:
        return False
    if raw_end and end is None:
        return False
    if start and now < start:
        return False
    if end and now >= end:
        return False
    return True


def get_tariff_price_info(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
    mode: str = "regular",
) -> Optional[dict]:
    """Return usable ENERGY/FLAT information for a regular or ad-hoc tariff.

    OCPI tariff restrictions determine which element is active. We currently
    do not evaluate those restrictions against a planned session, so any
    restricted tariff is retained for transparency but blocked from ranking.
    For an unrestricted tariff, OCPI says that only the first TariffElement
    containing a given dimension is active for that dimension. We follow that
    rule instead of turning later fallback elements into an artificial range.

    ENERGY billing ``step_size`` is preserved when unambiguous so the browser
    can round charged energy exactly as OCPI specifies. Unsupported, malformed
    or non-EUR monetary components fail closed rather than silently lowering a
    consumer total.
    """
    tariff = get_tariff(tariff_id, tariff_index, country_code, party_id)
    if not tariff:
        return None

    raw_tariff_type = str(tariff.get("type") or "").upper()
    tariff_type = raw_tariff_type or "UNSPECIFIED"
    if not tariff_is_active(tariff):
        return None
    if mode == "ad_hoc" and raw_tariff_type != "AD_HOC_PAYMENT":
        return None
    if mode == "ad_hoc_unspecified" and raw_tariff_type:
        return None
    if mode == "regular" and raw_tariff_type not in {"", "REGULAR"}:
        return None

    currency = str(tariff.get("currency") or "").upper()
    if currency and currency != "EUR":
        # The public site presents euro amounts and deliberately has no FX
        # conversion layer. Never relabel a foreign-currency tariff as EUR.
        return None

    elements = [element for element in (tariff.get("elements") or []) if isinstance(element, dict)]
    restricted = any(bool(element.get("restrictions")) for element in elements)

    # With no restrictions, OCPI activates the first TariffElement containing
    # a PriceComponent for each dimension. Later occurrences are fallbacks and
    # must not be interpreted as simultaneous alternatives. When restrictions
    # exist we cannot know the active element without session context, so retain
    # all candidate components as a bounded indication and block ranking below.
    components: list[dict] = []
    structural_blockers: set[str] = set()
    if restricted:
        for element in elements:
            components.extend(c for c in (element.get("price_components") or []) if isinstance(c, dict))
    else:
        chosen_dimensions: set[str] = set()
        for element in elements:
            by_dimension: dict[str, list[dict]] = {}
            for component in element.get("price_components") or []:
                if not isinstance(component, dict):
                    continue
                component_type = str(component.get("type") or "").upper()
                by_dimension.setdefault(component_type, []).append(component)
            for component_type, rows in by_dimension.items():
                if component_type in chosen_dimensions:
                    continue
                if len(rows) > 1:
                    structural_blockers.add("DUPLICATE_PRICE_DIMENSION")
                components.extend(rows)
                chosen_dimensions.add(component_type)

    energy_rates: list[float] = []
    energy_steps: list[int] = []
    flat_fees: list[float] = []
    unmodelled_types: set[str] = set(structural_blockers)
    quality_reasons: set[str] = set()

    if not currency:
        # Currency is mandatory in OCPI. In a Dutch feed EUR is a reasonable
        # diagnostic assumption, but it is not strong enough for a hard winner.
        quality_reasons.add("currency_not_explicit")

    if tariff.get("min_price"):
        unmodelled_types.add("MIN_PRICE")
    if tariff.get("max_price"):
        unmodelled_types.add("MAX_PRICE")
    if restricted:
        unmodelled_types.add("TARIFF_RESTRICTIONS")

    known_dimensions = {"ENERGY", "FLAT", "TIME", "PARKING_TIME"}
    for component in components:
        component_type = str(component.get("type") or "").upper()

        if component_type not in known_dimensions:
            unmodelled_types.add("UNSUPPORTED_PRICE_COMPONENT")
            continue

        # TIME/PARKING are known monetary dimensions even when their numeric
        # payload is malformed. Mark them before parsing so they can never be
        # silently dropped from a session total.
        if component_type in {"TIME", "PARKING_TIME"}:
            unmodelled_types.add(component_type)

        if component.get("vat") is not None:
            try:
                float(component.get("vat"))
            except (TypeError, ValueError):
                unmodelled_types.add("INVALID_VAT")
                continue

        value = price_component_including_vat(component)
        if value is None:
            unmodelled_types.add("INVALID_PRICE_COMPONENT")
            continue

        if component_type == "ENERGY":
            energy_rates.append(value)
            step = energy_step_size_wh(component)
            if step is None:
                quality_reasons.add("energy_step_size_not_explicit")
            else:
                energy_steps.append(step)
        elif component_type == "FLAT":
            flat_fees.append(value)

    energy_rates = sorted(set(energy_rates))
    energy_steps = sorted(set(energy_steps))
    flat_fees = sorted(set(flat_fees))
    if not energy_rates:
        if flat_fees:
            # OCPI explicitly models a free-of-charge tariff as FLAT = 0. A
            # flat-only tariff can therefore be complete without an ENERGY
            # dimension; represent its energy component as EUR 0/kWh.
            energy_rates = [0.0]
        else:
            return None

    if len(energy_steps) > 1:
        # Multiple ENERGY components in the selected element with different
        # billing blocks cannot be represented by one quote safely.
        unmodelled_types.add("ENERGY_STEP_SIZE_VARIANTS")
        energy_step = None
    else:
        energy_step = energy_steps[0] if energy_steps else None

    low_rate, high_rate = energy_rates[0], energy_rates[-1]
    low_flat = flat_fees[0] if flat_fees else 0.0
    high_flat = flat_fees[-1] if flat_fees else 0.0
    return {
        "rate": round((low_rate + high_rate) / 2, 4),
        "range": [low_rate, high_rate] if len(energy_rates) > 1 else None,
        "session": round((low_flat + high_flat) / 2, 4),
        "session_range": [low_flat, high_flat] if len(flat_fees) > 1 else None,
        "energy_step_size_wh": energy_step,
        "currency": currency or None,
        "tariff_type": tariff_type,
        "tariff_id": str(tariff_id),
        "unmodelled_types": sorted(unmodelled_types),
        "quality_reasons": sorted(quality_reasons),
        "restricted": restricted,
    }


def get_cpo_rates(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
) -> list[float]:
    """Return all distinct regular OCPI ENERGY prices for a resolved tariff."""
    tariff = get_tariff(tariff_id, tariff_index, country_code, party_id)
    if not tariff or normalized_tariff_type(tariff) != "REGULAR" or not tariff_is_active(tariff):
        return []
    # Operator medians are displayed as euro diagnostics. Do not let a
    # foreign or unspecified currency pollute that estimate.
    if str(tariff.get("currency") or "").upper() != "EUR":
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
    """Return only the currently active REGULAR CPO tariff information."""
    return get_tariff_price_info(tariff_id, tariff_index, country_code, party_id, mode="regular")


def get_ad_hoc_price_info(
    tariff_id: str,
    tariff_index: dict,
    country_code: str = "",
    party_id: str = "",
    allow_unspecified: bool = False,
) -> Optional[dict]:
    """Return an OCPI tariff applicable to ad-hoc payment.

    An explicit ``AD_HOC_PAYMENT`` tariff proves the tariff/payment relation.
    OCPI also says an omitted Tariff.type is valid for all sessions; we only
    use such an unspecified tariff for ad-hoc pricing when direct-payment
    support has independently been established from capabilities or operator
    documentation.
    """
    mode = "ad_hoc_unspecified" if allow_unspecified else "ad_hoc"
    return get_tariff_price_info(tariff_id, tariff_index, country_code, party_id, mode=mode)


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
    """Legacy confidence field retained for backwards compatibility.

    Quality Model v2.1 no longer uses this as the primary quality KPI. A source
    can be authoritative while still being geographically non-specific.
    """
    if source in {"ndw", "official_cpo_tariff"}:
        return "high"
    if source in {
        "operator_median", "totalenergies_mrae", "totalenergies_mrae_dc",
        "vattenfall_mrae", "vattenfall_mrae_2021", "vattenfall_mrae_2024",
    }:
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


def combined_session_range(
    base_session: float = 0.0,
    base_range: Optional[list[float] | tuple[float, float]] = None,
    msp_fee: float = 0.0,
    msp_fee_range: Optional[list[float] | tuple[float, float]] = None,
) -> Optional[list[float]]:
    """Combine CPO and MSP session-fee uncertainty into one bounded range."""
    base_low, base_high = (float(base_session), float(base_session))
    if base_range and len(base_range) == 2:
        base_low, base_high = float(base_range[0]), float(base_range[1])
    msp_low, msp_high = (float(msp_fee), float(msp_fee))
    if msp_fee_range and len(msp_fee_range) == 2:
        msp_low, msp_high = float(msp_fee_range[0]), float(msp_fee_range[1])
    low, high = round(base_low + msp_low, 4), round(base_high + msp_high, 4)
    return [low, high] if abs(high - low) > 1e-9 else None


def source_quality_for_basis(basis: str, inherited_base_source: Optional[str] = None) -> str:
    source = inherited_base_source or basis
    if source in {
        "ndw", "ndw_ad_hoc", "ndw_ad_hoc_compatible", "official_cpo_adhoc", "official_cpo_msp_rate",
        "official_cpo_tariff",
        "official_cpo_direct_rule", "totalenergies_mrae", "totalenergies_mrae_dc",
        "vattenfall_mrae", "vattenfall_mrae_2021", "vattenfall_mrae_2024",
        "published_shell", "published_band",
    }:
        return "high"
    if source == "operator_median":
        return "low"
    return "medium"


def price_specificity_for_basis(basis: str, inherited_base_source: Optional[str] = None) -> str:
    source = inherited_base_source or basis
    if source in {"ndw", "ndw_ad_hoc", "ndw_ad_hoc_compatible"}:
        return "connector"
    if source in {"official_cpo_adhoc", "official_cpo_msp_rate", "official_cpo_tariff"}:
        return "network"
    if source in {
        "totalenergies_mrae", "totalenergies_mrae_dc",
        "vattenfall_mrae", "vattenfall_mrae_2021", "vattenfall_mrae_2024",
    }:
        return "regional"
    if source == "official_cpo_direct_rule":
        return "regional"
    if source in {"published_shell", "published_band"}:
        return "national"
    if source == "operator_median":
        return "operator_estimate"
    return "unknown"


def quote_quality(
    basis: str,
    price_range: Optional[list[float]] = None,
    session_range: Optional[list[float]] = None,
    unmodelled_costs: Optional[list[str]] = None,
    source_quality: Optional[str] = None,
    price_specificity: Optional[str] = None,
    inherited_base_source: Optional[str] = None,
    extra_reasons: Optional[list[str]] = None,
) -> dict:
    """Return Quality Model v2.1 dimensions for a quote.

    - source_quality: trustworthiness of the source itself;
    - price_specificity: how closely that source identifies this connector;
    - cost_completeness: whether every known monetary component is modelled;
    - decision_grade: whether the quote can support a hard recommendation.
    """
    source_quality = source_quality or source_quality_for_basis(basis, inherited_base_source)
    price_specificity = price_specificity or price_specificity_for_basis(basis, inherited_base_source)
    missing = sorted({str(value) for value in (unmodelled_costs or []) if value})
    completeness = "partial" if missing else "complete"
    reasons = set(extra_reasons or [])

    # These issues do not make the numeric indication useless, but they do
    # make it unsafe to call the resulting route a hard/reliable winner.
    forced_indicative = {
        "potential_network_discount",
        "potential_msp_blocking_fee",
        "energy_step_size_not_explicit",
        "currency_not_explicit",
        "tariff_restrictions_bounded",
    }

    if missing:
        reasons.add("known_cost_component_not_modelled")
        decision_grade = "exclude"
    elif reasons & forced_indicative:
        decision_grade = "indicative"
    elif (
        source_quality == "high"
        and price_specificity in {"connector", "network"}
        and not price_range
        and not session_range
    ):
        decision_grade = "reliable"
    else:
        decision_grade = "indicative"

    if decision_grade == "indicative":
        if price_range or session_range:
            reasons.add("bounded_price_uncertainty")
        if price_specificity not in {"connector", "network"}:
            reasons.add(f"specificity_{price_specificity}")
        if source_quality != "high":
            reasons.add(f"source_quality_{source_quality}")

    return {
        "source_quality": source_quality,
        "price_specificity": price_specificity,
        "cost_completeness": completeness,
        "decision_grade": decision_grade,
        "reasons": sorted(reasons),
        "unmodelled_costs": missing,
    }


def bounded_restriction_adjustment(
    unmodelled_costs: Optional[list[str]],
    price_range: Optional[list[float]] = None,
    session_range: Optional[list[float]] = None,
) -> tuple[list[str], list[str]]:
    """Downgrade OCPI restrictions from blocker to bounded uncertainty when safe.

    A restriction is not itself a monetary component. If the only blocker is
    ``TARIFF_RESTRICTIONS`` and the tariff already exposes a genuine numeric
    range, the unknown condition merely selects a value inside that known
    envelope. Quality Model v2.1 therefore keeps the quote complete but
    indicative. TIME, PARKING_TIME, minimum/maximum prices and malformed
    components remain hard blockers because their session impact is not
    bounded by the displayed range.
    """
    missing = sorted({str(value) for value in (unmodelled_costs or []) if value})
    if "TARIFF_RESTRICTIONS" not in missing:
        return missing, []

    other = [value for value in missing if value != "TARIFF_RESTRICTIONS"]
    has_price_range = bool(
        price_range and len(price_range) == 2
        and abs(float(price_range[1]) - float(price_range[0])) > 1e-9
    )
    has_session_range = bool(
        session_range and len(session_range) == 2
        and abs(float(session_range[1]) - float(session_range[0])) > 1e-9
    )
    if not other and (has_price_range or has_session_range):
        return [], ["tariff_restrictions_bounded"]
    return missing, []


def totalenergies_mrae_fallback(operator_name: str, current_type: str) -> Optional[dict]:
    """Return official MRA-E pricing for TotalEnergies when NDW is incomplete.

    AC/DC is determined from OCPI ``Connector.power_type`` (or, only when that
    field is absent, from the connector standard), never from a kW threshold.
    """
    op = operator_key(operator_name)
    if "totalenergies" not in op and "total energies" not in op:
        return None

    if str(current_type).upper() == "DC":
        return {
            "rate": TOTALENERGIES_MRAE_DC_RATE,
            "range": None,
            "session": 0.0,
            "session_range": None,
            "unmodelled_types": [],
            "source": "totalenergies_mrae_dc",
            "note": "Officieel TotalEnergies MRA-E DC-tarief voor Noord-Holland, Flevoland en Utrecht.",
        }

    if str(current_type).upper() != "AC":
        return None

    low, high = TOTALENERGIES_MRAE_AC_RANGE
    return {
        "rate": round((low + high) / 2, 4),
        "range": [low, high],
        "session": 0.0,
        "session_range": None,
        "unmodelled_types": [],
        "source": "totalenergies_mrae",
        "note": "Officiële MRA-E prijsband van TotalEnergies; de exacte concessie en eventuele dynamische prijs zijn niet uit NDW af te leiden.",
    }


def vattenfall_mrae_fallback(
    operator_name: str,
    current_type: str,
    rates: dict,
    concession: Optional[str] = None,
) -> Optional[dict]:
    """Return a Vattenfall MRA price only from verified official rate input.

    ``concession`` is accepted solely for an independently verified mapping.
    The normal Huizen pipeline passes no concession and therefore receives the
    full MRA 2024 off-peak/peak through MRA 2021 envelope. No location age,
    address or proximity heuristic is used here.
    """
    op = operator_key(operator_name)
    if not any(token in op for token in ("vattenfall", "incharge", "nuon")):
        return None
    if str(current_type).upper() != "AC":
        return None
    required = {"mra_2021", "mra_2024_peak", "mra_2024_off_peak"}
    if not rates or not required.issubset(rates):
        return None

    old = float(rates["mra_2021"])
    peak = float(rates["mra_2024_peak"])
    off_peak = float(rates["mra_2024_off_peak"])
    group = str(concession or "").strip().upper().replace("-", "_").replace(" ", "_")
    if group == "MRA_2021":
        return {
            "rate": old,
            "range": None,
            "session": 0.0,
            "session_range": None,
            "unmodelled_types": [],
            "source": "vattenfall_mrae_2021",
            "note": "Officieel Vattenfall MRA 2021-tarief; concessiegroep is onafhankelijk vastgesteld.",
        }
    if group == "MRA_2024":
        low, high = min(off_peak, peak), max(off_peak, peak)
        return {
            "rate": round((low + high) / 2, 4),
            "range": [low, high],
            "session": 0.0,
            "session_range": None,
            "unmodelled_types": [],
            "source": "vattenfall_mrae_2024",
            "note": "Officiële Vattenfall MRA 2024 dal-/piekprijsband; het tijdstip van de laadsessie bepaalt het kWh-tarief.",
        }
    if group:
        return None

    low, high = min(off_peak, peak, old), max(off_peak, peak, old)
    return {
        "rate": round((low + high) / 2, 4),
        "range": [low, high],
        "session": 0.0,
        "session_range": None,
        "unmodelled_types": [],
        "source": "vattenfall_mrae",
        "note": "Officiële Vattenfall MRA-E prijsband; MRA 2021 versus MRA 2024 is voor dit laadpunt niet vastgesteld.",
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
    cpo_session: float = 0.0,
    msp_session: float = 0.0,
    percentage_scope: Optional[str] = None,
    unmodelled_costs: Optional[list[str]] = None,
    source_quality: Optional[str] = None,
    price_specificity: Optional[str] = None,
    inherited_base_source: Optional[str] = None,
    quality_reasons: Optional[list[str]] = None,
    energy_step_size_wh: Optional[int] = None,
    cpo_kwh: Optional[float] = None,
    cpo_kwh_range: Optional[list[float]] = None,
    msp_kwh: float = 0.0,
) -> dict:
    quality = quote_quality(
        basis,
        price_range=price_range,
        session_range=session_range,
        unmodelled_costs=unmodelled_costs,
        source_quality=source_quality,
        price_specificity=price_specificity,
        inherited_base_source=inherited_base_source,
        extra_reasons=quality_reasons,
    )
    quote = {
        "kwh": round(float(kwh), 4),
        "session": round(float(session), 4),
        "confidence": confidence,
        "basis": basis,
        "quality": quality,
    }
    if note:
        quote["note"] = note
    if price_range:
        quote["range"] = [round(float(v), 4) for v in price_range]
    if transaction_percentage:
        quote["percentage"] = round(float(transaction_percentage), 4)
    if session_range:
        quote["session_range"] = [round(float(v), 4) for v in session_range]
    if cpo_session:
        quote["cpo_session"] = round(float(cpo_session), 4)
    if msp_session:
        quote["msp_session"] = round(float(msp_session), 4)
    if percentage_scope:
        quote["percentage_scope"] = percentage_scope
    if energy_step_size_wh:
        quote["energy_step_size_wh"] = int(energy_step_size_wh)
    if cpo_kwh is not None:
        quote["cpo_kwh"] = round(float(cpo_kwh), 4)
    if cpo_kwh_range:
        quote["cpo_kwh_range"] = [round(float(v), 4) for v in cpo_kwh_range]
    if msp_kwh:
        quote["msp_kwh"] = round(float(msp_kwh), 4)
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
    current_type: str = "UNKNOWN",
    cpo_rate_range: Optional[list[float]] = None,
    cpo_note: Optional[str] = None,
    party_id: str = "",
    direct_price_info: Optional[dict] = None,
    msp_price_overrides: Optional[dict] = None,
    cpo_session: float = 0.0,
    cpo_session_range: Optional[list[float]] = None,
    cpo_unmodelled_types: Optional[list[str]] = None,
    cpo_restricted: bool = False,
    cpo_energy_step_size_wh: Optional[int] = None,
    cpo_quality_reasons: Optional[list[str]] = None,
    verified_rules: Optional[set[str]] = None,
) -> dict:
    """Build direct-payment and MSP price components for one connector profile.

    Static MSP rules are fail-closed when ``verified_rules`` is supplied by the
    daily source check. Quality Model v2.1 distinguishes bounded uncertainty
    from an actually missing monetary component: bounded tariff alternatives
    remain rankable as ``indicative`` while unbounded missing costs stay visible
    as partial quotes and are never ranked.
    """
    pricing: dict[str, dict] = {}
    op = operator_key(operator_name)
    msp_overrides = msp_price_overrides or {}
    base_confidence = confidence_for_source(cpo_source)
    if cpo_rate_range and len(cpo_rate_range) == 2 and abs(cpo_rate_range[1] - cpo_rate_range[0]) > 1e-9:
        base_confidence = downgrade_confidence(base_confidence)
    base_unmodelled = list(cpo_unmodelled_types or [])
    base_quality_reasons = list(cpo_quality_reasons or [])
    if cpo_restricted and "TARIFF_RESTRICTIONS" not in base_unmodelled:
        base_unmodelled.append("TARIFF_RESTRICTIONS")
    base_unmodelled, bounded_reasons = bounded_restriction_adjustment(
        base_unmodelled,
        cpo_rate_range,
        cpo_session_range,
    )
    base_quality_reasons.extend(bounded_reasons)

    def combined_reasons(*extra: str) -> Optional[list[str]]:
        values = list(base_quality_reasons)
        values.extend(value for value in extra if value)
        return sorted(set(values)) or None

    def enabled(rule_id: str) -> bool:
        return verified_rules is None or rule_id in verified_rules

    if direct_price_info:
        direct_confidence = direct_price_info.get("confidence") or "high"
        if direct_price_info.get("confidence") is None and (
            direct_price_info.get("range") or direct_price_info.get("session_range")
        ):
            direct_confidence = "medium"
        direct_note = direct_price_info.get("note")
        unmodelled = list(direct_price_info.get("unmodelled_types") or [])
        if direct_price_info.get("restricted") and "TARIFF_RESTRICTIONS" not in unmodelled:
            unmodelled.append("TARIFF_RESTRICTIONS")
        unmodelled, bounded_direct_reasons = bounded_restriction_adjustment(
            unmodelled,
            direct_price_info.get("range"),
            direct_price_info.get("session_range"),
        )
        if unmodelled:
            direct_confidence = downgrade_confidence(direct_confidence)
            direct_note = merge_notes(
                direct_note,
                "Het OCPI-tarief bevat kosten of voorwaarden die nog niet veilig in het sessietotaal gemodelleerd kunnen worden.",
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
            cpo_session=direct_price_info.get("session", 0.0),
            unmodelled_costs=unmodelled,
            inherited_base_source=direct_price_info.get("inherited_base_source"),
            quality_reasons=sorted(set((direct_price_info.get("quality_reasons") or []) + bounded_direct_reasons)),
            energy_step_size_wh=direct_price_info.get("energy_step_size_wh"),
        )
        pricing["direct_pay"] = apply_source_metadata(direct_quote, direct_price_info)

    if enabled("anwb_free"):
        anwb_override = msp_overrides.get("anwb_free")
        if cpo_rate is not None or anwb_override:
            anwb_rate = float(anwb_override["rate"]) if anwb_override else cpo_rate
            anwb_confidence = anwb_override.get("confidence", "medium") if anwb_override else base_confidence
            anwb_note = anwb_override.get("note") if anwb_override else cpo_note
            anwb_basis = anwb_override.get("basis", cpo_source) if anwb_override else cpo_source
            anwb_range = anwb_override.get("range") if anwb_override else shifted_range(cpo_rate_range)
            if not anwb_override and any(token in op for token in ANWB_DISCOUNT_NETWORKS):
                anwb_confidence = downgrade_confidence(anwb_confidence)
                anwb_note = merge_notes(anwb_note, "ANWB noemt korting op dit netwerk; de app kan een lager tarief tonen.")
            route_session = float(anwb_override.get("session", 0.0)) if anwb_override else float(cpo_session or 0.0)
            route_session_range = anwb_override.get("session_range") if anwb_override else cpo_session_range
            route_unmodelled = list(anwb_override.get("unmodelled_types") or []) if anwb_override else base_unmodelled
            total_session = route_session + 0.89
            anwb_discount = not anwb_override and any(token in op for token in ANWB_DISCOUNT_NETWORKS)
            anwb_reasons = list(anwb_override.get("quality_reasons") or []) if anwb_override else combined_reasons("potential_network_discount" if anwb_discount else "")
            quote = make_quote(
                anwb_rate, total_session, anwb_confidence, anwb_basis,
                note=anwb_note,
                price_range=anwb_range,
                session_range=combined_session_range(route_session, route_session_range, 0.89),
                route="msp_roaming", relation="roaming",
                cpo_session=route_session, msp_session=0.89,
                unmodelled_costs=route_unmodelled,
                source_quality="high" if anwb_override else None,
                price_specificity="network" if anwb_override else None,
                quality_reasons=anwb_reasons,
                energy_step_size_wh=anwb_override.get("energy_step_size_wh") if anwb_override else cpo_energy_step_size_wh,
            )
            pricing["anwb_free"] = apply_source_metadata(quote, anwb_override)

    if enabled("tap_light"):
        tap_override = msp_overrides.get("tap_light")
        if cpo_rate is not None or tap_override:
            tap_rate = float(tap_override["rate"]) if tap_override else cpo_rate
            tap_confidence = tap_override.get("confidence", "medium") if tap_override else base_confidence
            tap_basis = tap_override.get("basis", cpo_source) if tap_override else cpo_source
            tap_note = merge_notes(
                tap_override.get("note") if tap_override else cpo_note,
                "Tap Light rekent 5% transactiekosten over het gemodelleerde laadpaaltarief.",
            )
            route_session = float(tap_override.get("session", 0.0)) if tap_override else float(cpo_session or 0.0)
            route_session_range = tap_override.get("session_range") if tap_override else cpo_session_range
            route_unmodelled = list(tap_override.get("unmodelled_types") or []) if tap_override else base_unmodelled
            quote = make_quote(
                tap_rate, route_session, tap_confidence, tap_basis,
                note=tap_note,
                price_range=tap_override.get("range") if tap_override else shifted_range(cpo_rate_range),
                transaction_percentage=0.05,
                session_range=route_session_range,
                route="msp_roaming", relation="roaming",
                cpo_session=route_session,
                percentage_scope="cpo_subtotal",
                unmodelled_costs=route_unmodelled,
                source_quality="high" if tap_override else None,
                price_specificity="network" if tap_override else None,
                quality_reasons=list(tap_override.get("quality_reasons") or []) if tap_override else combined_reasons(),
                energy_step_size_wh=tap_override.get("energy_step_size_wh") if tap_override else cpo_energy_step_size_wh,
            )
            pricing["tap_light"] = apply_source_metadata(quote, tap_override)

    # Vattenfall publishes a fixed EUR 0.35 start fee when the InCharge charge
    # card is used at a charging point that is not operated by Vattenfall. A
    # roaming quote is still only created when the CPO publishes a Vattenfall-
    # specific kWh rate for that network. We do not assume that the generic CPO
    # tariff is also the Vattenfall MSP tariff.
    vf_override = msp_overrides.get("vattenfall")
    own_vattenfall = is_msp_home_network("vattenfall", operator_name, party_id)
    vattenfall_rule_ready = enabled("vattenfall") and enabled("vattenfall_roaming_fee")
    if own_vattenfall and cpo_rate is not None:
        # The own-network CPO tariff is itself the relevant InCharge tariff;
        # it does not depend on the separate roaming start-fee rule.
        pricing["vattenfall"] = make_quote(
            cpo_rate, float(cpo_session or 0.0), base_confidence, cpo_source,
            note=cpo_note,
            price_range=shifted_range(cpo_rate_range),
            session_range=cpo_session_range,
            route="msp_home", relation="own_network",
            cpo_session=cpo_session,
            unmodelled_costs=base_unmodelled,
            quality_reasons=combined_reasons(),
            energy_step_size_wh=cpo_energy_step_size_wh,
        )
    elif vattenfall_rule_ready and vf_override:
        route_session = float(vf_override.get("session", 0.0))
        route_session_range = vf_override.get("session_range")
        route_unmodelled = list(vf_override.get("unmodelled_types") or [])
        note = merge_notes(
            vf_override.get("note"),
            "Vattenfall rekent bij laadpalen die niet van Vattenfall zijn €0,35 starttarief per laadsessie.",
        )
        quote = make_quote(
            float(vf_override["rate"]),
            route_session + VATTENFALL_ROAMING_SESSION_FEE,
            vf_override.get("confidence", "medium"),
            vf_override.get("basis", "official_cpo_msp_rate"),
            note=note,
            price_range=vf_override.get("range"),
            session_range=combined_session_range(
                route_session, route_session_range, VATTENFALL_ROAMING_SESSION_FEE
            ),
            route="msp_roaming", relation="roaming",
            cpo_session=route_session, msp_session=VATTENFALL_ROAMING_SESSION_FEE,
            unmodelled_costs=route_unmodelled,
            source_quality="high",
            price_specificity="network",
            quality_reasons=list(vf_override.get("quality_reasons") or []),
            energy_step_size_wh=vf_override.get("energy_step_size_wh"),
        )
        quote = apply_source_metadata(quote, vf_override)
        quote["fee_source_id"] = "vattenfall_roaming_fee"
        quote["fee_source_url"] = VATTENFALL_ROAMING_FEE_SOURCE_URL
        pricing["vattenfall"] = quote

    if enabled("eflux_flex") and cpo_rate is not None:
        own_eflux = is_msp_home_network("eflux_flex", operator_name, party_id)
        markup = 0.0 if own_eflux else 0.024
        ef_confidence = base_confidence if own_eflux else downgrade_confidence(base_confidence)
        msp_session_range = None if own_eflux else [0.31, 0.79]
        ef_note = cpo_note if own_eflux else merge_notes(
            cpo_note,
            "Buiten E-Flux geldt €0,024/kWh toeslag. Op Hubject, Gireve of e-clearing kan daarnaast €0,48 per sessie gelden; dit is als bandbreedte meegenomen.",
        )
        pricing["eflux_flex"] = make_quote(
            cpo_rate + markup, float(cpo_session or 0.0) + 0.31, ef_confidence, cpo_source,
            note=ef_note,
            price_range=shifted_range(cpo_rate_range, markup),
            session_range=combined_session_range(cpo_session, cpo_session_range, 0.31, msp_session_range),
            route="msp_home" if own_eflux else "msp_roaming",
            relation="own_network" if own_eflux else "roaming",
            cpo_session=cpo_session, msp_session=0.31,
            unmodelled_costs=base_unmodelled,
            quality_reasons=combined_reasons(),
            energy_step_size_wh=cpo_energy_step_size_wh,
            cpo_kwh=cpo_rate,
            cpo_kwh_range=cpo_rate_range,
            msp_kwh=markup,
        )

    if enabled("shell_basic"):
        is_dc = str(current_type).upper() == "DC"
        own_shell = is_msp_home_network("shell_basic", operator_name, party_id)
        shell_override = msp_overrides.get("shell_basic") if not is_dc else None
        if shell_override:
            route_session = float(shell_override.get("session", 0.0))
            route_session_range = shell_override.get("session_range")
            route_unmodelled = list(shell_override.get("unmodelled_types") or [])
            quote = make_quote(
                float(shell_override["rate"]), route_session + 0.35,
                shell_override.get("confidence", "medium"),
                shell_override.get("basis", "official_cpo_msp_rate"),
                note=shell_override.get("note"),
                price_range=shell_override.get("range"),
                session_range=combined_session_range(route_session, route_session_range, 0.35),
                route="msp_home" if own_shell else "msp_roaming",
                relation="own_network" if own_shell else "roaming",
                cpo_session=route_session, msp_session=0.35,
                unmodelled_costs=route_unmodelled,
                source_quality="high",
                price_specificity="network",
                quality_reasons=sorted(set((shell_override.get("quality_reasons") or []) + ["potential_msp_blocking_fee"])),
                energy_step_size_wh=shell_override.get("energy_step_size_wh"),
            )
            pricing["shell_basic"] = apply_source_metadata(quote, shell_override)
        elif is_dc:
            if own_shell:
                pricing["shell_basic"] = make_quote(
                    0.78, 0.35, "medium", "published_shell",
                    note="Gepubliceerd Shell Recharge Basic snellaadtarief in Nederland. Shell meldt dat eventuele extra/blokkeerkosten per laadpunt kunnen verschillen; daarom indicatief.",
                    route="msp_home", relation="own_network", msp_session=0.35,
                    quality_reasons=["potential_msp_blocking_fee"],
                )
            else:
                pricing["shell_basic"] = make_quote(
                    0.82, 0.35, "low", "published_band",
                    note="Shell publiceert een DC-prijsband; de exacte paalprijs en eventuele extra/blokkeerkosten staan in de Shell-app.",
                    price_range=[0.79, 0.85],
                    route="msp_roaming", relation="roaming", msp_session=0.35,
                    quality_reasons=["potential_msp_blocking_fee"],
                )
        elif str(current_type).upper() == "AC" and not own_shell:
            pricing["shell_basic"] = make_quote(
                0.55, 0.35, "low", "published_band",
                note="Shell publiceert voor AC-laders bij andere aanbieders een prijsband; de exacte paalprijs en eventuele extra/blokkeerkosten staan in de Shell-app.",
                price_range=[0.50, 0.60],
                route="msp_roaming", relation="roaming", msp_session=0.35,
                quality_reasons=["potential_msp_blocking_fee"],
            )

    if enabled("laadkompas_free") and cpo_rate is not None:
        # The canonical Laadkompas page currently states EUR 0.47 in its title,
        # product copy and FAQ, but still contains one contradictory EUR 0.39
        # paragraph. Quality Model v2.1 treats that live official-source conflict
        # as bounded uncertainty rather than choosing one value silently. When
        # the legacy wording disappears, the monitor disables only the conflict
        # marker and EUR 0.47 becomes exact again.
        laadkompas_conflict = enabled("laadkompas_legacy_039")
        laadkompas_fee = 0.43 if laadkompas_conflict else 0.47
        laadkompas_fee_range = [0.39, 0.47] if laadkompas_conflict else None
        laadkompas_note = cpo_note
        laadkompas_reasons = combined_reasons("official_source_internal_conflict" if laadkompas_conflict else "")
        if laadkompas_conflict:
            laadkompas_note = merge_notes(
                laadkompas_note,
                "De officiële Laadkompas-pagina noemt overwegend EUR 0,47 per sessie, maar bevat ook nog een conflicterende EUR 0,39-vermelding. De kaart rekent daarom met de volledige EUR 0,39-EUR 0,47 sessieband.",
            )
        pricing["laadkompas_free"] = make_quote(
            cpo_rate, float(cpo_session or 0.0) + laadkompas_fee, base_confidence, cpo_source,
            note=laadkompas_note,
            price_range=shifted_range(cpo_rate_range),
            session_range=combined_session_range(
                cpo_session, cpo_session_range, laadkompas_fee, laadkompas_fee_range
            ),
            route="msp_roaming", relation="roaming",
            cpo_session=cpo_session, msp_session=laadkompas_fee,
            unmodelled_costs=base_unmodelled,
            quality_reasons=laadkompas_reasons,
            energy_step_size_wh=cpo_energy_step_size_wh,
        )

    # A failed MSP source monitor must not erase independently verified CPO or
    # network-specific information. For a route whose provider-side monetary
    # model is not currently validated, retain the known price component as a
    # partial floor. The frontend shows it as "vanaf" and never ranks it.
    # This is deliberately different from last-known-good pricing: no stale MSP
    # fee or markup is reused.
    for pass_id in ("anwb_free", "tap_light", "vattenfall", "eflux_flex", "shell_basic", "laadkompas_free"):
        if pass_id in pricing:
            continue
        override = msp_overrides.get(pass_id)
        if override:
            partial_rate = float(override["rate"])
            partial_range = override.get("range")
            partial_session = float(override.get("session", 0.0))
            partial_session_range = override.get("session_range")
            partial_basis = override.get("basis", "official_cpo_msp_rate")
            partial_source_quality = "high"
            partial_specificity = "network"
            partial_unmodelled = list(override.get("unmodelled_types") or [])
            partial_reasons = list(override.get("quality_reasons") or [])
            partial_step = override.get("energy_step_size_wh")
            partial_note = override.get("note")
        elif cpo_rate is not None:
            partial_rate = float(cpo_rate)
            partial_range = shifted_range(cpo_rate_range)
            partial_session = float(cpo_session or 0.0)
            partial_session_range = cpo_session_range
            partial_basis = cpo_source
            partial_source_quality = None
            partial_specificity = None
            partial_unmodelled = list(base_unmodelled)
            partial_reasons = list(base_quality_reasons)
            partial_step = cpo_energy_step_size_wh
            partial_note = cpo_note
        else:
            continue

        partial_unmodelled.append("MSP_TARIFF_COMPONENTS_UNKNOWN")
        partial_note = merge_notes(
            partial_note,
            "De actuele providerkosten voor deze betaalroute konden in deze run niet volledig worden gevalideerd. Alleen de bekende prijscomponent wordt als ondergrens getoond; deze route wordt niet gerangschikt.",
        )
        partial_quote = make_quote(
            partial_rate,
            partial_session,
            "low",
            partial_basis,
            note=partial_note,
            price_range=partial_range,
            session_range=partial_session_range,
            route="msp_home" if is_msp_home_network(pass_id, operator_name, party_id) else "msp_roaming",
            relation="own_network" if is_msp_home_network(pass_id, operator_name, party_id) else "roaming",
            cpo_session=partial_session,
            unmodelled_costs=sorted(set(partial_unmodelled)),
            source_quality=partial_source_quality,
            price_specificity=partial_specificity,
            quality_reasons=sorted(set(partial_reasons)),
            energy_step_size_wh=partial_step,
        )
        pricing[pass_id] = apply_source_metadata(partial_quote, override)

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


def connector_current_type(conn: dict) -> tuple[str, str]:
    """Return AC/DC using OCPI power_type, with conservative standard fallback.

    ``power_type`` is the authoritative OCPI field. The fallback exists only
    for incomplete feeds and is limited to connector standards whose current
    type follows from the standard itself. Power is never used for AC/DC.
    """
    power_type = str(conn.get("power_type") or "").upper()
    if power_type == "DC":
        return "DC", "ocpi_power_type"
    if power_type.startswith("AC_"):
        return "AC", "ocpi_power_type"

    standard = str(conn.get("standard") or "").upper()
    dc_standards = {
        "CHADEMO", "CHAOJI", "GBT_DC",
        "IEC_62196_T1_COMBO", "IEC_62196_T2_COMBO",
    }
    ac_standards = {
        "GBT_AC",
        "DOMESTIC_A", "DOMESTIC_B", "DOMESTIC_C", "DOMESTIC_D",
        "DOMESTIC_E", "DOMESTIC_F", "DOMESTIC_G", "DOMESTIC_H",
        "DOMESTIC_I", "DOMESTIC_J", "DOMESTIC_K", "DOMESTIC_L",
        "DOMESTIC_M", "DOMESTIC_N", "DOMESTIC_O",
        "IEC_60309_2_SINGLE_16", "IEC_60309_2_THREE_16",
        "IEC_60309_2_THREE_32", "IEC_60309_2_THREE_64",
        "IEC_62196_T1", "IEC_62196_T2", "IEC_62196_T3A", "IEC_62196_T3C",
        "NEMA_5_20", "NEMA_6_30", "NEMA_6_50", "NEMA_10_30",
        "NEMA_10_50", "NEMA_14_30", "NEMA_14_50",
    }
    if standard in dc_standards:
        return "DC", "connector_standard_inference"
    if standard in ac_standards:
        return "AC", "connector_standard_inference"
    return "UNKNOWN", "unknown"


def connector_power_kw(conn: dict) -> float:
    """Return connector power, deriving it from voltage/current when needed.

    OCPI makes ``max_electric_power`` optional. ``max_voltage`` is line-to-
    neutral for AC_3_PHASE, so multiplying by the number of phases gives the
    correct nominal maximum when the explicit power field is absent.
    """
    value = conn.get("max_electric_power")
    if value not in (None, "", 0, 0.0):
        try:
            return round(float(value) / 1000, 1)
        except (TypeError, ValueError):
            pass

    try:
        voltage = float(conn.get("max_voltage"))
        amperage = float(conn.get("max_amperage"))
    except (TypeError, ValueError):
        return 0.0

    power_type = str(conn.get("power_type") or "").upper()
    phases = {
        "AC_1_PHASE": 1,
        "AC_2_PHASE": 2,
        "AC_2_PHASE_SPLIT": 2,
        "AC_3_PHASE": 3,
        "DC": 1,
    }.get(power_type)
    if phases is None:
        return 0.0
    return round((voltage * amperage * phases) / 1000, 1)


def connector_decision_status(pricing: dict) -> str:
    grades = [
        (quote.get("quality") or {}).get("decision_grade", "indicative")
        for quote in pricing.values()
        if quote.get("kwh") is not None
    ]
    if sum(1 for grade in grades if grade == "reliable") >= 2:
        return "reliable"
    if sum(1 for grade in grades if grade in {"reliable", "indicative"}) >= 2:
        return "indicative"
    return "insufficient"


def _profile_key(row: dict) -> str:
    """Stable grouping key for physically different but price-equivalent connectors."""
    payload = {
        "connector_type": row.get("connector_type"),
        "standard": row.get("standard"),
        "current_type": row.get("current_type"),
        "power_kw": row.get("power_kw"),
        "tariff": row.get("tariff"),
        "direct_payment": {k: v for k, v in row.get("direct_payment", {}).items() if k not in {"reason"}},
        "pricing": row.get("pricing"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def group_connector_profiles(rows: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        key = _profile_key(row)
        if key not in groups:
            groups[key] = {
                "connector_type": row.get("connector_type"),
                "standard": row.get("standard"),
                "current_type": row.get("current_type"),
                "current_type_source": row.get("current_type_source"),
                "ocpi_power_types": sorted({row.get("ocpi_power_type")} - {None, ""}),
                "power_kw": row.get("power_kw", 0.0),
                "count": 0,
                "available_count": 0,
                "statuses": set(),
                "evse_ids": set(),
                "connector_ids": set(),
                "last_updated_values": [],
                "tariff": row.get("tariff"),
                "direct_payment": row.get("direct_payment", {}),
                "pricing": row.get("pricing", {}),
                "decision_status": connector_decision_status(row.get("pricing", {})),
            }
        group = groups[key]
        group["count"] += 1
        if row.get("status") == "AVAILABLE":
            group["available_count"] += 1
        if row.get("status"):
            group["statuses"].add(row["status"])
        if row.get("evse_id"):
            group["evse_ids"].add(str(row["evse_id"]))
        if row.get("connector_id"):
            group["connector_ids"].add(str(row["connector_id"]))
        if row.get("last_updated"):
            group["last_updated_values"].append(row["last_updated"])
        if row.get("ocpi_power_type"):
            group["ocpi_power_types"] = sorted(set(group["ocpi_power_types"]) | {row["ocpi_power_type"]})

    result = []
    for index, group in enumerate(groups.values(), 1):
        group["statuses"] = sorted(group["statuses"])
        group["evse_ids"] = sorted(group["evse_ids"])
        group["connector_ids"] = sorted(group["connector_ids"])
        group["last_updated"] = max(group.pop("last_updated_values"), default=None)
        group["id"] = f"connector-{index}"
        result.append(group)

    order = {"AC": 0, "DC": 1, "UNKNOWN": 2}
    result.sort(key=lambda row: (
        order.get(row.get("current_type"), 3),
        row.get("connector_type") or "",
        row.get("power_kw") or 0,
        row.get("id") or "",
    ))
    for index, group in enumerate(result, 1):
        group["id"] = f"connector-{index}"
    return result


def load_pricing_rule_verification(path: str = "pricing-source-status.json") -> tuple[Optional[set[str]], dict]:
    """Load transient daily source-check status.

    Missing status keeps local/manual runs usable, while the GitHub workflow
    always writes this file and therefore gets fail-closed behaviour.
    """
    if not os.path.exists(path):
        return None, {"mode": "unverified_local_fallback", "checked_at": None, "all_ok": None, "disabled_rules": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        results = data.get("results") or []
        enabled = {row.get("id") for row in results if row.get("status") == "ok" and row.get("id")}
        disabled = sorted({row.get("id") for row in results if row.get("status") != "ok" and row.get("id")})
        return enabled, {
            "mode": "daily_verified_fail_closed",
            "checked_at": data.get("checked_at"),
            "all_ok": bool(data.get("all_ok")),
            "disabled_rules": disabled,
            "results": results,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return set(), {
            "mode": "verification_file_invalid_fail_closed",
            "checked_at": None,
            "all_ok": False,
            "disabled_rules": [p["id"] for p in PASSES if p.get("kind") == "msp"],
            "error": str(exc),
        }


def process_location(
    loc: dict,
    tariff_map: dict,
    operator_median: Optional[dict] = None,
    boundary: Optional[list] = None,
    official_direct: Optional[dict] = None,
    official_cpo: Optional[dict] = None,
    official_msp: Optional[dict] = None,
    verified_rules: Optional[set[str]] = None,
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
    location_capabilities: set[str] = set()
    raw_connectors: list[dict] = []

    for evse in loc.get("evses", []):
        evse_id = evse.get("evse_id") or evse.get("uid")
        if evse_id:
            evse_ids.append(str(evse_id))
        evse_capabilities = {str(value).upper() for value in (evse.get("capabilities") or []) if value}
        location_capabilities.update(evse_capabilities)
        status = evse.get("status", "UNKNOWN")
        evse_updated = evse.get("last_updated")

        for conn in evse.get("connectors", []):
            current_type, current_type_source = connector_current_type(conn)
            power_kw = connector_power_kw(conn)
            cpo_info = None
            used_tariff_id = None
            source = "unknown"
            cpo_note = None
            direct_price_info = None

            tariff_ids = conn.get("tariff_ids") or []
            for tariff_id in tariff_ids:
                price_info = get_cpo_price_info(tariff_id, tariff_map, country_code, party_id)
                if price_info is not None:
                    cpo_info = price_info
                    used_tariff_id = str(tariff_id)
                    source = "ndw"
                    break

            for tariff_id in tariff_ids:
                ad_hoc_info = get_ad_hoc_price_info(tariff_id, tariff_map, country_code, party_id)
                if ad_hoc_info is not None:
                    ad_hoc_info = dict(ad_hoc_info)
                    ad_hoc_info["basis"] = "ndw_ad_hoc"
                    direct_price_info = ad_hoc_info
                    break

            direct_supported_pre, direct_reason_pre = direct_payment_supported(
                operator, party_id, evse_capabilities,
                has_ad_hoc_tariff=direct_price_info is not None,
            )

            if cpo_info is None:
                supplemental_cpo = supplemental_cpo_price_info(party_id, current_type, official_cpo)
                supplemental_rule_ids = []
                if supplemental_cpo:
                    supplemental_rule_ids = list(supplemental_cpo.get("verification_rule_ids") or [])
                    if not supplemental_rule_ids and supplemental_cpo.get("source_id"):
                        supplemental_rule_ids = [supplemental_cpo["source_id"]]
                rules_enabled = verified_rules is None or all(rule_id in verified_rules for rule_id in supplemental_rule_ids)
                if supplemental_cpo and rules_enabled:
                    cpo_info = supplemental_cpo
                    source = supplemental_cpo.get("basis", "official_cpo_tariff")
                    cpo_note = supplemental_cpo.get("note")

            if cpo_info is None and (verified_rules is None or "totalenergies_mrae" in verified_rules):
                regional = totalenergies_mrae_fallback(operator, current_type)
                if regional:
                    cpo_info = {
                        "rate": regional["rate"],
                        "range": regional.get("range"),
                        "session": regional.get("session", 0.0),
                        "session_range": regional.get("session_range"),
                        "unmodelled_types": regional.get("unmodelled_types", []),
                        "quality_reasons": regional.get("quality_reasons", []),
                        "energy_step_size_wh": regional.get("energy_step_size_wh"),
                        "tariff_id": None,
                        "restricted": False,
                    }
                    cpo_note = regional["note"]
                    source = regional["source"]

            if cpo_info is None and operator_median:
                median = find_operator_median(operator, operator_median)
                if median is not None:
                    cpo_info = {
                        "rate": median,
                        "range": None,
                        "session": 0.0,
                        "session_range": None,
                        "unmodelled_types": ["LOCATION_TARIFF_COMPONENTS_UNKNOWN"],
                        "quality_reasons": ["operator_estimate_only"],
                        "energy_step_size_wh": None,
                        "tariff_id": None,
                        "restricted": False,
                    }
                    cpo_note = "Operator-mediaan uit andere NDW-tarieven; locatiegebonden sessie-, tijd- of afrondingscomponenten zijn niet bekend. Niet gebruiken voor ranking."
                    source = "operator_median"

            cpo_rate = cpo_info.get("rate") if cpo_info else None
            cpo_range = cpo_info.get("range") if cpo_info else None
            cpo_session = cpo_info.get("session", 0.0) if cpo_info else 0.0
            cpo_session_range = cpo_info.get("session_range") if cpo_info else None
            cpo_unmodelled = list(cpo_info.get("unmodelled_types") or []) if cpo_info else []
            cpo_quality_reasons = list(cpo_info.get("quality_reasons") or []) if cpo_info else []
            cpo_energy_step_size_wh = cpo_info.get("energy_step_size_wh") if cpo_info else None

            if direct_price_info is None:
                direct_price_info = supplemental_direct_price_info(
                    party_id,
                    cpo_rate,
                    cpo_range,
                    source,
                    official_direct,
                    cpo_session=cpo_session,
                    cpo_session_range=cpo_session_range,
                    cpo_unmodelled_types=cpo_unmodelled,
                    cpo_restricted=bool(cpo_info and cpo_info.get("restricted")),
                    cpo_energy_step_size_wh=cpo_energy_step_size_wh,
                    cpo_quality_reasons=cpo_quality_reasons,
                    current_type=current_type,
                )

            # Source precedence for direct payment is deliberately strict:
            # 1) explicit OCPI AD_HOC_PAYMENT, 2) verified official CPO direct
            # price, 3) only then a generic OCPI tariff without Tariff.type.
            # This prevents a generic tariff/restriction set from masking a
            # more explicit operator-published direct price (e.g. Ubitricity).
            if direct_price_info is None and direct_supported_pre:
                for tariff_id in tariff_ids:
                    generic_info = get_ad_hoc_price_info(
                        tariff_id, tariff_map, country_code, party_id, allow_unspecified=True
                    )
                    if generic_info is not None:
                        generic_info = dict(generic_info)
                        generic_info["basis"] = "ndw_ad_hoc_compatible"
                        generic_info["note"] = merge_notes(
                            generic_info.get("note"),
                            "OCPI Tariff.type ontbreekt; volgens OCPI 2.2.1 is dit tarief voor alle sessies geldig. Direct betalen is afzonderlijk bevestigd.",
                        )
                        direct_price_info = generic_info
                        break

            direct_supported, direct_reason = direct_supported_pre, direct_reason_pre
            if direct_price_info and direct_price_info.get("basis") == "ndw_ad_hoc":
                direct_supported, direct_reason = True, "ocpi_ad_hoc_tariff"
            elif direct_price_info and direct_price_info.get("basis") == "ndw_ad_hoc_compatible":
                direct_supported, direct_reason = direct_supported_pre, direct_reason_pre
            elif direct_price_info and direct_price_info.get("basis") not in {"ndw_ad_hoc", "ndw_ad_hoc_compatible"}:
                direct_supported, direct_reason = True, "official_operator_source"

            pricing = build_pricing(
                cpo_rate,
                source,
                operator,
                current_type,
                cpo_rate_range=cpo_range,
                cpo_note=cpo_note,
                party_id=party_id,
                direct_price_info=direct_price_info,
                msp_price_overrides=(official_msp or {}).get(party_id),
                cpo_session=cpo_session,
                cpo_session_range=cpo_session_range,
                cpo_unmodelled_types=cpo_unmodelled,
                cpo_restricted=bool(cpo_info and cpo_info.get("restricted")),
                cpo_energy_step_size_wh=cpo_energy_step_size_wh,
                cpo_quality_reasons=cpo_quality_reasons,
                verified_rules=verified_rules,
            )

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
                    "quality_reasons": direct_price_info.get("quality_reasons", []),
                    "energy_step_size_wh": direct_price_info.get("energy_step_size_wh"),
                    "restricted": bool(direct_price_info.get("restricted")),
                    "basis": direct_price_info.get("basis", "ndw_ad_hoc"),
                    "source_id": direct_price_info.get("source_id"),
                    "source_url": direct_price_info.get("source_url"),
                    "source_checked_at": direct_price_info.get("source_checked_at"),
                })

            raw_connectors.append({
                "evse_id": str(evse_id) if evse_id else None,
                "connector_id": str(conn.get("id")) if conn.get("id") is not None else None,
                "status": status,
                "last_updated": conn.get("last_updated") or evse_updated,
                "connector_type": connector_type_label(conn),
                "standard": conn.get("standard"),
                "ocpi_power_type": conn.get("power_type"),
                "current_type": current_type,
                "current_type_source": current_type_source,
                "power_kw": power_kw,
                "tariff": {
                    "id": used_tariff_id,
                    "source": source,
                    "rate": cpo_rate,
                    "rate_range": cpo_range,
                    "session": cpo_session,
                    "session_range": cpo_session_range,
                    "unmodelled_types": cpo_unmodelled,
                    "quality_reasons": cpo_quality_reasons,
                    "energy_step_size_wh": cpo_energy_step_size_wh,
                    "restricted": bool(cpo_info and cpo_info.get("restricted")),
                    "note": cpo_note,
                    "source_id": cpo_info.get("source_id") if cpo_info else None,
                    "source_url": cpo_info.get("source_url") if cpo_info else None,
                    "context_source_url": cpo_info.get("context_source_url") if cpo_info else None,
                    "source_checked_at": cpo_info.get("source_checked_at") if cpo_info else None,
                    "quality": {
                        "source_quality": source_quality_for_basis(source),
                        "price_specificity": price_specificity_for_basis(source),
                    },
                },
                "direct_payment": direct_payment,
                "pricing": pricing,
            })

    if not raw_connectors:
        return None

    connector_options = group_connector_profiles(raw_connectors)
    statuses = [row.get("status") for row in raw_connectors if row.get("status")]
    available = "AVAILABLE" in statuses
    connector_types = list(dict.fromkeys(row.get("connector_type") for row in raw_connectors if row.get("connector_type")))
    max_power = max((row.get("power_kw", 0.0) for row in raw_connectors), default=0.0)

    # Location-level summaries are conservative. Pricing itself lives on the
    # connector profiles. Legacy top-level pricing is exposed only when there is
    # a single unambiguous profile, preventing old clients from silently mixing
    # AC and DC or different connector tariffs.
    single_profile = connector_options[0] if len(connector_options) == 1 else None
    legacy_pricing = single_profile.get("pricing", {}) if single_profile else {}
    legacy_tariff = single_profile.get("tariff", {}) if single_profile else {}

    direct_supported = any(option.get("direct_payment", {}).get("supported") for option in connector_options)
    direct_priced = any(option.get("direct_payment", {}).get("priced") for option in connector_options)
    direct_payment = {
        "supported": direct_supported,
        "reason": "connector_profile_summary",
        "priced": direct_priced,
    }
    if single_profile:
        direct_payment = dict(single_profile.get("direct_payment", direct_payment))

    last_updated_values = [
        value
        for value in [loc.get("last_updated"), *(evse.get("last_updated") for evse in loc.get("evses", [])), *(row.get("last_updated") for row in raw_connectors)]
        if value
    ]
    last_updated = max(last_updated_values) if last_updated_values else None

    status_order = {"reliable": 2, "indicative": 1, "insufficient": 0}
    best_decision = max(
        (option.get("decision_status", "insufficient") for option in connector_options),
        key=lambda value: status_order.get(value, 0),
        default="insufficient",
    )
    fully_reliable = bool(connector_options) and all(option.get("decision_status") == "reliable" for option in connector_options)

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
        "connector_options": connector_options,
        "max_power": max_power,
        "num_evses": len(loc.get("evses", [])),
        "available": available,
        "statuses": sorted(set(statuses)),
        "last_updated": last_updated,
        "direct_payment": direct_payment,
        "decision_status": best_decision,
        "fully_reliable": fully_reliable,
        "pricing": legacy_pricing,
        "pricing_source": legacy_tariff.get("source", "mixed" if len(connector_options) > 1 else "unknown"),
        "pricing_note": legacy_tariff.get("note"),
        "cpo_rate": legacy_tariff.get("rate"),
        "cpo_rate_range": legacy_tariff.get("rate_range"),
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
        op_key = operator_key(operator)
        if "totalenergies" not in op_key and "total energies" not in op_key:
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

    verified_rules, pricing_rule_verification = load_pricing_rule_verification()
    if verified_rules is None:
        print("  Pricing rule verification: local fallback (no transient status file)")
    else:
        disabled = pricing_rule_verification.get("disabled_rules", [])
        print(f"  Pricing rule verification: {len(verified_rules)} rules enabled, {len(disabled)} disabled")
        if disabled:
            print(f"    Disabled fail-closed: {', '.join(disabled)}")

    print("\n[5/5] Filtering to gemeente Huizen...")
    results = []
    for loc in locations:
        processed = process_location(
            loc,
            tariff_map,
            operator_median,
            boundary,
            official_direct=official_harvest["direct_by_party"],
            official_cpo=official_harvest["cpo_by_party"],
            official_msp=official_harvest["msp_by_party"],
            verified_rules=verified_rules,
        )
        if processed:
            results.append(processed)

    profiles = [profile for row in results for profile in row.get("connector_options", [])]
    direct = sum(1 for p in profiles if p.get("tariff", {}).get("source") == "ndw")
    median = sum(1 for p in profiles if p.get("tariff", {}).get("source") == "operator_median")
    regional_sources = {
        "totalenergies_mrae", "totalenergies_mrae_dc",
        "vattenfall_mrae", "vattenfall_mrae_2021", "vattenfall_mrae_2024",
    }
    regional = sum(1 for p in profiles if p.get("tariff", {}).get("source") in regional_sources)
    unknown = sum(1 for p in profiles if p.get("tariff", {}).get("rate") is None)
    comparison_ready = sum(1 for r in results if r.get("decision_status") in {"reliable", "indicative"})
    decision_ready = sum(1 for r in results if r.get("decision_status") == "reliable")
    indicative_only = sum(1 for r in results if r.get("decision_status") == "indicative")
    insufficient = sum(1 for r in results if r.get("decision_status") == "insufficient")
    fully_reliable = sum(1 for r in results if r.get("fully_reliable"))
    adhoc_priced = sum(1 for p in profiles if p.get("direct_payment", {}).get("priced"))
    adhoc_priced_ndw = sum(
        1 for p in profiles
        if p.get("pricing", {}).get("direct_pay", {}).get("basis") in {"ndw_ad_hoc", "ndw_ad_hoc_compatible"}
    )
    adhoc_priced_official = sum(
        1 for p in profiles
        if p.get("pricing", {}).get("direct_pay", {}).get("basis") in {"official_cpo_adhoc", "official_cpo_direct_rule"}
    )
    direct_payment_known = sum(1 for p in profiles if p.get("direct_payment", {}).get("supported"))
    msp_quotes_official = sum(
        1 for p in profiles
        for pass_id, quote in p.get("pricing", {}).items()
        if pass_id != "direct_pay" and quote.get("basis") == "official_cpo_msp_rate"
    )

    print(f"  Locations in area:       {len(results)}")
    print(f"  Connector profiles:      {len(profiles)}")
    print(f"  Direct NDW CPO tariff:   {direct}")
    print(f"  Operator-median tariff:  {median}")
    print(f"  Official MRA-E fallback: {regional}")
    print(f"  Unknown CPO base tariff: {unknown}")
    print(f"  Reliable locations:      {decision_ready}")
    print(f"  Indicative locations:    {indicative_only}")
    print(f"  Insufficient locations:  {insufficient}")
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
        "schema_version": 5,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "NDW open data plus verified public CPO pricing sources",
        "official_pricing_harvest": {
            "checked_at": official_harvest["checked_at"],
            "sources": official_harvest["sources"],
        },
        "pricing_rule_verification": pricing_rule_verification,
        "regional_pricing": {
            "totalenergies_mrae": {
                "ac_range": list(TOTALENERGIES_MRAE_AC_RANGE),
                "dc_rate": TOTALENERGIES_MRAE_DC_RATE,
                "verified_at": TOTALENERGIES_MRAE_VERIFIED_AT,
                "source_url": TOTALENERGIES_MRAE_SOURCE_URL,
                "history_source_url": TOTALENERGIES_MRAE_HISTORY_SOURCE_URL,
                "dynamic_source_url": TOTALENERGIES_MRAE_DYNAMIC_SOURCE_URL,
                "laadwerk_tariff_source_url": LAADWERK_TARIFF_SOURCE_URL,
                "resolution_status": "unresolved_per_connector",
                "resolution_note": TOTALENERGIES_MRAE_RESOLUTION_NOTE,
                "rejected_heuristics": ["tnlp_or_pp_number", "evse_id_pattern", "power_kw", "last_updated"],
                "municipality_source_url": HUIZEN_CHARGING_SOURCE_URL,
                "laadwerk_source_url": LAADWERK_SOURCE_URL,
            },
            "vattenfall_mrae": {
                "verified_at": VATTENFALL_MRAE_VERIFIED_AT,
                "source_url": VATTENFALL_PUBLIC_TARIFF_SOURCE_URL,
                "laadwerk_tariff_source_url": LAADWERK_TARIFF_SOURCE_URL,
                "resolution_status": "unresolved_per_connector_without_strong_identifier",
                "resolution_note": VATTENFALL_MRAE_RESOLUTION_NOTE,
                "rejected_heuristics": ["address_only", "proximity_only", "hardware_age", "last_updated"],
                "municipality_source_url": HUIZEN_CHARGING_SOURCE_URL,
            },
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
            "connector_profiles": len(profiles),
            "available_snapshot": sum(1 for r in results if r["available"]),
            "ndw_priced": direct,
            "median_priced": median,
            "regional_priced": regional,
            "unknown_base_rate": unknown,
            "comparison_ready": comparison_ready,
            "decision_ready": decision_ready,
            "indicative_only": indicative_only,
            "insufficient": insufficient,
            "fully_reliable": fully_reliable,
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
