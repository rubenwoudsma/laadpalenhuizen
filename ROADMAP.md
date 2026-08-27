# Roadmap Laadpalen Huizen

De roadmap is geen wensenlijst. Iedere stap moet aantoonbaar bijdragen aan een betrouwbaardere keuze voor iemand die bij een laadpunt staat.

## Uitgangspunten

- Betrouwbaarheid gaat voor maximale dekking.
- Een bekende ontbrekende kostencomponent blokkeert een harde ranking.
- NDW DOT-NL blijft de primaire genormaliseerde bron voor locaties, EVSE's, connectoren en OCPI-tarieven.
- Officiele publieke CPO- en MSP-bronnen mogen aanvullen als ze reproduceerbaar en tijdens de datarun verifieerbaar zijn.
- Iedere numerieke prijs moet herleidbaar zijn naar prijsbasis en bron.
- De statische GitHub Pages architectuur blijft staan zolang een backend geen aantoonbare kwaliteitswinst oplevert.
- `pricing-quality.json` is de meetlat voor de volgorde van nieuwe werkzaamheden.

## P0, Methodology & Decision Quality hardening

Status: gerealiseerd in Quality Model v2 / dataschema 5.

- [x] Prijsberekening verplaatst van representatieve locatieprijs naar connectorprofielen.
- [x] OCPI `power_type` gebruikt voor AC/DC, geen kW-drempel meer.
- [x] Connector `tariff_ids` afzonderlijk verwerkt.
- [x] Vermogen afgeleid uit spanning/stroom/fasen wanneer `max_electric_power` ontbreekt.
- [x] OCPI `FLAT` vaste sessiekosten opgenomen.
- [x] OCPI-tariefgeldigheid en `Tariff.type` expliciet verwerkt, inclusief veilig gebruik van een ontbrekend type.
- [x] Direct/QR-support en ad-hoc prijsinformatie gescheiden, een generiek tarief bewijst geen directe betaalmogelijkheid.
- [x] OCPI-btw semantiek verwerkt zonder een niet-gepubliceerd btw-percentage te verzinnen.
- [x] Valuta fail-closed verwerkt: niet-EUR wordt niet als euro getoond, ontbrekende valuta kan geen harde winnaar dragen.
- [x] Ongeldige of onbekende prijscomponenten kunnen niet stilzwijgend uit een sessietotaal verdwijnen.
- [x] ENERGY `step_size` wordt toegepast op de CPO-energieafrekening; ontbrekende of conflicterende informatie verlaagt/blokkeert de decision grade.
- [x] Operator-mediaan teruggebracht tot diagnostische schatting, niet geschikt voor sessieranking.
- [x] `TIME`, `PARKING_TIME`, `MIN_PRICE`, `MAX_PRICE` en nog niet gemodelleerde `TariffRestrictions` blokkeren ranking.
- [x] Quality Model v2 met `source_quality`, `price_specificity`, `cost_completeness` en `decision_grade`.
- [x] Harde winnaar alleen bij complete, betrouwbare en niet-overlappende geselecteerde prijsroutes.
- [x] Vattenfall roamingstarttarief niet meer als vast bedrag aangenomen.
- [x] ANWB-netwerkkortingen niet zonder exacte bron als korting toegepast.
- [x] E-Flux mogelijke EUR 0,48 clearingtoeslag in de prijsband opgenomen.
- [x] Shell extra/blokkeerkosten maken de statische Shell-route indicatief.
- [x] Dagelijkse publieke broncontrole voor statische MSP-regels, per regel fail-closed.
- [x] Quality report v2 valideert kernstatistieken onafhankelijk van de producer.

### P0.1, productiecalibratie na eerste schema-5 run

Status: gerealiseerd na analyse van de eerste live Quality Model v2 baseline.

