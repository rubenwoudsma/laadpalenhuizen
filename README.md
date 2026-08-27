# Laadpalen Huizen

Een statische webapp voor openbare laadpunten in de gemeente Huizen. Het doel is niet alleen te tonen waar een laadpunt staat, maar vooral welke betaalroute voor een concrete laadsessie naar verwachting het goedkoopst is.

**Live website:** https://rubenwoudsma.github.io/laadpalenhuizen/

## Wat de kaart vergelijkt

De kaart vergelijkt drie verschillende prijsroutes als aparte opties:

1. **Direct / QR [ad-hoc]**, rechtstreeks betalen bij de CPO zonder laadpas of MSP-contract.
2. **MSP op eigen netwerk**, wanneer de gekozen laadpasaanbieder aantoonbaar op het eigen CPO-netwerk wordt gebruikt.
3. **MSP roaming**, wanneer de laadpas via een andere CPO loopt en roamingvoorwaarden of transactiekosten gelden.

Een gedeeld merk of dezelfde bedrijvengroep is niet voldoende om twee routes als hetzelfde tarief te behandelen. De code gebruikt waar mogelijk de OCPI `party_id` voor CPO-identificatie. Daardoor wordt bijvoorbeeld Ubitricity niet automatisch als een Shell Recharge eigen-netwerktarief behandeld.

## Sessiekosten in plaats van alleen €/kWh

De rangschikking wordt berekend voor de hoeveelheid energie die de gebruiker zelf kiest:

```text
Total Session Cost = (Session kWh × kWh Rate × Percentage Multiplier) + Start Fee
```

De browser ondersteunt 5 tot 60 kWh via een slider. Er zijn daarnaast snelle presets, waaronder:

```text
Hyundai Inster 20% -> 80% ≈ 25 kWh
```

Als een tarief een prijsband heeft, wordt die volledige band doorgerekend naar de sessiekosten. De Top 3 op basis van de geschatte middelkosten wordt groen gemarkeerd. Een optie krijgt alleen een harde aanduiding als goedkoopste wanneer zijn hoogste berekende prijs nog lager is dan de laagste berekende prijs van alle andere opties. Bij overlappende prijsbanden blijft de kaart expliciet onzeker.

## Ondersteunde betaalopties

De kernvergelijking bevat momenteel:

- Direct / QR, zonder laadpas, wanneer een directe betaalroute is bevestigd;
- ANWB, Zonder abonnement;
- Tap Electric, Light;
- Vattenfall InCharge, Gratis laadpas;
- E-Flux by Road, Flex;
- Shell Recharge, Basic;
- Laadkompas, Zonder abonnement.

De selectie is bewust beperkt. Een MSP wordt alleen opgenomen wanneer de prijslogica publiek genoeg is om reproduceerbaar te modelleren en de optie voldoende lokale of Nederlandse relevantie heeft.

## Direct betalen en OCPI `AD_HOC_PAYMENT`

OCPI ondersteunt een specifiek tarieftype `AD_HOC_PAYMENT`. `process.py` houdt zo'n tarief apart van reguliere CPO/MSP-tarieven en verwerkt daarnaast een eventuele `FLAT` component als vaste sessiekosten.

Wanneer NDW wel bevestigt dat direct betalen mogelijk is, maar geen apart berekenbaar ad-hoc tarief bevat, toont de webapp **Direct / QR** als beschikbare of mogelijke route zonder een prijs te verzinnen. De optie wordt dan niet in de Top 3 gerangschikt. De gebruiker moet de live prijs na het scannen van de QR-code controleren.

## CPO versus MSP

Voor bekende eigen-netwerkrelaties wordt eerst de OCPI CPO `party_id` gebruikt. De relevante mappings staan in `process.py`.

Voorbeelden:

```text
Vattenfall MSP + CPO party NUO  -> own network
E-Flux MSP + CPO party EFL      -> own network
Shell MSP + CPO party TNM       -> own network
Shell MSP + CPO party UB2       -> roaming, not automatically own network
```

