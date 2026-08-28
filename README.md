# Laadpalen Huizen

Laadpalen Huizen is een statische open-source webapp voor publiek toegankelijke laadpunten in de gemeente Huizen. De kaart helpt een EV-rijder om voor een concrete laadsessie laadpassen, Direct/QR betalen en andere gemodelleerde betaalroutes te vergelijken op totale sessiekosten.

Live website: https://rubenwoudsma.github.io/laadpalenhuizen/

Kwaliteitsrapport: https://rubenwoudsma.github.io/laadpalenhuizen/kwaliteit.html

## Scope

De vergelijking gaat uitsluitend over **publiek toegankelijke laadpunten**. Thuisladen valt buiten het product en wordt niet als alternatief meegerekend. Afhankelijk van onder meer het thuisenergiecontract, zonnepanelen en een werkgevers- of leasevergoeding kan thuisladen goedkoper zijn dan publiek laden.

De site vergelijkt alleen prijsroutes waarvoor voldoende openbare en reproduceerbare informatie beschikbaar is. De prijs op de betaalpagina, in de app van de aanbieder en op de uiteindelijke factuur blijft leidend.

## Kernprincipe

**Betrouwbaarheid gaat voor dekking.**

Quality Model v2.1 onderscheidt vier praktische uitkomsten:

1. **Betrouwbaar vergelijkbaar**: de prijsroute is voldoende specifiek en volledig om een harde vergelijking te ondersteunen.
2. **Indicatief vergelijkbaar**: alle geldcomponenten zijn numeriek begrensd, maar de precieze uitkomst hangt bijvoorbeeld af van een bekende prijsband, regionale specificiteit of een andere begrensde onzekerheid. Deze route wordt wel gerangschikt.
3. **Onvolledig, vanaf-bedrag**: een bruikbare prijscomponent is bekend, maar minstens één geldcomponent is niet numeriek begrensd. De kaart toont de bekende component als `vanaf`, maar rangschikt de route niet.
4. **Onbekend**: er is geen betekenisvolle numerieke basis voor een sessie-indicatie.

Een onbekende prijs blijft onbekend. De applicatie vult geen prijs in om de dekking kunstmatig te verhogen. Betrouwbaarheid bepaalt vooral hoe stellig de kaart mag adviseren, niet of een bruikbare begrensde indicatie überhaupt zichtbaar mag zijn.

## Connectorniveau

De berekening gebeurt per connectorprofiel. Binnen één locatie kunnen AC/DC, vermogen, status en gekoppelde OCPI-tarieven verschillen.

`process.py` gebruikt bij voorkeur rechtstreeks uit OCPI:

- `Connector.power_type` voor AC of DC;
- `Connector.tariff_ids` voor connectorgebonden tarieven;
- `Connector.max_electric_power`, of spanning, stroom en fasen wanneer dat nodig is voor het vermogen;
- EVSE- en connectorstatus voor de actuele snapshot;
- locatie-ID en EVSE-ID voor herleidbaarheid.

Laadvermogen wordt niet gebruikt om AC of DC te raden. Als `power_type` ontbreekt, is alleen een conservatieve inferentie uit een ondubbelzinnig connectortype toegestaan.

## Betaalroutes

De kernvergelijking bevat momenteel:

- Direct / QR, wanneer ondersteuning en een berekenbare prijs zijn bevestigd;
- ANWB, Zonder abonnement;
- Tap Electric, Light;
- Vattenfall InCharge, Gratis laadpas;
- E-Flux by Road, Flex;
- Shell Recharge, Basic;
- Laadkompas, Zonder abonnement.

De lijst is bewust beperkt. Een extra betaaloptie wordt alleen gemodelleerd wanneer de prijslogica openbaar, reproduceerbaar en relevant genoeg is voor de gebruikersbeslissing.

### Direct / QR

De bronprioriteit voor Direct / QR is strikt:

1. expliciet connectorgebonden OCPI `AD_HOC_PAYMENT`;
2. een tijdens dezelfde run geverifieerde officiële CPO-bron;
3. een generiek OCPI-tarief zonder `Tariff.type`, uitsluitend als direct betalen onafhankelijk is bevestigd.

Een generiek OCPI-tarief bewijst dus niet dat Direct / QR beschikbaar is.

Aanvullende officiële Direct/QR-logica is aanwezig voor:

- Ubitricity `UB2`, officieel MRA-E Direct/QR-tarief;
- TotalEnergies `GFX`, de officiële regel dat de CPO-basisprijs ook de direct-payment prijs is;
- Lidl `LDL`, het actuele Lidl.nl-tarief wordt per run apart voor AC en DC uit de officiële pagina gelezen. Wanneer NDW geen CPO-basistarief levert, mag dezelfde officiële AC/DC-prijs ook als network-specific CPO-basis worden gebruikt omdat Lidl die expliciet publiceert voor laden met een eigen laadpas;
- Vattenfall `NUO` wordt niet generiek als Direct/QR ondersteund gemarkeerd. Laadwerk beschrijft direct betalen via QR in zijn laadnetwerk, maar zonder reproduceerbare stationkoppeling is dat onvoldoende bewijs voor ieder individueel NDW-profiel.

## Sessiekosten

De vergelijking rekent de totale sessieprijs, niet alleen een losse prijs per kWh.

```text
CPO-afrekenenergie = gekozen kWh afgerond volgens ENERGY step_size
CPO-energie         = CPO-afrekenenergie x CPO-kWh-tarief
MSP-opslag          = gekozen kWh x eventuele MSP-opslag per kWh
sessietotaal        = CPO-energie + vaste CPO-kosten + MSP-kosten en opslagen
```

Prijsbanden worden als bandbreedte doorgerekend. Betrouwbare én complete indicatieve routes kunnen in Quality Model v2.1 worden gerangschikt. Voor de volgorde gebruikt de UI de middenwaarde van een prijsband, terwijl de volledige minimum- en maximumwaarde zichtbaar blijft. Een harde winnaar wordt alleen getoond wanneer de vergelijking daarvoor voldoende sterk is. Als prijsbanden overlappen of de voordeligste route slechts indicatief is, blijft de uitkomst expliciet onzeker. Een gedeeltelijke route met een bekende numerieke component toont een `vanaf`-bedrag maar doet niet mee aan de ranking.

## OCPI-tarieven

De huidige processor ondersteunt veilig:

- `ENERGY`;
- `FLAT`;
- expliciete OCPI-btw per prijscomponent;
- `ENERGY step_size`;
- geldigheidsperiode van een tarief;
- scheiding tussen reguliere en `AD_HOC_PAYMENT` tarieven;
- tariefkoppeling op `country_code`, `party_id` en `tariff_id`.

OCPI `price` is exclusief btw. Als een component een geldige `vat` bevat, wordt die toegevoegd. Als `vat` ontbreekt, wordt geen Nederlands btw-percentage aangenomen.

De volgende constructies worden gedetecteerd en blokkeren ranking wanneer hun geldimpact niet numeriek kan worden begrensd:

- `TIME`;
- `PARKING_TIME`;
- `min_price` en `max_price`;
- onbekende of ongeldige prijsdimensies;
- conflicterende prijscomponenten of afrekenstappen.

`TariffRestrictions` zijn in v2.1 niet automatisch een blocker. Een restriction is een toepassingsvoorwaarde, geen zelfstandige geldcomponent. Als de enige onzekerheid is welk tarief binnen een volledig bekende ENERGY- of sessieband geldt, blijft de route `complete` en wordt de volledige band als `indicative` gerangschikt. Zodra restrictions samenhangen met niet-berekenbare TIME-, PARKING_TIME-, minimum/maximum- of andere onbegrenste kosten blijft de route fail-closed buiten de ranking. Een gebruiker voert in de huidige interface alleen de hoeveelheid energie in.