- [x] Frontend-contract op `decision_grade` vastgelegd, `exclude` en `partial` mogen nooit in ranking of winnaar terechtkomen.
- [x] `app.js` versiegebonden geladen om oude frontendcode naast nieuwe schema-5 JSON te voorkomen.
- [x] Direct/QR bronprioriteit aangescherpt naar expliciet OCPI ad-hoc, officiele CPO Direct/QR, daarna pas generiek typeloos OCPI.
- [x] Officiele netwerk-specifieke MSP-prijzen erven geen ongerelateerde NDW-tariefrestricties meer.
- [x] Quality blockers primair per connectorprofiel tellen, met prijsroute-occurrences als afzonderlijke impactmaat.
- [x] Laadkompas- en Vattenfall-broncontrole naar beter uitleesbare officiele pagina's verplaatst.

### Acceptatie P0

Na de eerste succesvolle productie-run:

- `huizen-data.json` heeft `schema_version: 5`;
- `pricing-quality.json` heeft `schema_version: 2`;
- `legacy_profile_fallback` is `false`;
- een gemengde AC/DC-locatie toont eerst een connector-keuze;
- routes met bekende niet-gemodelleerde kosten worden niet gerangschikt;
- de GitHub Actions summary toont de nieuwe kwaliteitsdimensies en eventuele geblokkeerde prijsregels.

## P1, Datacorrectheid en lokale dekking

De aantallen hieronder moeten steeds uit de meest recente schema-5 quality run worden gehaald. Oude schema-4 aantallen zijn alleen een historische baseline.

### 1. Mogelijke oude/vervangen CPO-records onderzoeken

**Waarom eerst:** een onjuist of vervangen laadpunt op de kaart is fundamenteler dan een ontbrekende prijs.

**Aanpak:**

- start met `possible_operator_transition` uit `pricing-quality.json`;
- vergelijk EVSE-ID, operator, status, coordinaten en `last_updated`;
- zoek waar nodig aanvullend bewijs in NDW of een officiele CPO-bron;
- onderdruk een record alleen met een reproduceerbare regel;
- laat echte co-located laadpunten van verschillende CPO's zichtbaar.

**Klaar wanneer:** iedere suppressieregel getest en verklaarbaar is, zonder deduplicatie op alleen adres of coordinaten.

### 2. Direct/QR gaten per CPO oplossen

**Historische baseline voor schema 4:** Laadnet 9, Lidl 4, JOLT 1. Herbevestig aantallen na de P0-run.

**Aanpak:**

- prioriteer op aantal getroffen connectorprofielen in Huizen;
- voorkeur: expliciet OCPI `AD_HOC_PAYMENT`, officiele CPO API/feed of stabiele publieke CPO-pagina;
- koppel op `party_id`, EVSE-ID of connector waar mogelijk;
- geen app reverse engineering of omzeilen van login/toegangsbeperkingen;
- iedere adapter krijgt tests en bronmonitoring.

**Klaar wanneer:** dekking stijgt zonder `exclude` of `indicative` kunstmatig als betrouwbaar te classificeren.

### 3. Ontbrekende CPO-basistarieven oplossen

Gebruik de actuele `base_pricing.gaps` uit het quality report. Onderzoek eerst operators met de grootste lokale impact.

**Klaar wanneer:** iedere nieuwe basisprijs connector-, netwerk- of regionaal herleidbaar is, of bewust onbekend blijft.

### 4. Vattenfall Direct/QR numeriek koppelen

QR/direct betalen is bevestigd. Voeg pas een numerieke prijs toe wanneer die publiek en reproduceerbaar aan het laadpunt of relevante tariefgroep kan worden gekoppeld.

### 5. Netwerk-specifieke MSP-prijzen verfijnen

Onderzoek vooral gevallen waar de MSP zelf aangeeft dat netwerkprijzen afwijken van de generieke formule, bijvoorbeeld ANWB-kortingsnetwerken.

**Klaar wanneer:** een netwerkoverride alleen actief is zolang de specifieke bron kan worden geverifieerd.

## P2, Compleet sessiemodel

### 6. OCPI TariffRestrictions echt evalueren

