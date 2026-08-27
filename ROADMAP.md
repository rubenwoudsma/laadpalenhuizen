# Roadmap Laadpalen Huizen

Deze roadmap vertaalt de beperkingen uit de methodologie naar concrete ontwikkelstappen. De methodologie beschrijft hoe de applicatie vandaag rekent en welke bronnen vandaag worden gebruikt. Dit bestand beschrijft wat daarna wordt verbeterd, waarom dat nodig is en wanneer een stap klaar is.

## Uitgangspunten

- Betrouwbaarheid gaat voor maximale dekking. Een onbekende prijs blijft onbekend als er geen controleerbare bron is.
- NDW DOT-NL blijft de primaire, genormaliseerde bron voor locaties, CPO-identiteit, EVSE-ID's en OCPI-tarieven.
- Officiele CPO-bronnen mogen NDW aanvullen voor directe/ad-hoc tarieven als de publieke bron tijdens de datarun opnieuw kan worden geverifieerd.
- MSP-prijzen worden alleen automatisch gebruikt als de publieke tariefregel reproduceerbaar is.
- De applicatie blijft statisch op GitHub Pages zolang een runtime backend geen aantoonbare functionele winst oplevert.
- Iedere numerieke prijs moet herleidbaar zijn naar bron, controle-moment en prijsbasis.

## Nu gerealiseerd

### Rustige kaart zonder API-key

De standaardkaart gebruikt OpenFreeMap Positron via MapLibre GL en Leaflet. Als deze vectorlaag niet kan initialiseren, valt de site terug op de standaard OpenStreetMap rasterlaag.

**Klaar wanneer:**
- de kaart zonder account of API-key laadt;
- de kaart visueel rustig genoeg is voor de laadpuntmarkers;
- een storing van de vectorlaag de kaart niet volledig onbruikbaar maakt.

### Direct / QR als aparte prijsroute

Direct betalen is geen laadpas en wordt daarom als afzonderlijke route gemodelleerd. Een expliciet OCPI `AD_HOC_PAYMENT` tarief blijft de eerste bron.

### Aanvullende officiele CPO-harvesting

De dagelijkse preprocessing kan nu publieke operatorpagina's valideren en gebruiken als aanvullende ad-hoc bron wanneer NDW geen expliciet ad-hoc tarief levert.

Momenteel ondersteund:

| CPO | OCPI party | Huidige aanvulling |
| --- | --- | --- |
| Ubitricity | `UB2` | Officieel MRA-E ad-hoc QR-tarief wordt uit de publieke tariefpagina gelezen. Dezelfde pagina levert ook netwerk-specifieke kWh-prijzen voor ANWB, Tap Electric, Shell Recharge en Vattenfall. |
| TotalEnergies | `GFX` | Officiele regel wordt geverifieerd dat de CPO-basisprijs ook de ad-hoc/direct-payment prijs is. De bestaande locatieprijs of prijsband kan daardoor voor Direct / QR worden gebruikt. |
| Vattenfall | `NUO` | Direct betalen via QR is publiek bevestigd, maar er wordt nog geen numeriek bedrag afgeleid zolang geen betrouwbare connector-specifieke publieke prijsroute is gevonden. |

Een fout of wijziging op een operatorwebsite blokkeert de NDW-update niet. De aanvullende prijs wordt dan niet toegepast en de bronstatus wordt in de dataset vastgelegd.

## P1, dekking van echte ad-hoc tarieven vergroten

### 1. Vattenfall direct-payment prijs koppelen

**Doel:** een numerieke Direct / QR prijs tonen voor Vattenfall-palen wanneer die prijs publiek en reproduceerbaar aan het laadpunt kan worden gekoppeld.

**Aanpak:**
- onderzoeken of de QR-betaalflow een openbaar endpoint, een stabiele laadpunt-URL of een publiek tariefobject gebruikt;
- koppelen op EVSE-ID of een andere duurzame laadpuntidentifier;
- alleen publieke, legaal toegankelijke data gebruiken, geen login, app reverse engineering of omzeilen van toegangsbeperkingen;
- vergelijken met de reguliere Vattenfall CPO-prijs om te voorkomen dat twee verschillende producten onterecht gelijk worden gesteld.

