# Laadpalen Huizen

Een statische webapp voor openbare laadpunten in de gemeente Huizen. Het doel is niet alleen tonen waar een laadpunt staat, maar vooral een zo betrouwbaar mogelijke vergelijking geven van de totale kosten van een concrete laadsessie.

Live website: https://rubenwoudsma.github.io/laadpalenhuizen/

## Kernprincipe

Betrouwbaarheid gaat voor dekking. De site geeft daarom drie soorten uitkomsten:

1. **Betrouwbare vergelijking**: voldoende complete en specifieke prijsinformatie om een harde rangschikking te ondersteunen.
2. **Indicatieve vergelijking**: een bedrag of prijsband is bruikbaar als schatting, maar niet specifiek of volledig genoeg voor een harde winnaar.
3. **Onvoldoende informatie**: bekende kosten of voorwaarden ontbreken, daarom wordt de route niet gerangschikt.

Een onbekende prijs blijft onbekend. De applicatie vult geen kosten in alleen om een complete Top 3 te kunnen tonen.

## Connectorniveau, niet locatieniveau

Vanaf dataschema 5 wordt de prijsberekening per connectorprofiel uitgevoerd. Dat is belangrijk omdat binnen een locatie AC, DC, vermogen, beschikbaarheid en gekoppelde OCPI-tarieven kunnen verschillen.

De processor gebruikt bij voorkeur rechtstreeks uit OCPI:

- `Connector.power_type` voor AC/DC;
- `Connector.tariff_ids` voor de bij die connector geldige tarieven;
- `Connector.max_electric_power`, of anders spanning x stroom x aantal fasen voor het vermogen;
- EVSE- en connectorstatus voor de actuele snapshot.

Laadvermogen wordt nooit meer gebruikt om AC of DC te raden. Als `power_type` ontbreekt, wordt alleen een beperkte standaard-inferentie gebruikt wanneer het connectortype de stroomsoort ondubbelzinnig maakt. Anders blijft de stroomsoort onbekend.

Wanneer een locatie meerdere prijsverschillende connectorprofielen heeft, vraagt de browser eerst welke aansluiting wordt gebruikt. Er wordt dan niet stilzwijgend een representatieve locatieprijs gekozen.

## Sessiekosten

De basisberekening is:

```text
CPO-afrekenenergie = gekozen kWh afgerond op ENERGY step_size
CPO-energie = CPO-afrekenenergie x CPO-kWh-tarief
MSP-kWh-opslag = gekozen kWh x eventuele MSP-opslag per kWh
sessietotaal = CPO-energie + vaste CPO-kosten + MSP-kosten/opslag
```

De browser ondersteunt 5 tot 60 kWh en rekent prijsbanden volledig door. Een harde winnaar wordt alleen getoond wanneer:

- minimaal twee geselecteerde betaalroutes berekenbaar zijn;
- alle geselecteerde routes volledig gemodelleerd en `reliable` zijn;
- de maximale prijs van de winnaar lager is dan de minimale prijs van iedere andere route.

Anders toont de site alleen een laagste indicatie.

## OCPI-kostencomponenten

De processor ondersteunt nu:

- `ENERGY`: prijs per kWh, inclusief de OCPI `step_size` voor afrekening in Wh-blokken;
- `FLAT`: vaste CPO-kosten per sessie;
- OCPI-btw semantiek: `price` is exclusief btw en een expliciete `vat` wordt toegevoegd. Als `vat` ontbreekt schrijft OCPI voor dat geen btw van toepassing is, er wordt dus geen Nederlands percentage verzonnen;
- valuta: alleen expliciete EUR-tarieven kunnen volledig betrouwbaar zijn. Een expliciet niet-EUR tarief wordt niet als euro getoond. Ontbreekt `currency`, dan blijft een numerieke Nederlandse feedwaarde hooguit indicatief.

Ontbreekt een verplichte of eenduidige ENERGY-`step_size`, dan blijft een berekening hooguit indicatief. Een ongeldige prijscomponent, ongeldige expliciete btw-waarde, onbekende prijsdimensie, dubbele prijsdimensie in hetzelfde element of conflicterende afrekenstappen blokkeren de ranking. Een geldig FLAT-only tarief, inclusief OCPI's `FLAT = 0` voor gratis laden, blijft wel volledig berekenbaar. Bij een unrestricted tarief wordt per prijsdimensie het eerste TariffElement gebruikt, conform de OCPI-volgorderegel. Bekende componenten die nog niet veilig in een sessietotaal kunnen worden verwerkt blokkeren eveneens de ranking van die route:

