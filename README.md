# Laadpalen Huizen

Een interactieve kaart met publieke laadpunten in de gemeente Huizen.

De site vergelijkt indicatief meerdere laadpassen op basis van openbare NDW-data en toont per laadpunt onder andere locatie, operator, beschikbaarheid, vermogen en geschatte laadkosten.

**Live data van NDW** [Nationaal Dataportaal Wegverkeer], dagelijks bijgewerkt via GitHub Actions.

## Live website

De website draait via GitHub Pages:

```text
https://rubenwoudsma.github.io/laadpalenhuizen/
```

## Hoe werkt het?

```text
NDW open data [gratis, geen API-key]
         ↓  [dagelijks, 06:00 UTC]
GitHub Actions → process.py
         ↓
huizen-data.json [gecommit naar repo]
         ↓
GitHub Pages [publiceert main branch / root]
         ↓
index.html leest huizen-data.json
```

## Wat doet deze repo?

Deze repository bevat een statische website die:

1. NDW open data downloadt.
2. Publieke laadpunten filtert op de gemeentegrens van Huizen.
3. Locaties en tarieven combineert.
4. Een compact `huizen-data.json` bestand genereert.
5. De kaart in `index.html` toont via GitHub Pages.

Er is geen backend, database of API-key nodig.

## Setup

### 1. Clone deze repo

```bash
git clone https://github.com/rubenwoudsma/laadpalenhuizen.git
cd laadpalenhuizen
```

### 2. Handmatig draaien [testen]

```bash
python3 process.py
```

Dit schrijft lokaal:

```text
huizen-data.json
```

Start daarna een lokale webserver:

```bash
python3 -m http.server 8080
```

Open vervolgens:

```text
http://localhost:8080
```

## GitHub Pages

Deze site is bedoeld om direct vanuit de root van de `main` branch te draaien.

Instelling in GitHub:

```text
Settings → Pages
Source: Deploy from a branch
Branch: main
Folder: /root
```

GitHub Pages publiceert daarna automatisch de bestanden uit de repository.

## GitHub Actions [automatische dagelijkse update]

De workflow in `.github/workflows/update.yml` draait elke dag om 06:00 UTC.

De workflow doet het volgende:

1. Downloadt de actuele NDW-bestanden.
2. Draait `process.py`.
3. Genereert een nieuwe `huizen-data.json`.
4. Controleert of de data gewijzigd is.
5. Commit de nieuwe data terug naar de repo als er wijzigingen zijn.

Geen secrets nodig, NDW-data is openbaar beschikbaar.

Let op: de workflow moet schrijfrechten hebben.

Controleer in GitHub:

```text
Settings → Actions → General → Workflow permissions
Read and write permissions
```

## Data bronnen

| Bron | Bestand | Update |
|------|---------|--------|
| NDW locaties [OCPI] | `charging_point_locations_ocpi.json.gz` | Dagelijks |
| NDW tarieven [OCPI] | `charging_point_tariffs_ocpi.json.gz` | Dagelijks |

NDW haalt data op bij laadpaalexploitanten [CPO's]. De open databestanden worden periodiek bijgewerkt.

## Gemeentegrens Huizen

De filtering gebeurt in twee stappen:

1. Een ruime bounding box rond Huizen voor snelle voorselectie.
2. Een precieze polygon-check op basis van `huizen-boundary.geojson`.

Het bestand `huizen-boundary.geojson` bevat de gemeentegrens van Huizen als GeoJSON `Feature` met een `MultiPolygon` geometry.

## Passen vergeleken

| Pas | Maandkosten | Methode |
|-----|-------------|---------|
| Vattenfall | €0 | CPO-tarief of fallback op basis van operator |
| Laadkompas | €4,78/maand | CPO-basistarief of fallback op basis van operator |
| Allego | €0 | Allego-tarief op eigen netwerk, CPO-tarief elders |
| Shell Recharge | €0 | Vast of geschat tarief afhankelijk van operator |
| Chargemap | €0 | CPO-tarief met indicatieve opslag |

Tarieven zijn indicatief. Controleer altijd de app van je laadpas of aanbieder voordat je gaat laden.

## Belangrijke kanttekeningen

Deze site is bedoeld als hulpmiddel, niet als officiële prijsbron.

Mogelijke beperkingen:

- Niet elk laadpunt heeft een volledig NDW-tarief.
- Sommige tarieven worden geschat via fallbackregels in `process.py`.
- Beschikbaarheid kan afwijken van de werkelijke situatie bij de laadpaal.
- Laadpassen kunnen eigen voorwaarden, starttarieven of roamingkosten rekenen.
- Tarieven kunnen wijzigen zonder dat dit direct zichtbaar is in de open data.

## Bestanden

```text
laadpalenhuizen/
├── index.html                  ← website en kaart
├── methodologie.html           ← uitleg over data en tariefberekening
├── huizen-data.json            ← gegenereerde laadpuntdata
├── huizen-boundary.geojson     ← gemeentegrens Huizen
├── process.py                  ← NDW downloader en preprocessor
├── .nojekyll                   ← voorkomt Jekyll-verwerking door GitHub Pages
└── .github/workflows/
      └── update.yml            ← dagelijkse update via GitHub Actions
```

## Gebaseerd op

Dit project is gebaseerd op de open source repository:

```text
https://github.com/jdevalk/laadpalenwijchen.nl
```

Aanpassingen voor deze versie:

- Omgezet van Wijchen naar Huizen.
- Gemeentegrens vervangen door `huizen-boundary.geojson`.
- Outputbestand aangepast van `wijchen-data.json` naar `huizen-data.json`.
- Bounding box aangepast naar de omgeving Huizen.
- Workflow aangepast voor GitHub Pages in plaats van Cloudflare Pages.
- Teksten, methodologie en kaartinstellingen aangepast voor Huizen.

## Licentie

Controleer de licentie van de oorspronkelijke repository en neem die over als deze van toepassing is. 