Ondersteun minimaal:

- start/end time;
- start/end date;
- day of week;
- min/max kWh;
- min/max duration;
- min/max current/power waar relevant.

Tot deze stap klaar is blokkeren restricted tariffs de ranking.

### 7. Tijd-, parkeer-, idle- en blokkeerkosten

**Doel:** een sessietotaal kunnen berekenen wanneer naast energie ook tijd of aangesloten blijven geld kost.

Waarschijnlijk is hiervoor extra gebruikersinput nodig, bijvoorbeeld verwachte laadtijd en/of parkeertijd.

**Klaar wanneer:** de UI het totaal uitsplitst in energie, vaste kosten, tijdkosten en eventuele overige gemodelleerde componenten.

### 8. Prijsgrenzen en resterende afrekenregels

Modelleer OCPI `min_price` en `max_price` volgens de specificatie in plaats van ze te blokkeren. ENERGY `step_size` is in P0 gerealiseerd. Wanneer TIME/PARKING later wordt gemodelleerd moet ook de bijbehorende `step_size` uit die componenten worden toegepast.

### 9. Regionale fallbacktarieven automatisch harvesten

Regionale tarieven, bijvoorbeeld MRA-E, niet handmatig in code bijhouden wanneer de officiele bron voldoende stabiel en eenduidig machineleesbaar is.

## P3, Actualiteit en statusdata

### 10. Freshness van beschikbaarheid verbeteren

Prijsdata en beschikbaarheidsstatus hebben verschillende updatebehoeften. Onderzoek of de status vaker kan worden vernieuwd zonder het volledige prijsmodel realtime te maken.

Opties:

- NDW geografische API/PULL;
- frequentere lichte statusrun;
- gescheiden status- en prijsartefacten.

**Klaar wanneer:** de gemeten status-freshness aantoonbaar verbetert en de site altijd duidelijk de ouderdom van de snapshot kan tonen.

### 11. NDW-interface alleen vervangen wanneer dat winst oplevert

De landelijke snapshots blijven prima zolang ze reproduceerbaar, licht genoeg en voldoende actueel zijn voor het gekozen updatepatroon. Geen architectuurwijziging om de architectuurwijziging zelf.

## Later, alleen bij bewezen noodzaak

### Externe prijsaggregators

Eco-Movement of Chargeprice kan later nuttig zijn als benchmark of aanvulling, maar alleen met passende toegang/licentie en zonder API-sleutels in de browser.

### Open Charge Map

Alleen voor secundaire POI-, operator- of ID-verrijking, niet als primaire prijsautoriteit.

### Serverless laag

Alleen toevoegen wanneer credentials, sterk frequentere updates of connector-specifieke runtime calls dat aantoonbaar vereisen.

## Definitie van een stabiel eindproduct

De roadmap hoeft niet te eindigen met 100 procent prijsdekking. De site is stabiel wanneer:

1. iedere getoonde prijs reproduceerbaar naar een bron en prijsbasis is terug te leiden;
2. bekende ontbrekende kosten nooit in een harde winnaar verdwijnen;
3. reliable, indicative en insufficient consequent en begrijpelijk worden onderscheiden;
4. connectorverschillen niet op locatieniveau worden gladgestreken;
5. alle statische prijsregels automatisch op bronwijzigingen worden gecontroleerd;
6. de quality run geen interne inconsistenties bevat;
7. oude of vervangen laadpuntrecords aantoonbaar worden beheerst;
8. status-freshness passend is bij wat de UI aan de gebruiker belooft;
9. regressietests de prijsregels, bronchecks en kwaliteitslogica afdekken;
10. beheer na een normale dagelijkse Action geen handmatige datareparatie vraagt.

Als een CPO of MSP geen voldoende openbare prijsinformatie beschikbaar stelt, mag de stabiele eindtoestand dus ook bewust `onvoldoende informatie` zijn. Dat is betrouwbaarder dan een verzonnen percentage van 100 procent dekking.