- `TIME`;
- `PARKING_TIME`;
- `MIN_PRICE`;
- `MAX_PRICE`;
- OCPI `TariffRestrictions`.

Dit is bewust fail-closed. Een zichtbaar bedrag kan nog steeds als informatie worden getoond, maar wordt niet gebruikt om een winnaar aan te wijzen.

## Betaalroutes

De kernvergelijking bevat:

- Direct / QR, indien een ad-hoc betaalroute en berekenbare prijs zijn bevestigd;
- ANWB zonder abonnement;
- Tap Electric Light;
- Vattenfall InCharge gratis laadpas;
- E-Flux by Road Flex;
- Shell Recharge Basic;
- Laadkompas zonder abonnement.

### Direct / QR

De bronprioriteit voor Direct / QR is bewust strikt: (1) expliciet connectorgebonden OCPI `AD_HOC_PAYMENT`, (2) een tijdens dezelfde run geverifieerd officieel CPO Direct/QR-tarief, (3) pas daarna een generiek OCPI-tarief zonder `Tariff.type`. Zo'n generiek tarief mag alleen worden hergebruikt wanneer direct betalen onafhankelijk via OCPI-capabilities of operatorinformatie is bevestigd. Een tarief zonder type bewijst dus nooit op zichzelf dat Direct / QR beschikbaar is. Deze volgorde voorkomt dat een generiek OCPI-tarief een expliciete publieke Direct/QR-prijs maskeert.

Huidige aanvullende logica:

- Ubitricity `UB2`: officieel MRA-E Direct/QR tarief en netwerk-specifieke MSP-kWh-prijzen;
- TotalEnergies `GFX`: geverifieerde regel dat de CPO-basisprijs ook de direct-payment prijs is;
- Vattenfall `NUO`: QR/direct betalen is bevestigd, maar zonder reproduceerbare numerieke laadpuntprijs wordt geen prijs verzonnen.

## MSP-regels en onzekerheid

Een publieke MSP-regel is alleen actief wanneer de bijbehorende bron tijdens de dagelijkse run nog herkenbaar is. Bij een mislukte broncontrole wordt alleen die prijsregel uitgeschakeld, de NDW-update en andere prijsroutes blijven werken.

Belangrijke huidige regels:

| MSP | Huidige modellering |
| --- | --- |
| ANWB zonder abonnement | CPO-prijs + EUR 0,89 per sessie. Bekende netwerkkortingen bij onder andere TotalEnergies, Ubitricity en Equans worden zonder exact netwerktarief niet verzonnen, de route wordt dan indicatief. |
| Tap Electric Light | Gemodelleerd CPO-subtotaal + 5 procent transactiekosten. |
| Vattenfall InCharge | Eigen netwerk op `NUO` zonder extra MSP-starttarief. Generieke roaming wordt niet geprijsd zolang het gepubliceerde starttarief geen reproduceerbaar numeriek bedrag heeft. |
| E-Flux Flex | EUR 0,31 per sessie, buiten E-Flux + EUR 0,024/kWh. De mogelijke extra EUR 0,48 clearingtoeslag wordt als sessieband meegenomen. |
| Shell Recharge Basic | Gepubliceerd Shell snellaadtarief op eigen netwerk, of de gepubliceerde AC/DC roamingband bij andere aanbieders, + EUR 0,35 per sessie. Voor eigen-netwerk AC wordt geen roamingband hergebruikt. Omdat Shell extra/blokkeerkosten per laadpunt of aanbieder noemt, blijft een statische Shell-route indicatief. |
| Laadkompas zonder abonnement | CPO-prijs + EUR 0,47 per sessie. |

Eigen netwerk wordt primair via OCPI `party_id` bepaald. Ubitricity `UB2` wordt bijvoorbeeld niet automatisch als Shell `TNM` behandeld, ook al zijn de bedrijven commercieel aan elkaar gerelateerd.

### P0.1 correctielaag

Na de eerste schema-5 productierun zijn drie calibratieproblemen gecorrigeerd. De frontend weigert nu aantoonbaar `exclude`/`partial` routes te rangschikken en wordt met een versietag geladen zodat oude JavaScript niet naast nieuwe JSON kan blijven hangen. Officiele CPO-routeprijzen erven bovendien geen restrictions van een onafhankelijke NDW-basisprijs. In het kwaliteitsrapport worden blockers primair per connectorprofiel geteld, met quote-occurrences alleen als tweede impactmaat.

## Quality Model v2

Iedere prijsquote heeft vier afzonderlijke kwaliteitsdimensies:

- `source_quality`: hoe betrouwbaar is de bron zelf, `high`, `medium` of `low`;
- `price_specificity`: hoe specifiek is de prijs, `connector`, `network`, `regional`, `national`, `operator_estimate` of `unknown`;
- `cost_completeness`: zijn alle bekende kosten gemodelleerd, `complete` of `partial`;
- `decision_grade`: mag deze quote een harde keuze ondersteunen, `reliable`, `indicative` of `exclude`.

Hierdoor wordt een officiele regionale prijsband niet meer simpelweg als `low confidence` behandeld. De bron kan uitstekend zijn terwijl de locatiespecificiteit beperkt is. Dat onderscheid is zichtbaar in `pricing-quality.json` en op `kwaliteit.html`.

De quality-pagina maakt daarnaast expliciet onderscheid tussen **decision quality** en **data quality**. `reliable_locations` is de strengste maat voor de vraag of een gebruiker op een locatie veilig meerdere routes kan vergelijken. Het is geen percentage "goede data". Afzonderlijke KPI's tonen daarom high-quality brondata, bekende basistarieven, geprijsde Direct/QR-routes en complete prijsroutes.

### TotalEnergies / MRA-E resolutie

De actuele officiele TotalEnergies-bron publiceert voor MRA-E meerdere AC-tariefgroepen: MRA-E 2 t/m 5, MRA-E 6 en MRA-E 6 Dynamic. Laadwerk publiceert daarnaast een eigen contractindeling voor regio Noordwest: laadpalen geplaatst vanaf 1 juli 2024 vallen onder de nieuwe afspraken, oudere locaties onder de oude afspraken. Dat is bruikbare concessie-informatie, maar nog geen veilige connector-mapping. In NDW is `last_updated` geen plaatsingsdatum en een vervangen fysieke paal kan volgens Laadwerk het oude tarief behouden. Voor Huizen is daarom in de publieke NDW/OCPI-kenmerken en de onderzochte officiele bronnen nog geen reproduceerbare koppeling gevonden waarmee een individueel TNLP/PP-/EVSE-ID veilig aan een concrete tariefgroep kan worden toegewezen. TNLP-/PP-nummerreeksen, vermogen en `last_updated` worden daarom **niet** als concessieheuristiek gebruikt.

Er is bovendien een semantisch verschil tussen de openbare tabellen: TotalEnergies publiceert MRA-E 6 als concessietarief, terwijl Laadwerk voor nieuwe TotalEnergies-palen een eigen maximaal afgesproken direct-payment tarief publiceert. Die labels worden niet als hetzelfde contract behandeld zonder laadpunt-specifiek bewijs. Dat is extra belangrijk voor MRA-E 6 Dynamic: het tarief wisselt per tijdsblok en de uiteindelijke sessieprijs is de som van de energie die in ieder tijdsblok daadwerkelijk is geladen. Alleen het label "Dynamic" of "nieuw" herkennen zou dus nog geen betrouwbare vaste kWh-prijs opleveren. Totdat een officiele laadpunt-specifieke bron of expliciet OCPI-tarief deze koppeling levert, blijft de AC-fallback bewust regionaal en indicatief.

## Databron en datastroom

Primaire bron: NDW DOT-NL OCPI snapshots.

```text
https://opendata.ndw.nu/charging_point_locations_ocpi.json.gz
https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz
```

Datastroom:

```text
NDW OCPI + geverifieerde publieke prijsbronnen
        |
        v
GitHub Actions
        |
        +--> dagelijkse broncontrole statische MSP-regels
        |
        v
process.py
        |
        v
huizen-data.json [schema 5]
        |
        +--> scripts/generate_quality_report.py
        |           |
        |           +--> pricing-quality.json [schema 2]
        |           +--> GitHub Actions summary
        |
        v
index.html + app.js + kwaliteit.html
        |
        v
GitHub Pages
```

De applicatie heeft geen runtime backend of database.

## Datakwaliteit

`kwaliteit.html` is de beheerpagina voor betrouwbaarheid en dekking. Het rapport meet onder andere:

- reliable, indicative en insufficient locaties;
- reliable, indicative en insufficient connectorprofielen;
- Direct/QR ondersteund, geprijsd en ongeprijsd;
- verdeling van bronkwaliteit, prijs-specificiteit, kostenvolledigheid en decision grade;
- prijsbronnen en statische prijsregels die tijdens de run niet konden worden geverifieerd;
- bekende kostencomponenten die ranking blokkeren;
- statusdata ouder dan 24 uur, 7 dagen en 30 dagen;
- adressen met meerdere records en mogelijke CPO-wissels;
- interne consistentie tussen `huizen-data.json` en het onafhankelijk herberekende kwaliteitsrapport.