## MSP-regels

| Betaaloptie | Huidige modellering |
| --- | --- |
| ANWB zonder abonnement | CPO-prijs + EUR 0,89 per sessie. Gepubliceerde netwerkkortingen worden niet numeriek ingevuld zonder een reproduceerbaar specifiek tarief. |
| Tap Electric Light | Gemodelleerd CPO-subtotaal + 5 procent transactiekosten. |
| Vattenfall InCharge | Eigen netwerk op `NUO` zonder extra MSP-starttarief. Bij roaming rekent Vattenfall EUR 0,35 starttarief per sessie. Een roamingroute wordt alleen berekend als voor dat netwerk ook een Vattenfall-specifiek kWh-tarief uit een officiële bron beschikbaar is. |
| E-Flux Flex | EUR 0,31 per sessie, buiten E-Flux + EUR 0,024/kWh. De mogelijke extra clearingtoeslag wordt als sessieband meegenomen. |
| Shell Recharge Basic | Gepubliceerd Shell snellaadtarief op eigen netwerk, of de gepubliceerde AC/DC roamingband bij andere aanbieders, + EUR 0,35 per sessie. Locatieafhankelijke extra kosten houden de route indicatief. |
| Laadkompas zonder abonnement | CPO-prijs + sessietarief. De actuele officiële pagina noemt herhaaldelijk EUR 0,47 maar bevat ook één conflicterende EUR 0,39-vermelding. Zolang beide teksten aanwezig zijn wordt daarom EUR 0,39-EUR 0,47 als begrensde sessieband gebruikt. |

Eigen netwerk wordt primair via OCPI `party_id` bepaald. Een commerciële relatie tussen bedrijven is niet genoeg om twee netwerken als hetzelfde tarief te behandelen.

## Bronselectie voor het CPO-basistarief

De primaire bron is NDW DOT-NL. De processor probeert eerst het connectorgebonden OCPI-tarief te gebruiken.

Als dat ontbreekt:

- kan alleen een expliciet gemodelleerde officiële aanvullende CPO-bron worden gebruikt;
- blijft een regionale prijs ook regionaal in `price_specificity`;
- mag een operator-mediaan alleen als grove indicatie bestaan en nooit als complete sessieprijs of harde winnaar;
- blijft de prijs onbekend als geen veilige bron beschikbaar is.

Voor TotalEnergies in het MRA-E-gebied bestaan meerdere officiële tariefgroepen. De publieke NDW/OCPI-kenmerken bevatten geen reproduceerbare station-specifieke sleutel waarmee een locatie veilig aan MRA-E 2-5, MRA-E 6 of MRA-E 6 Dynamic kan worden gekoppeld. `last_updated`, laadvermogen en ID-patronen worden daarom niet als concessieheuristiek gebruikt. De AC-fallback blijft een officiële regionale prijsband en dus indicatief.

Voor Vattenfall InCharge in Noordwest-Nederland wordt dezelfde terughoudendheid toegepast. Wanneer NDW een bruikbaar connectorgebonden tarief levert, blijft dat de eerste bron. Ontbreekt zo'n tarief, dan mag alleen na succesvolle verificatie van zowel de officiële Vattenfall-tariefpagina als de actuele Laadwerk-concessiecontext een regionale AC-prijsband worden gebruikt. Vattenfall publiceert voor MRA 2021 EUR 0,5222/kWh en voor MRA 2024 EUR 0,3594/kWh in de piekperiode en EUR 0,3394/kWh in de dalperiode. Als de concessie van het individuele laadpunt niet reproduceerbaar is vastgesteld, wordt daarom de volledige band EUR 0,3394-EUR 0,5222/kWh getoond, met `source_quality = high`, `price_specificity = regional` en `decision_grade = indicative`. Adres, nabijheid, hardwareleeftijd en `last_updated` worden nooit gebruikt om MRA 2021 of MRA 2024 te gokken.