Als `party_id` ontbreekt, gebruikt de code alleen een conservatieve operatornaam-fallback. Een onbekende relatie wordt niet als eigen netwerk aangenomen.

## Databron en prijsarchitectuur

De primaire bron is **NDW DOT-NL**, de Nederlandse publieke toegang tot actuele data over publiek toegankelijke laadpunten. De huidige GitHub Action downloadt de landelijke OCPI snapshots:

```text
https://opendata.ndw.nu/charging_point_locations_ocpi.json.gz
https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz
```

De preprocessor bewaart onder andere:

- locatie en gemeentegrens;
- CPO-naam en OCPI `party_id`;
- EVSE-ID's, bijvoorbeeld `NL*UB2*...`;
- connectoren, vermogen en status;
- reguliere OCPI ENERGY-tarieven;
- expliciete `AD_HOC_PAYMENT` tarieven;
- vaste `FLAT` bedragen waar beschikbaar;
- de herkomst en betrouwbaarheid van de prijs;
- de berekende MSP-route [eigen netwerk of roaming].

### Huidige datastroom

```text
NDW DOT-NL OCPI snapshots
        |
        v
GitHub Actions
        |
        v
process.py
        |
        v
huizen-data.json
        |
        v
index.html + app.js
        |
        v
GitHub Pages
```

De applicatie heeft daarom geen runtime backend, database of betaalde kaart-API nodig.

## API-roadmap

### Fase 1, huidige statische architectuur verder aanscherpen

Blijf NDW DOT-NL als primaire bron gebruiken en bouw op stabiele identifiers, vooral `country_code`, `party_id`, locatie-ID, EVSE-ID en `tariff_ids`. Bewaar reguliere en ad-hoc tarieven strikt gescheiden en sla per prijs ook een bron, update-tijd en betrouwbaarheid op.

### Fase 2, NDW API / GeoJSON en frequentere updates

NDW documenteert naast volledige downloads ook GeoJSON/OCPI API-ontwikkeling en een ontwikkeling richting OCPI PULL/PUSH. Zodra die interfaces stabiel genoeg zijn, kan de landelijke download worden vervangen of aangevuld met een geografische Huizen-query en frequentere tarief/status-updates.

Voor deze repository is een **server-side snapshot via GitHub Actions** voorlopig robuuster dan rechtstreeks vanuit de browser naar externe laad-API's bellen. Dit voorkomt CORS-problemen, beschermt eventuele API-credentials en houdt GitHub Pages volledig statisch.

### Fase 3, directe CPO-prijzen als aparte bronlaag

Voor ad-hoc tarieven zijn CPO-bronnen uiteindelijk belangrijker dan een afgeleid MSP-tarief. Voeg per CPO een adapter toe wanneer een stabiele publieke feed of endpoint beschikbaar is. De bronprioriteit kan dan worden:

```text
1. Explicit live/official CPO ad-hoc tariff
2. NDW OCPI AD_HOC_PAYMENT tariff
3. Official regional/concession tariff
4. No numeric direct-payment estimate
```

Een QR-route zonder betrouwbare prijs blijft zichtbaar, maar wordt niet als goedkoopste gerangschikt.

### Fase 4, Open Charge Map als secundaire verrijking

Open Charge Map kan nuttig zijn voor POI-verrijking, alternatieve operatornamen en ID-reconciliatie. Gebruik het niet als primaire tariefautoriteit wanneer NDW of de CPO zelf een actuelere Nederlandse prijsbron heeft.

### Fase 5, werkelijk realtime vergelijken

Als later prijzen direct bij het openen van de kaart moeten worden opgehaald, voeg dan een kleine serverless/edge API met caching toe. Die laag kan NDW en CPO-adapters combineren en aan de browser één genormaliseerd prijsobject leveren. De huidige `huizen-data.json` structuur is bedoeld als tussenstap naar zo'n model.

