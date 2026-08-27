# Wijzigingen v2, ANWB en multi-pass vergelijking

Deze branch-update is bedoeld als vervanging van de oude prijsvergelijking.

## Functioneel

- ANWB Zonder abonnement toegevoegd.
- E-Flux Flex toegevoegd aan de kernvergelijking.
- Vattenfall, Shell Recharge en Laadkompas opnieuw gemodelleerd op actuele publieke voorwaarden.
- Allego en Chargemap uit de kernvergelijking verwijderd, ze werden in de oude implementatie te grof of zonder voldoende lokale meerwaarde gemodelleerd.
- Gebruiker kan eigen laadpassen aanvinken, opgeslagen in `localStorage`.
- Sessiegrootte selecteerbaar: 5, 10, 20 of 30 kWh.
- Vergelijking op totale sessiekosten en effectieve €/kWh.
- Geen groene/gekleurde winnaar als minder dan twee geselecteerde passen berekenbaar zijn.
- Betrouwbaarheid en bronbasis per prijs zichtbaar.
- Locatieknop sorteert laadpunten op afstand.

## Data en prijsmodel

- Generieke hardcoded CPO-fallbacks verwijderd.
- Direct NDW-tarief is voorkeursbron.
- Operator-mediaan alleen vanaf vijf landelijke samples en niet voor uitgesloten regionaal variërende operators.
- Onbekende CPO-prijzen blijven onbekend.
- Sessiekosten en kWh-opslagen zijn aparte prijscomponenten.
- ANWB-netwerkkortingen worden gemeld, maar niet numeriek verzonnen wanneer geen connector-specifieke prijs beschikbaar is.
- De meegeleverde `huizen-data.json` is al geconverteerd naar schema versie 2.

## Hosting en documentatie

- Verouderde Wijchen-bounding-box uit de methodologie gecorrigeerd naar Huizen.
- Cloudflare Pages-verwijzingen verwijderd en GitHub Pages als hosting gedocumenteerd.
- De oude `/api/ocm` live-statusroute uit de frontend verwijderd.
- `functions/api/ocm.js` verwijderd, deze Cloudflare Function wordt op GitHub Pages niet gebruikt.
- Beschikbaarheid wordt expliciet als NDW-snapshot gepresenteerd.
- `README.md`, `CLAUDE.md` en `methodologie.html` bijgewerkt.

## Kwaliteit

- `tests/test_pricing.py` toegevoegd met regressietests voor de kernprijsregels.
- GitHub Action voert de tests uit voordat nieuwe NDW-data wordt gegenereerd.

## Let op bij mergen

Omdat `functions/api/ocm.js` is verwijderd, gebruik bij een lokale Git-branch bij voorkeur `git add -A` zodat ook de verwijdering wordt meegenomen in de commit.


## MRA-E en TotalEnergies

- OCPI-tarieven worden gekoppeld op land, partij en tarief-ID in plaats van alleen tarief-ID.
- TotalEnergies is uitgesloten van de landelijke operator-mediaan omdat MRA-E-tarieven per concessie verschillen.
- Voor TotalEnergies in Huizen wordt bij ontbrekende NDW-prijs een officiële MRA-E-prijsband gebruikt: AC EUR 0,34-0,48/kWh, DC EUR 0,54/kWh.
- Prijsbanden worden doorgerekend naar sessiekosten per laadpas.
- Een winnaar wordt alleen aangewezen wanneer prijsbanden niet overlappen.
- De dagelijkse processor rapporteert diagnostiek over TotalEnergies tariff_ids en resolvable ENERGY-tarieven.


## Tap Electric en tariefmonitor

- Tap Electric Light toegevoegd aan de kernvergelijking: geen maandabonnement, CPO-tarief plus 5% transactiekosten per sessie.
- Procentuele transactiekosten zijn als aparte prijscomponent aan het frontendmodel toegevoegd.
- `pricing-sources.json` legt de gecontroleerde bronvoorwaarden en selectiecriteria voor kernpassen vast.
- `scripts/check_pricing_sources.py` controleert officiële tariefpagina's zonder tarieven automatisch te wijzigen.
- `.github/workflows/pricing-monitor.yml` voert deze controle maandelijks uit en publiceert het rapport in de Job Summary. Als GitHub Issues is ingeschakeld, opent of actualiseert de workflow ook een review-Issue.
- De kernselectie blijft bewust beperkt tot passen met voldoende lokale/Nederlandse relevantie, reproduceerbare voorwaarden en duidelijke meerwaarde.