Een landelijk operatorgemiddelde wordt niet als CPO-fallback gebruikt. Zo'n gemiddelde zegt onvoldoende over het tarief van een specifieke concessie of connector in Huizen en telt daarom ook niet mee in de rangschikking.

## Quality Model v2.1

Iedere numerieke prijsroute heeft vier afzonderlijke kwaliteitsdimensies:

- `source_quality`: `high`, `medium` of `low`;
- `price_specificity`: `connector`, `network`, `regional`, `national`, `operator_estimate` of `unknown`;
- `cost_completeness`: `complete` of `partial`;
- `decision_grade`: `reliable`, `indicative` of `exclude`.

Een officiële bron kan dus `high` zijn terwijl de prijs toch `indicative` is, bijvoorbeeld omdat alleen een regionale prijsband bekend is. `partial` betekent niet meer dat alle numerieke informatie verdwijnt: als een onafhankelijke prijscomponent wel actueel en reproduceerbaar bekend is, kan die als niet-gerangschikt `vanaf`-bedrag zichtbaar blijven.

`kwaliteit.html` scheidt daarom **decision quality** van **data quality**. Het rapport toont onder meer:

- reliable, indicative en insufficient locaties en connectorprofielen;
- high-quality brondata;
- basistariefdekking;
- Direct/QR support en prijsdekking;
- complete en gedeeltelijke prijsroutes;
- blockers en inhoudelijke decision reasons;
- bronverificaties;
- statusversheid;
- multi-record adressen en operatorovergangssignalen.

## Bronmonitoring en fail-closed gedrag

GitHub Actions controleert officiële prijsbronnen vóór de dagelijkse dataverwerking.

De regels zijn:

- een tijdelijke netwerkfout op een CPO-bron blokkeert de NDW-update niet;
- een mislukte of inhoudelijk gewijzigde statische prijsregel wordt voor die run uitgeschakeld; onafhankelijk geverifieerde CPO- of netwerkcomponenten blijven wel als partiële informatie bruikbaar;
- een alternatieve officiële URL is alleen een transportfallback na een ophaalfout;
- een inhoudelijke mismatch op een bereikbare primaire pagina wordt niet gemaskeerd door een fallback-URL;
- er is geen last-known-good prijsfallback. Een mislukte MSP-controle mag dus geen oude MSP-fee laten doorlekken, maar wist ook geen onafhankelijk actuele CPO- of netwerkprijs;
- aanvullende CPO-harvesting accepteert een tarief alleen als de verwachte semantiek tijdens de huidige run herkenbaar is.
- de Vattenfall MRA-E-fallback is tweebronnig: de exacte MRA-bedragen moeten op de Vattenfall-tariefpagina staan en Laadwerk moet in dezelfde run de nieuwe/oude concessiecontext en de vervangingswaarschuwing bevestigen. Valt een van beide controles weg of spreken de bronnen elkaar tegen, dan wordt de regionale fallback voor die run uitgeschakeld.

De maandelijkse pricing monitor maakt bronproblemen zichtbaar via de workflow en GitHub issue-synchronisatie.

## Structurele beperkingen

De volgende beperkingen zijn bewust onderdeel van het huidige product:

- **Laadnet**: de eigenaar van een station kan het gasttarief instellen. Zonder openbare station-specifieke machineleesbare prijs wordt geen generiek Laadnet-tarief gebruikt.
- **EQUANS / Velian**: tarieven zijn contract- en concessieafhankelijk. De openbare bronnen leveren voor de Huizen-records geen reproduceerbare station-specifieke koppeling.
- **Vattenfall**: de officiële MRA-E-tarieven zijn regionaal reproduceerbaar, maar de publiek vindbare gemeentelijke ArcGIS-mirror van Laadwerk bevat `location_code` en `concession_id`, zonder EVSE-ID of tarief en vormt geen aangetoonde regionale Huizen-feed waarmee een NDW EVSE/location-ID veilig aan de toepasselijke concessie kan worden gekoppeld. Bij ontbrekend NDW-tarief gebruikt de kaart daarom de officiële regionale band in plaats van een verzonnen stationstarief. Direct/QR blijft per station fail-closed zolang de ondersteuning niet veilig gekoppeld kan worden.
- **JOLT**: openbare officiële JOLT-pagina's tonen niet overal hetzelfde ad-hocbedrag. Zolang de bron voor het Huizen-station niet eenduidig en reproduceerbaar is, wordt geen generieke prijs toegepast.
- **OCPI tijd en restricties**: `TIME`, `PARKING_TIME` en correcte min/max-prijsgrenzen vereisen meer sessie-informatie dan alleen kWh en blijven daarom blockers. `TariffRestrictions` blokkeren alleen wanneer hun geldimpact niet binnen een bekende numerieke band kan worden begrensd.
- **Operatortransities**: dezelfde adreslocatie kan meerdere CPO-records bevatten. Adres, nabijheid, ouderdom of operatorverschil is niet voldoende bewijs om een record automatisch te verwijderen.
- **Beschikbaarheid**: de site toont de laatste verwerkte NDW-snapshot, niet gegarandeerd realtime status.

## Architectuur

```text
GitHub Actions
      |
      +--> NDW/OCPI locatie- en tariefdata
      +--> officiële CPO- en MSP-bronnen
      |
      v
process.py
      |
      v
huizen-data.json
      |
      v
scripts/generate_quality_report.py
      |
      v
pricing-quality.json
      |
      +--> index.html + app.js
      +--> kwaliteit.html
      +--> methodologie.html
      |
      v
GitHub Pages
```

Er is geen runtime backend of database.

## Automatische updates

`.github/workflows/update.yml` voert de dagelijkse datapipeline uit. De workflow:

1. draait de regressietests;
2. controleert prijsbronnen fail-closed per regel;
3. verwerkt de actuele NDW/OCPI-data;
4. genereert het kwaliteitsrapport;
5. commit alleen opnieuw gegenereerde productie-JSON wanneer de pipeline slaagt.

`.github/workflows/pricing-monitor.yml` controleert de officiële prijsbronnen periodiek onafhankelijk van de dagelijkse data-update.

## Lokaal testen

```bash
python3 -m unittest discover -s tests
python3 -m py_compile process.py scripts/check_pricing_sources.py scripts/generate_quality_report.py
node --check app.js
```

Voor een volledige datarun is netwerktoegang tot NDW en de geconfigureerde officiële prijsbronnen nodig.

## Projectstructuur

```text
.github/workflows/              GitHub Actions
app.js                          kaart, sessiekeuze en prijsvergelijking
huizen-boundary.geojson         gemeentegrens
index.html                      hoofdinterface
kwaliteit.html                  actuele kwaliteitsrapportage
methodologie.html               actuele methodologie
pricing-sources.json            gecontroleerde openbare prijsregels
process.py                      NDW/OCPI verwerking en prijsmodel
scripts/check_pricing_sources.py bronmonitor
scripts/generate_quality_report.py kwaliteitsgenerator
tests/                          regressietests
```

`huizen-data.json` en `pricing-quality.json` zijn gegenereerde productie-uitvoer. Zij horen alleen bij een release of ZIP wanneer ze aantoonbaar met de actuele pipeline en voldoende actuele brondata zijn gegenereerd.

## Open source

De broncode, prijsregels en kwaliteitslogica zijn openbaar, zodat berekeningen en aannames controleerbaar blijven.

Gebruik de data en berekeningen als hulpmiddel. Controleer vóór het starten van een laadsessie de actuele prijs en voorwaarden van de gekozen aanbieder.
