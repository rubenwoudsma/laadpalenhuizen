# Repository notes: laadpalenhuizen

## Purpose

Static GitHub Pages site for public EV charge points in the municipality of Huizen, Netherlands. The main user problem is comparing expected session cost across the charge cards a visitor actually carries.

Live site: https://rubenwoudsma.github.io/laadpalenhuizen/

## Architecture

- `index.html`: dependency-light static frontend using Leaflet from CDN. Reads `huizen-data.json`, lets users select charge cards and an energy amount, stores those preferences in `localStorage`, and computes session totals in the browser.
- `process.py`: Python standard-library preprocessor. Downloads NDW OCPI locations/tariffs, filters to Huizen, derives CPO base tariffs and builds per-pass price components.
- `huizen-data.json`: generated static data consumed by the page.
- `huizen-boundary.geojson`: municipality boundary used after bbox prefiltering.
- `methodologie.html`: public explanation of pricing assumptions, confidence and limitations.
- `.github/workflows/update.yml`: daily refresh at 06:37 UTC.
- `.github/workflows/pricing-monitor.yml`: monthly verification of official pricing sources.
- `pricing-sources.json`: monitored commercial assumptions and core-pass selection policy.
- `scripts/check_pricing_sources.py`: source checker used by GitHub Actions; mismatches are written to the Job Summary and optionally synchronized to a review issue when repository Issues are enabled.
- `tests/`: pricing-rule and source-monitor regression tests.

There is intentionally no Cloudflare Pages Function and no `/api/ocm` endpoint. GitHub Pages is static. Availability shown by the frontend is the status snapshot contained in the last generated NDW dataset.

## Pricing principles

1. Prefer a direct NDW/OCPI connector tariff and resolve tariff IDs in country/party scope.
2. If direct pricing is absent, an operator median can be used only with at least five samples and only for operators where a nationwide median is not obviously misleading.
3. A targeted regional fallback is allowed only when an official source publishes a traceable concession tariff or range and the location can be tied to that region. TotalEnergies in Huizen currently uses this rule for MRA-E.
4. Never invent a generic CPO fallback. Unknown is better than false precision.
5. Model card-specific kWh price/markup, percentage transaction fee and per-session fee separately. Propagate CPO price ranges into pass session-cost ranges.
6. With price ranges, declare a winner only if that pass's maximum calculated cost is below every other pass's minimum calculated cost. Overlapping ranges must remain ambiguous.
7. Monthly-subscription plans are outside the core comparison until the UX includes a user-specific monthly charging frequency.
8. If a provider advertises a discount but does not expose a single safe connector-specific rate, show a note instead of inventing a discount.

## Current core passes

Conditions last verified in code on 2026-08-12:

- ANWB, no subscription
- Tap Electric, Light
- Vattenfall InCharge, free charge card
- E-Flux by Road, Flex
- Shell Recharge, Basic
- Laadkompas, no subscription

Each pass object in `process.py` has a `source_url` and `verified_at`. Update both when changing a commercial pricing rule. Keep `pricing-sources.json` in sync with the implemented rule.

The core pass list is intentionally selective. Prefer plans without a monthly subscription, require a publicly reproducible pricing model, require meaningful Dutch/local relevance, and only add a provider when it contributes enough practical or pricing diversity to justify the extra comparison row.

## Development checks

Before committing pricing or frontend changes:

```bash
python3 -m py_compile process.py
python3 -m unittest discover -s tests
node --check /tmp/laadpalenhuizen-index.js   # after extracting the inline script if Node is available
python3 -m http.server 8000
```

Keep the project free of Python runtime dependencies unless there is a strong reason to add one. Avoid generic per-operator fallback tables. Any targeted regional fallback must have an official source URL, verification date, explicit geographic rationale and regression tests.