**Acceptatiecriteria:**
- minimaal vijf Vattenfall-locaties in Huizen kunnen automatisch aan een direct-payment tarief worden gekoppeld;
- bron-URL, controle-moment en tariefbasis staan in `huizen-data.json`;
- een parserwijziging heeft unit tests met zowel geldige als gewijzigde/ongeldige broninhoud;
- bij bronfouten vervalt alleen de aanvullende prijs, niet de gehele datarun.

### 2. Overige CPO's in Huizen prioriteren

**Doel:** de resterende Direct / QR gaten aanpakken op basis van lokaal bereik, niet op basis van een lange landelijke lijst.

**Aanpak:**
- laat de dagelijkse dataset eerst rapporteren hoeveel laadpunten per `party_id` geen berekenbaar direct tarief hebben;
- onderzoek CPO's in aflopende volgorde van het aantal getroffen Huizen-locaties;
- accepteer als prijsbron bij voorkeur OCPI, een officiele CPO API/feed of een stabiele publieke operatorpagina;
- gebruik aggregators alleen als secundaire controle, niet als primaire prijsautoriteit wanneer de CPO zelf een bron publiceert.

**Acceptatiecriteria:**
- iedere nieuwe adapter heeft bronmonitoring en tests;
- geen hardcoded tarief zonder publiek bewijs en controledatum;
- de dekking stijgt aantoonbaar zonder de hoeveelheid `low` confidence prijzen kunstmatig te vergroten.

### 3. Pricing quality report [gerealiseerd]

**Doel:** na iedere datarun direct zien of de prijsdekking beter of slechter wordt.

**Huidige output:**
- `pricing-quality.json` als machineleesbaar rapport;
- `kwaliteit.html` als menselijk leesbaar dashboard;
- dezelfde kern-KPI's in de GitHub Actions summary;
- vergelijking-ready en CPO-basistarief-dekking;
- Direct / QR bekend, geprijsd en ongeprijsd per `party_id`;
- `high`, `medium` en `low` confidence over alle prijsregels;
- prijsbron- en quote-basisverdeling;
- officiele bronnen die tijdens de run niet konden worden geverifieerd;
- statusdata ouder dan 24 uur, 7 dagen en 30 dagen;
- potentiële dubbele adressen en een apart signaal voor mogelijke CPO-wissels/superseded records.

**Gerealiseerd:** de dagelijkse update-workflow genereert het rapport na `huizen-data.json` en commit beide bestanden samen. Er is bewust geen samengestelde quality score; de transparante KPI's vormen de meetlat.

**Vervolg:** gebruik de actuele aandachtspunten uit het rapport om de eerstvolgende harvesting- en data-cleanup issues te prioriteren.


### 3a. Mogelijke CPO-wissels en dubbele records onderzoeken

**Doel:** voorkomen dat een gebruiker meerdere markers voor feitelijk dezelfde of inmiddels vervangen locatie ziet.

**Aanpak:**
- start met de groepen die `pricing-quality.json` als `possible_operator_transition` markeert;
- vergelijk EVSE-ID's, operator, status en `last_updated`;
- verwijder of onderdruk een oud record alleen wanneer daar reproduceerbaar bewijs voor is;
- houd echte co-located laadpunten van verschillende exploitanten gewoon zichtbaar.

**Acceptatiecriteria:** geen automatische deduplicatie op alleen adres of coordinaten; iedere suppressieregel is getest en verklaarbaar.

## Kandidaten voor aanvullende databronnen

Niet iedere interessante bron hoort direct in de harvestingcode. De keuze hangt af van openbaarheid, licentie, koppelbaarheid op EVSE/connector en reproduceerbaarheid.

| Bron | Waarde voor dit project | Besluit nu |
| --- | --- | --- |
| NDW DOT-NL | Publieke Nederlandse OCPI-locaties en tarieven | Primair blijven gebruiken. |
| Officiele CPO-pagina's/API's | Directe/ad-hoc prijzen van de bronhouder | Actief uitbreiden via kleine, geteste adapters. |
| Eco-Movement Prices API | Connector-level MSP-prijzen en CPO ad-hoc prijzen uit meerdere bronnen | Zeer interessant als toekomstige vergelijking/aanvulling, maar vereist authenticatie en prijsdata is licentieafhankelijk. Eerst toegang en gebruiksvoorwaarden beoordelen. |
| Chargeprice API | Uitgebreide sessie- en MSP-prijsvergelijking | Niet automatisch integreren, API-key/commerciele licentie vereist. Wel bruikbaar als benchmark als passende toegang beschikbaar komt. |
| Open Charge Map | POI-, operator- en ID-verrijking | Alleen secundair gebruiken, niet als primaire tariefautoriteit. |

