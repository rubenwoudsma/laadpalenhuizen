#!/usr/bin/env python3
"""Verify the public pricing assumptions used by Laadpalen Huizen.

The checker never changes a tariff. It produces a human-readable report and,
optionally, a machine-readable status file consumed by the daily data pipeline.
A failed source check therefore disables the affected static pricing rule for
that run instead of silently continuing with an unverified assumption.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "pricing-sources.json"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36 laadpalenhuizen-pricing-monitor/2.0"


class TextExtractor(HTMLParser):
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


def normalize_page(raw_html: str) -> str:
    parser = TextExtractor()
    parser.feed(raw_html)
    text = html.unescape(" ".join(parser.parts)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def fetch_page(url: str, attempts: int = 3, timeout: int = 30) -> str:
    last_error: Exception | None = None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.6",
    }
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"bron kon na {attempts} pogingen niet worden opgehaald: {last_error}")


def evaluate_source(source: dict, page_text: str) -> list[str]:
    missing = []
    for check in source.get("checks", []):
        patterns = check.get("patterns") or []
        if not patterns or not any(re.search(pattern, page_text, flags=re.IGNORECASE) for pattern in patterns):
            missing.append(check.get("label") or "naamloze controle")
    return missing


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported pricing-sources.json schema_version")
    if not data.get("sources"):
        raise ValueError("pricing-sources.json contains no sources")
    for source in data["sources"]:
        if not source.get("id") or not source.get("url") or not source.get("checks"):
            raise ValueError(f"Incomplete pricing source entry: {source!r}")
    return data


def build_report(results: list[dict], verified_at: str) -> str:
    failures = [result for result in results if result["status"] != "ok"]
    lines = [
        "# Tariefbroncontrole",
        "",
        f"Referentiedatum in configuratie: {verified_at}",
        "",
        f"Resultaat: **{len(results) - len(failures)}/{len(results)} bronnen akkoord**.",
        "",
    ]
    for result in results:
        marker = "OK" if result["status"] == "ok" else "ACTIE NODIG"
        lines.append(f"## {marker}: {result['provider']} [{result['plan']}]")
        lines.append("")
        lines.append(f"Bron: {result['url']}")
        lines.append("")
        if result["status"] == "ok":
            lines.append("Alle verwachte tariefvoorwaarden zijn teruggevonden.")
        elif result["status"] == "fetch_error":
            lines.append(f"De bron kon niet betrouwbaar worden opgehaald: {result['error']}")
        else:
            lines.append("Niet teruggevonden voorwaarden:")
            for label in result["missing"]:
                lines.append(f"- {label}")
        lines.append("")
    if failures:
        lines.extend([
            "## Gevolg voor de dagelijkse vergelijking",
            "",
            "De bijbehorende statische prijsregel wordt voor die dagelijkse run fail-closed uitgeschakeld. Officiële NDW/CPO-data blijft wel verwerkt worden. Controleer de bron handmatig voordat de aanname of monitor wordt aangepast.",
            "",
        ])
    return "\n".join(lines)


def build_status(results: list[dict], verified_at: str, checked_at: str | None = None) -> dict:
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "checked_at": checked_at,
        "config_verified_at": verified_at,
        "all_ok": all(result["status"] == "ok" for result in results),
        "enabled_rule_ids": [result["id"] for result in results if result["status"] == "ok"],
        "disabled_rule_ids": [result["id"] for result in results if result["status"] != "ok"],
        "results": results,
    }


def run(config: dict, fetcher=fetch_page) -> tuple[list[dict], bool]:
    results = []
    for source in config["sources"]:
        base = {
            "id": source["id"],
            "provider": source["provider"],
            "plan": source["plan"],
            "url": source["url"],
        }
        try:
            page_text = normalize_page(fetcher(source["url"]))
            missing = evaluate_source(source, page_text)
            results.append({**base, "status": "mismatch" if missing else "ok", "missing": missing})
        except Exception as exc:
            results.append({**base, "status": "fetch_error", "error": str(exc), "missing": []})
    return results, all(result["status"] == "ok" for result in results)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--json-status", type=Path, default=None)
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="Write/report failures but return success so the data pipeline can fail closed per rule.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    results, ok = run(config)
    verified_at = config.get("verified_at", "onbekend")
    report = build_report(results, verified_at)
    print(report)
    if args.report:
        args.report.write_text(report + "\n", encoding="utf-8")
    if args.json_status:
        status = build_status(results, verified_at)
        args.json_status.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.allow_failures:
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