Er is bewust geen samengestelde score van bijvoorbeeld 82/100. Een score zou verschillende soorten risico onterecht samenvoegen.

## Status is een snapshot

De kaart is statisch. De huidige workflow haalt NDW-data periodiek op en de getoonde beschikbaarheid is daarom een snapshot, geen gegarandeerde realtime status. De UI noemt dit expliciet. Een latere roadmapstap onderzoekt of statusinformatie vaker moet worden vernieuwd zonder de eenvoudige statische architectuur voor prijsdata op te geven.

## Fallbacks

Wanneer geen bruikbaar regulier connector-tarief uit NDW beschikbaar is, gebruikt de processor alleen gecontroleerde alternatieven:

1. een geverifieerde officiele regionale CPO-prijs wanneer die eenduidig past;
2. een operator-mediaan alleen als diagnostische schatting wanneer voldoende landelijke voorbeelden bestaan;
3. anders blijft de basisprijs onbekend.

Een operator-mediaan wordt niet gebruikt voor een sessieranking, omdat lokale vaste, tijd- of afrondingscomponenten daarmee onbekend kunnen blijven. Andere fallbacks hebben een lagere `price_specificity` en krijgen daarom niet automatisch dezelfde decision grade als een connector-specifiek NDW-tarief.

## Automatische updates

`.github/workflows/update.yml`:

1. draait unit tests;
2. controleert de actuele publieke MSP-prijsregels en schrijft tijdelijk `pricing-source-status.json`;
3. haalt NDW-data en aanvullende CPO-bronnen op;
4. genereert `huizen-data.json`;
5. genereert `pricing-quality.json`;
6. schrijft de kwaliteits-KPI's naar de GitHub Actions summary;
7. commit beide gegenereerde JSON-bestanden als ze zijn gewijzigd.

Een mislukte publieke MSP-broncontrole schakelt die ene statische regel fail-closed uit. De gehele kaartupdate wordt daardoor niet onnodig geblokkeerd. Voor bronnen met meerdere officiele, inhoudelijk equivalente publieke pagina's mag de monitor alleen bij een ophaalfout een tweede live URL proberen. Een inhoudelijke mismatch op een wel opgehaalde primaire pagina stopt direct fail-closed, zodat oudere wording op een alternatieve pagina een echte prijswijziging niet kan maskeren. Zo'n fallback is geen last-known-good cache: ook de fallbackpagina wordt tijdens diezelfde run live opgehaald en moet alle semantische prijscontroles doorstaan.

`.github/workflows/pricing-monitor.yml` blijft daarnaast de periodieke bronmonitor en maakt wijzigingen zichtbaar.

## Lokaal testen

```bash
python3 -m unittest discover -s tests -q
python3 -m py_compile process.py scripts/check_pricing_sources.py scripts/generate_quality_report.py
node --check app.js
```

Lokale webserver:

```bash
python3 -m http.server 8000
```

Open daarna `http://localhost:8000/`.

## Projectstructuur

```text
.github/workflows/
  pricing-monitor.yml
  update.yml
scripts/
  check_pricing_sources.py
  generate_quality_report.py
tests/
  test_pricing.py
  test_pricing_monitor.py
  test_quality_report.py
app.js
huizen-boundary.geojson
huizen-data.json
index.html
kwaliteit.html
methodologie.html
pricing-quality.json
pricing-sources.json
process.py
README.md
ROADMAP.md
```

## Roadmap en definitie van stabiel

Zie [ROADMAP.md](ROADMAP.md). Het doel is niet 100 procent prijsdekking. Het eindproduct is stabiel wanneer de site consequent onderscheid maakt tussen betrouwbare, indicatieve en onvoldoende informatie, geen bekende kosten weglaat in een harde ranking, alle statische prijsregels controleert en wijzigingen reproduceerbaar met tests kan verwerken.

## Herkomst

Het project is oorspronkelijk ontstaan uit `jdevalk/laadpalenwijchen.nl`. Inmiddels heeft Laadpalen Huizen een eigen prijsmodel, connectorniveau-datamodel, bronharvesting, kwaliteitsrapportage en roadmap. De Wijchen-repository is daarom historische herkomst en geen functionele upstream meer.

## Disclaimer

De kaart is een hulpmiddel. CPO- en MSP-tarieven kunnen wijzigen en apps kunnen aanvullende locatie-specifieke kosten tonen. Controleer voor het starten van een laadsessie altijd de prijs op het laadpunt, de betaalpagina of in de app van de gekozen aanbieder.