## TotalEnergies en regionale fallback

Voor TotalEnergies-locaties in Huizen gebruikt de kaart, wanneer NDW geen bruikbaar direct regulier tarief levert, de officiële MRA-E prijsinformatie voor Noord-Holland, Flevoland en Utrecht. Voor reguliere AC-laders blijft dit bewust een prijsband wanneer niet betrouwbaar is vast te stellen welke concessie of dynamische prijs bij de connector hoort.

Er wordt geen generieke fallbackprijs ingevuld om toch een winnaar te kunnen tonen.

## Kaartlaag

De kaart gebruikt Leaflet met de standaard OpenStreetMap rasterlaag:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

Daarvoor is geen CartoDB API-key nodig. De OpenStreetMap-attributie blijft zichtbaar. Voor een kleine gemeentelijke toepassing is dit praktisch, maar bij sterk groeiend verkeer hoort een eigen tile-provider of gehoste tileservice bij de schaalstrategie.

## Automatische updates

`.github/workflows/update.yml` draait:

- dagelijks om 06:37 UTC;
- handmatig via `workflow_dispatch`;
- automatisch na een wijziging aan `process.py`, `tests/test_pricing.py` of de update-workflow zelf.

De workflow voert eerst de tests uit, haalt vervolgens actuele NDW-data op en schrijft alleen een nieuwe `huizen-data.json` terug wanneer de inhoud is veranderd. Een commit die alleen het gegenereerde JSON-bestand wijzigt start de workflow niet opnieuw.

`.github/workflows/pricing-monitor.yml` controleert maandelijks de publieke tariefpagina's uit `pricing-sources.json`. De monitor signaleert mogelijke wijzigingen, maar past tarieven nooit automatisch aan.

## Lokaal draaien

```bash
git clone https://github.com/rubenwoudsma/laadpalenhuizen.git
cd laadpalenhuizen
python3 -m http.server 8000
```

Open daarna:

```text
http://localhost:8000/
```

Data opnieuw genereren:

```bash
python3 process.py
```

Tests draaien:

```bash
python3 -m unittest discover -s tests
```

JavaScript syntax controleren wanneer Node.js beschikbaar is:

```bash
node --check app.js
```

## Projectstructuur

```text
.github/workflows/update.yml           NDW-data update en regeneratie
.github/workflows/pricing-monitor.yml  Maandelijkse controle van tariefbronnen
index.html                             Paginastructuur en styling
app.js                                 Kaart, filters, sessiecalculator en ranking
methodologie.html                      Uitleg over model, bronnen en beperkingen
process.py                             NDW/OCPI-preprocessor en prijsregels
pricing-sources.json                   Bronvoorwaarden voor statische MSP-regels
scripts/check_pricing_sources.py       Controle van officiële tariefpagina's
huizen-data.json                       Gegenereerde laadpuntdata
huizen-boundary.geojson                Gemeentegrens Huizen
tests/                                 Regressietests voor prijsmodel en bronmonitor
```

## Beperkingen

- MSP roamingtarieven kunnen afwijken van een CPO-basistarief en soms alleen in de MSP-app zichtbaar zijn.
- Tijd-, parkeer-, idle- en blokkeerkosten worden gesignaleerd maar nog niet volledig naar een sessietotaal omgerekend.
- De beschikbaarheidsstatus in deze statische versie is een snapshot, niet continu realtime.
- Een directe QR-optie zonder expliciet tarief wordt bewust niet numeriek geschat.
- De uiteindelijke prijs op de betaalpagina, in de MSP-app of op de factuur blijft leidend.

Meer details staan in [Methodologie](methodologie.html).

## Herkomst

Dit project is ontstaan vanuit de open source repository `jdevalk/laadpalenwijchen.nl` en is aangepast voor Huizen en voor een expliciete CPO/MSP/ad-hoc prijsvergelijking.