Voor betaalde of geauthenticeerde bronnen geldt dat API-tokens nooit in `index.html` of `app.js` mogen staan. Als zo'n bron later aantoonbaar waarde toevoegt, hoort de call in GitHub Actions of achter een kleine server-side cache.

## P2, nauwkeuriger sessiemodel

### 4. Tijd-, parkeer- en idle-kosten

**Doel:** sessies vergelijken waarvoor naast energie ook `TIME`, `PARKING_TIME` of andere tijdsafhankelijke componenten gelden.

**Aanpak:**
- apart invoerveld of voertuigprofiel voor verwachte laadtijd toevoegen;
- OCPI-restricties zoals starttijd, minimumduur en step size correct verwerken;
- parkeren en laden in de UI als verschillende kostencomponenten tonen.

**Acceptatiecriteria:** de gebruiker kan zien welk deel van het totaal uit energie, vaste kosten en tijdkosten bestaat.

### 5. Regionale tarieven automatisch harvesten

**Doel:** regionale fallbackwaarden, bijvoorbeeld MRA-E, niet langer handmatig in code hoeven bijwerken.

**Aanpak:**
- officiele tariefpagina's op dezelfde manier valideren als de nieuwe ad-hoc bronnen;
- alleen een nieuwe waarde toepassen wanneer regio, eenheid en inclusief/exclusief btw eenduidig kunnen worden gelezen;
- wijzigingen zichtbaar maken in de pricing quality summary.

## P3, data-interface verbeteren wanneer dat aantoonbaar nodig is

### 6. NDW geografische API / OCPI PULL

De volledige landelijke snapshot is voor circa enkele honderden Huizen-locaties nog eenvoudig, reproduceerbaar en goedkoop. Overstappen heeft pas prioriteit wanneer een stabiele NDW-interface aantoonbaar een van deze problemen oplost:
- updates moeten veel frequenter dan de huidige snapshot;
- de landelijke download wordt onnodig zwaar;
- status- of tariefversheid wordt een merkbaar gebruikersprobleem.

**Acceptatiecriteria:** de nieuwe interface levert dezelfde of betere prijsdekking en identifiers op, zonder extra client-side afhankelijkheden of credentials in GitHub Pages.

### 7. Open Charge Map als metadata-verrijking

Open Charge Map kan worden onderzocht voor operatoraliasing, POI-controle en ID-reconciliatie. Het wordt niet de primaire Nederlandse prijsbron zolang NDW of de CPO zelf actuelere en beter herleidbare tarieven levert.

## Later, alleen bij bewezen noodzaak

### Serverless prijsaggregator

Een kleine serverless API met caching wordt pas toegevoegd wanneer dagelijkse/preprocessed snapshots onvoldoende blijken. Mogelijke redenen zijn sterk frequentere prijsupdates, meerdere CPO-endpoints die credentials vereisen of connector-specifieke calls die niet veilig vanuit de browser kunnen worden gedaan.

Tot die tijd heeft de huidige GitHub Actions plus GitHub Pages architectuur de voorkeur vanwege eenvoud, transparantie en reproduceerbaarheid.

## Werkwijze voor nieuwe prijsbronnen

Voor iedere nieuwe bron volgen we dezelfde beslisroute:

1. Kan NDW een expliciet connector- of EVSE-gebonden tarief leveren? Gebruik dat eerst.
2. Zo niet, publiceert de CPO zelf een directe prijs of reproduceerbare prijsregel? Harvest en verifieer die server-side tijdens de datarun.
3. Kan de prijs niet betrouwbaar aan Huizen, de CPO of het laadpunt worden gekoppeld? Toon geen bedrag.
4. Voeg bron-URL, controle-moment, `basis` en `confidence` toe aan de quote.
5. Voeg parser- en regressietests toe voordat de bron meetelt in de ranking.

Zo blijft de Top 3 een vergelijking van aantoonbare prijzen, niet van aannames.
