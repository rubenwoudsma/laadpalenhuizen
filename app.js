'use strict';

const HUIZEN = [52.2955, 5.2451];
const STORAGE_KEY = 'laadpalenhuizen-v3-preferences';
const DEFAULT_KWH = 25;
const MIN_KWH = 5;
const MAX_KWH = 60;
const DIRECT_PASS_ID = 'direct_pay';

const map = L.map('map', { center: HUIZEN, zoom: 14, zoomControl: true });
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

let passes = [];
let points = [];
let markers = [];
let activeId = null;
let activeOperator = 'all';
let selectedPasses = new Set();
let energyKwh = DEFAULT_KWH;
let userLocation = null;
let userMarker = null;
let searchQuery = '';

function euro(value, digits = 2) {
  return `€${Number(value).toFixed(digits).replace('.', ',')}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function normalizeSearch(value) {
  return String(value ?? '').toLocaleLowerCase('nl-NL').replace(/\s+/g, ' ').trim();
}

function ensureDirectPass(dataPasses) {
  const result = Array.isArray(dataPasses) ? [...dataPasses] : [];
  if (!result.some(pass => pass.id === DIRECT_PASS_ID)) {
    result.unshift({
      id: DIRECT_PASS_ID,
      name: 'Direct / QR',
      plan: 'Zonder laadpas',
      color: '#15803d',
      monthly_fee: 0,
      summary: 'Rechtstreeks betalen bij de laadpaal wanneer de CPO dit ondersteunt',
      default_selected: true,
      kind: 'direct',
    });
  }
  return result;
}

function passById(id) {
  return passes.find(pass => pass.id === id);
}

function passLabel(pass) {
  return pass ? pass.name : 'Onbekend';
}

function sessionCostRange(quote) {
  if (!quote || quote.kwh == null) return null;
  const kwhRange = quote.range?.length === 2 ? quote.range : [quote.kwh, quote.kwh];
  const sessionRange = quote.session_range?.length === 2
    ? quote.session_range
    : [Number(quote.session || 0), Number(quote.session || 0)];
  const multiplier = 1 + Number(quote.percentage || 0);
  const min = Number(kwhRange[0]) * energyKwh * multiplier + Number(sessionRange[0]);
  const max = Number(kwhRange[1]) * energyKwh * multiplier + Number(sessionRange[1]);
  const midKwh = Number(quote.kwh) * energyKwh * multiplier;
  const midSession = Number(quote.session || 0);
  return {
    min,
    max,
    mid: midKwh + midSession,
  };
}

function formatCostRange(cost) {
  if (!cost) return 'onbekend';
  if (Math.abs(cost.max - cost.min) < 0.005) return euro(cost.mid);
  return `${euro(cost.min)}-${euro(cost.max)}`;
}

function quoteForPoint(pt, passId) {
  return pt.pricing?.[passId] || null;
}

function comparisonFor(pt) {
  const pricedRows = [...selectedPasses]
    .map(id => ({ id, quote: quoteForPoint(pt, id) }))
    .filter(row => row.quote?.kwh != null)
    .map(row => ({ ...row, cost: sessionCostRange(row.quote) }))
    .map(row => ({ ...row, total: row.cost.mid, min: row.cost.min, max: row.cost.max }))
    .sort((a, b) => a.total - b.total)
    .map((row, index) => ({ ...row, rank: index + 1 }));

  const comparable = pricedRows.length >= 2;
  let best = pricedRows.length === 1 ? pricedRows[0] : null;
  if (comparable) {
    best = pricedRows.find(row => pricedRows.every(other => (
      other.id === row.id || row.max < other.min - 1e-9
    ))) || null;
  }

  return {
    rows: pricedRows,
    best,
    likely: pricedRows[0] || null,
    comparable,
    ambiguous: comparable && !best,
  };
}

function rankForPass(cmp, passId) {
  return cmp.rows.find(row => row.id === passId)?.rank || null;
}

function confidenceLabel(confidence) {
  if (confidence === 'high') return 'hoge zekerheid';
  if (confidence === 'medium') return 'redelijke schatting';
  return 'indicatief';
}

function routeLabel(quote) {
  if (quote?.route === 'ad_hoc') return 'direct / QR';
  if (quote?.route === 'msp_home') return 'eigen netwerk';
  if (quote?.route === 'msp_roaming') return 'roaming';
  return 'laadpasroute';
}

function sourceInfo(pt) {
  if (pt.pricing_source === 'ndw') return { cls: 'source-direct', label: 'NDW CPO-tarief' };
  if (pt.pricing_source === 'totalenergies_mrae') return { cls: 'source-regional', label: 'Officiële TotalEnergies MRA-E prijsband' };
  if (pt.pricing_source === 'totalenergies_mrae_dc') return { cls: 'source-regional', label: 'Officieel TotalEnergies MRA-E DC-tarief' };
  if (pt.pricing_source === 'operator_median') return { cls: 'source-estimate', label: 'Schatting via operator-mediaan' };
  return { cls: 'source-unknown', label: 'CPO-basistarief onbekend' };
}

function knownDirectPayment(pt) {
  if (pt.direct_payment?.supported) return true;
  if ((pt.party_id || '').toUpperCase() === 'UB2') return true;
  return normalizeSearch(pt.operator).includes('ubitricity');
}

function unknownQuoteDetail(pt, pass) {
  if (pass?.id === DIRECT_PASS_ID) {
    if (knownDirectPayment(pt)) {
      return 'Direct betalen is beschikbaar, maar er staat geen apart berekenbaar ad-hoc tarief in deze snapshot. Scan de QR-code voor het live bedrag.';
    }
    return 'Geen directe betaalroute met berekenbaar tarief bevestigd in de huidige data.';
  }
  return 'Geen betrouwbare prijs voor deze betaalroute beschikbaar.';
}

function quoteDetail(quote) {
  const parts = [`<span class="route-pill">${escapeHtml(routeLabel(quote))}</span>`];
  if (quote.range?.length === 2) {
    parts.push(`${euro(quote.range[0], 3)}-${euro(quote.range[1], 3)}/kWh`);
  } else {
    parts.push(`${euro(quote.kwh, 3)}/kWh`);
  }
  if (quote.percentage) parts.push(`+${Math.round(Number(quote.percentage) * 100)}% transactiekosten`);
  if (quote.session_range?.length === 2) {
    parts.push(`${euro(quote.session_range[0])}-${euro(quote.session_range[1])} start`);
  } else if (Number(quote.session || 0) > 0) {
    parts.push(`${euro(quote.session)} start`);
  } else if (!quote.percentage) {
    parts.push('geen starttarief');
  }
  parts.push(confidenceLabel(quote.confidence));
  return parts.join(' · ');
}

function markerColor(pt) {
  const cmp = comparisonFor(pt);
  if (cmp.best && cmp.comparable) return '#15803d';
  if (cmp.ambiguous) return '#0f766e';
  return '#6b7280';
}

function markerText(pt) {
  const cmp = comparisonFor(pt);
  if (!cmp.likely) return '?';
  const value = euro(cmp.likely.total, 0).replace('€', '');
  return cmp.ambiguous ? `~${value}` : value;
}

function makeIcon(pt) {
  return L.divIcon({
    className: '',
    iconSize: [30, 30],
    iconAnchor: [15, 29],
    popupAnchor: [0, -26],
    html: `<div class="price-marker" style="--marker-color:${markerColor(pt)}"><span>${markerText(pt)}</span></div>`,
  });
}

function statusLabel(pt) {
  return pt.available ? 'Vrij' : 'Geen vrije poort';
}

function selectedRowsForPoint(pt, cmp) {
  return passes
    .filter(pass => selectedPasses.has(pass.id))
    .map(pass => {
      const quote = quoteForPoint(pt, pass.id);
      return {
        pass,
        quote,
        cost: sessionCostRange(quote),
        rank: rankForPass(cmp, pass.id),
      };
    })
    .sort((a, b) => {
      if (a.cost == null && b.cost == null) return passes.indexOf(a.pass) - passes.indexOf(b.pass);
      if (a.cost == null) return 1;
      if (b.cost == null) return -1;
      return a.cost.mid - b.cost.mid;
    });
}

function popupQuoteRows(pt, cmp) {
  if (selectedPasses.size === 0) {
    return '<div class="popup-quote"><div class="popup-quote-detail" style="padding-left:0">Selecteer minimaal één betaaloptie.</div></div>';
  }

  return selectedRowsForPoint(pt, cmp).map(({ pass, quote, cost, rank }) => {
    if (cost == null) {
      return `<div class="popup-quote">
        <div class="popup-quote-main">
          <div class="popup-quote-pass"><span class="popup-quote-dot" style="background:${pass.color}"></span><span>${escapeHtml(pass.name)}</span></div>
          <span class="popup-quote-price" style="color:var(--muted)">onbekend</span>
        </div>
        <div class="popup-quote-detail">${escapeHtml(unknownQuoteDetail(pt, pass))}</div>
      </div>`;
    }

    const topThree = rank && rank <= 3;
    return `<div class="popup-quote ${topThree ? 'top-three' : ''}">
      <div class="popup-quote-main">
        <div class="popup-quote-pass">
          ${topThree ? `<span class="rank-badge">#${rank}</span>` : `<span class="popup-quote-dot" style="background:${pass.color}"></span>`}
          <span>${escapeHtml(pass.name)}</span>
        </div>
        <span class="popup-quote-price">${formatCostRange(cost)}</span>
      </div>
      <div class="popup-quote-detail">${quoteDetail(quote)}</div>
    </div>`;
  }).join('');
}

function makePopup(pt) {
  const cmp = comparisonFor(pt);
  const src = sourceInfo(pt);
  let bestHtml = '<span>Prijsvergelijking</span><strong>onvoldoende data</strong>';

  if (cmp.best && cmp.comparable) {
    bestHtml = `<span>Voordeligst: ${escapeHtml(passLabel(passById(cmp.best.id)))}</span><strong>${formatCostRange(cmp.best.cost)}</strong>`;
  } else if (cmp.ambiguous) {
    bestHtml = `<span>Laagste schatting: ${escapeHtml(passLabel(passById(cmp.likely.id)))}</span><strong>${formatCostRange(cmp.likely.cost)}</strong>`;
  } else if (cmp.best) {
    bestHtml = `<span>Enige berekende optie voor ${energyKwh} kWh</span><strong>${formatCostRange(cmp.best.cost)}</strong>`;
  }

  const cpoRange = pt.cpo_rate_range?.length === 2
    ? ` · CPO ${euro(pt.cpo_rate_range[0], 3)}-${euro(pt.cpo_rate_range[1], 3)}/kWh`
    : '';
  const idLine = pt.evse_ids?.length ? `<div class="sub">ID: ${escapeHtml(pt.evse_ids[0])}</div>` : '';

  return `<div class="popup">
    <h3>${escapeHtml(pt.name)}</h3>
    <div class="sub">${escapeHtml(pt.operator)} · ${escapeHtml(pt.address)}</div>
    ${idLine}
    <div class="sub">Status bij laatste NDW-update: ${statusLabel(pt)}</div>
    <div class="best">${bestHtml}</div>
    <div class="popup-quotes">
      <div class="popup-quotes-title">Betaalopties voor ${energyKwh} kWh, Top 3 groen</div>
      <div class="popup-quotes-list">${popupQuoteRows(pt, cmp)}</div>
      <span class="source-pill ${src.cls}">${src.label}${cpoRange}</span>
    </div>
  </div>`;
}

function renderQuoteRows(pt, cmp) {
  if (selectedPasses.size === 0) return '<div class="empty">Selecteer minimaal één betaaloptie.</div>';

  return selectedRowsForPoint(pt, cmp).map(({ pass, quote, cost, rank }) => {
    if (cost == null) {
      return `<div class="quote-row">
        <div class="quote-main">
          <div class="quote-pass"><span class="quote-dot" style="background:${pass.color}"></span>${escapeHtml(pass.name)}</div>
          <div class="quote-price" style="color:var(--muted)">onbekend</div>
        </div>
        <div class="quote-detail">${escapeHtml(unknownQuoteDetail(pt, pass))}</div>
      </div>`;
    }

    const topThree = rank && rank <= 3;
    return `<div class="quote-row ${topThree ? 'top-three' : ''}">
      <div class="quote-main">
        <div class="quote-pass">
          ${topThree ? `<span class="rank-badge">#${rank}</span>` : `<span class="quote-dot" style="background:${pass.color}"></span>`}
          ${escapeHtml(pass.name)}
        </div>
        <div class="quote-price">${formatCostRange(cost)}</div>
      </div>
      <div class="quote-detail">${quoteDetail(quote)}</div>
      ${quote.note ? `<div class="quote-note">${escapeHtml(quote.note)}</div>` : ''}
    </div>`;
  }).join('');
}

function distanceM(pt) {
  if (!userLocation) return null;
  const [lat1, lng1] = userLocation;
  const lat2 = pt.lat;
  const lng2 = pt.lng;
  const radius = 6371000;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLng = (lng2 - lng1) * Math.PI / 180;
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
  return radius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function distanceLabel(pt) {
  const distance = distanceM(pt);
  if (distance == null) return '';
  if (distance < 1000) return `${Math.round(distance / 10) * 10} m`;
  return `${(distance / 1000).toFixed(1).replace('.', ',')} km`;
}

function cardBestHtml(cmp) {
  if (selectedPasses.size === 0) {
    return '<div class="best-name">Selecteer betaalopties</div><div class="best-price">-</div>';
  }
  if (!cmp.likely) {
    return '<div class="best-name">Geen berekenbare prijs</div><div class="best-price">-</div>';
  }
  if (cmp.ambiguous) {
    const pass = passById(cmp.likely.id);
    return `<div class="best-name">Laagste schatting: ${escapeHtml(passLabel(pass))}<span class="best-effective">prijsbanden overlappen, dus geen harde winnaar</span></div>
      <div class="best-price">${formatCostRange(cmp.likely.cost)}</div>`;
  }
  if (!cmp.best) return '<div class="best-name">Geen eenduidige goedkoopste optie</div><div class="best-price">-</div>';

  const pass = passById(cmp.best.id);
  const effective = cmp.best.total / energyKwh;
  return `<div class="best-name">Voordeligst: ${escapeHtml(passLabel(pass))}</div>
    <div class="best-price">${formatCostRange(cmp.best.cost)}<span class="best-effective">${energyKwh} kWh · ${euro(effective, 3)}/kWh effectief</span></div>`;
}

function renderList(inputPoints) {
  const list = document.getElementById('list');
  document.getElementById('visible-count').textContent = `${inputPoints.length} zichtbaar`;

  if (!inputPoints.length) {
    list.innerHTML = '<div class="empty">Geen laadpunten gevonden. Pas je zoekopdracht of filter aan.</div>';
    return;
  }

  list.innerHTML = inputPoints.map(pt => {
    const cmp = comparisonFor(pt);
    const color = markerColor(pt);
    const src = sourceInfo(pt);
    const distance = distanceLabel(pt);
    const range = pt.cpo_rate_range?.length === 2
      ? ` · CPO-range ${euro(pt.cpo_rate_range[0], 3)}-${euro(pt.cpo_rate_range[1], 3)}`
      : '';
    const partyTag = pt.party_id ? `<span class="tag">CPO ${escapeHtml(pt.party_id)}</span>` : '';
    const directTag = knownDirectPayment(pt) ? '<span class="tag">Direct betalen</span>' : '';

    return `<article class="card ${activeId === pt.id ? 'active' : ''}" data-id="${escapeHtml(pt.id)}" style="--card-color:${color}">
      <div class="card-stripe"></div>
      <div class="card-inner">
        <div class="card-head">
          <div class="card-name">${escapeHtml(pt.name)}</div>
          <span class="status-badge ${pt.available ? 'status-free' : 'status-other'}">${statusLabel(pt)}</span>
        </div>
        <div class="card-sub">${escapeHtml(pt.operator)} · ${escapeHtml(pt.address)}${distance ? ` · ${distance}` : ''}</div>
        <div class="tags">
          ${pt.max_power ? `<span class="tag">${pt.max_power} kW</span>` : ''}
          ${(pt.connectors || []).slice(0, 3).map(type => `<span class="tag">${escapeHtml(type)}</span>`).join('')}
          ${pt.num_evses > 1 ? `<span class="tag">${pt.num_evses} aansluitingen</span>` : ''}
          ${partyTag}${directTag}
        </div>
        <div class="best-row">${cardBestHtml(cmp)}</div>
        <div class="breakdown">
          <div class="breakdown-title">Betaalopties voor ${energyKwh} kWh, Top 3 op geschatte sessiekosten</div>
          ${renderQuoteRows(pt, cmp)}
          <span class="source-pill ${src.cls}">${src.label}${range}</span>
        </div>
      </div>
    </article>`;
  }).join('');

  list.querySelectorAll('.card').forEach(card => {
    card.addEventListener('click', () => selectCard(card.dataset.id));
  });
}

function pointMatchesSearch(pt) {
  if (!searchQuery) return true;
  const haystack = normalizeSearch([
    pt.name,
    pt.address,
    pt.operator,
    pt.party_id,
    ...(pt.evse_ids || []),
  ].filter(Boolean).join(' '));
  return haystack.includes(searchQuery);
}

function visiblePoints() {
  let filtered = points.filter(pt => (
    (activeOperator === 'all' || pt.operator === activeOperator)
    && pointMatchesSearch(pt)
  ));

  if (userLocation) {
    filtered = [...filtered].sort((a, b) => distanceM(a) - distanceM(b));
  }
  return filtered;
}

function refreshMarkers() {
  const allowed = new Set(visiblePoints().map(pt => pt.id));
  markers.forEach(marker => {
    const pt = points.find(point => point.id === marker._pid);
    if (!pt) return;
    marker.setIcon(makeIcon(pt));
    marker.setPopupContent(makePopup(pt));
    if (allowed.has(pt.id)) {
      if (!map.hasLayer(marker)) marker.addTo(map);
    } else if (map.hasLayer(marker)) {
      map.removeLayer(marker);
    }
  });
}

function applyFilters() {
  renderList(visiblePoints());
  refreshMarkers();
}

function scrollActiveCardIntoView(block = 'nearest') {
  if (!activeId) return;
  requestAnimationFrame(() => {
    const escapedId = window.CSS?.escape ? CSS.escape(activeId) : activeId.replaceAll('"', '\\"');
    const card = document.querySelector(`.card[data-id="${escapedId}"]`);
    if (card) card.scrollIntoView({ block, behavior: 'smooth' });
  });
}

function selectCard(id) {
  activeId = activeId === id ? null : id;
  applyFilters();
  if (!activeId) return;

  const pt = points.find(point => point.id === activeId);
  if (!pt) return;
  map.setView([pt.lat, pt.lng], 16, { animate: true });
  const marker = markers.find(item => item._pid === activeId);
  if (marker) marker.openPopup();
  scrollActiveCardIntoView('nearest');
}

function selectCardFromMarker(id) {
  if (activeId === id) return;
  activeId = id;
  renderList(visiblePoints());
  scrollActiveCardIntoView('center');
}

function renderPassChips() {
  const container = document.getElementById('pass-chips');
  container.innerHTML = passes.map(pass => {
    const active = selectedPasses.has(pass.id);
    return `<button type="button" class="pass-chip ${active ? 'active' : ''}" data-pass="${escapeHtml(pass.id)}" aria-pressed="${active}" style="--pass-color:${pass.color}" title="${escapeHtml(pass.summary || '')}">
      <span class="dot"></span><span>${escapeHtml(pass.name)}</span><span class="plan">${escapeHtml(pass.plan || '')}</span>
    </button>`;
  }).join('');

  container.querySelectorAll('.pass-chip').forEach(button => {
    button.addEventListener('click', () => {
      const id = button.dataset.pass;
      if (selectedPasses.has(id)) selectedPasses.delete(id);
      else selectedPasses.add(id);
      savePreferences();
      renderPassChips();
      applyFilters();
    });
  });
}

function renderOperatorChips() {
  const counts = new Map();
  points.forEach(pt => counts.set(pt.operator, (counts.get(pt.operator) || 0) + 1));
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  document.getElementById('operator-chips').innerHTML = [
    `<button type="button" class="operator-chip ${activeOperator === 'all' ? 'active' : ''}" data-op="all">Alle <span class="count">${points.length}</span></button>`,
    ...sorted.map(([operator, count]) => `<button type="button" class="operator-chip ${activeOperator === operator ? 'active' : ''}" data-op="${escapeHtml(operator)}">${escapeHtml(operator)} <span class="count">${count}</span></button>`),
  ].join('');

  document.querySelectorAll('.operator-chip').forEach(button => {
    button.addEventListener('click', () => {
      activeOperator = button.dataset.op;
      renderOperatorChips();
      applyFilters();
    });
  });
}

function clampEnergy(value) {
  return Math.min(MAX_KWH, Math.max(MIN_KWH, Math.round(Number(value) || DEFAULT_KWH)));
}

function setEnergy(kwh, persist = true) {
  energyKwh = clampEnergy(kwh);
  document.getElementById('energy-label').textContent = `${energyKwh} kWh`;
  document.getElementById('energy-slider').value = String(energyKwh);
  document.querySelectorAll('.energy-chip').forEach(button => {
    button.classList.toggle('active', Number(button.dataset.kwh) === energyKwh);
  });
  if (persist) savePreferences();
  applyFilters();
}

function savePreferences() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      passes: [...selectedPasses],
      kwh: energyKwh,
    }));
  } catch (_) {
    // localStorage can be unavailable in strict privacy modes.
  }
}

function loadPreferences() {
  const defaults = passes.filter(pass => pass.default_selected !== false).map(pass => pass.id);
  selectedPasses = new Set(defaults);
  energyKwh = DEFAULT_KWH;

  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (saved?.passes && Array.isArray(saved.passes)) {
      const valid = new Set(passes.map(pass => pass.id));
      selectedPasses = new Set(saved.passes.filter(id => valid.has(id)));
    }
    if (Number.isFinite(Number(saved?.kwh))) energyKwh = clampEnergy(saved.kwh);
  } catch (_) {
    // Use defaults when stored preferences are invalid.
  }
}

function formatAge(iso) {
  if (!iso) return 'onbekende leeftijd';
  const diffMs = Date.now() - new Date(iso).getTime();
  const hours = Math.max(0, Math.round(diffMs / 3600000));
  if (hours < 1) return 'data zojuist bijgewerkt';
  if (hours < 24) return `data ${hours}u oud`;
  return `data ${Math.round(hours / 24)}d oud`;
}

function setupControls() {
  document.querySelectorAll('.energy-chip').forEach(button => {
    button.addEventListener('click', () => setEnergy(button.dataset.kwh));
  });

  document.getElementById('energy-slider').addEventListener('input', event => {
    setEnergy(event.target.value);
  });

  const search = document.getElementById('charger-search');
  search.addEventListener('input', event => {
    searchQuery = normalizeSearch(event.target.value);
    activeId = null;
    applyFilters();
  });

  document.getElementById('search-clear').addEventListener('click', () => {
    search.value = '';
    searchQuery = '';
    activeId = null;
    search.focus();
    applyFilters();
  });

  document.getElementById('reset-passes').addEventListener('click', () => {
    selectedPasses = new Set(passes.filter(pass => pass.default_selected !== false).map(pass => pass.id));
    energyKwh = DEFAULT_KWH;
    savePreferences();
    renderPassChips();
    setEnergy(DEFAULT_KWH, false);
  });

  document.getElementById('locate-btn').addEventListener('click', () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(position => {
      userLocation = [position.coords.latitude, position.coords.longitude];
      if (userMarker) map.removeLayer(userMarker);
      userMarker = L.marker(userLocation, {
        icon: L.divIcon({
          className: '',
          iconSize: [14, 14],
          iconAnchor: [7, 7],
          html: '<div class="user-dot"></div>',
        }),
      }).addTo(map).bindPopup('Mijn locatie');
      map.setView(userLocation, 15);
      applyFilters();
    }, () => {
      document.getElementById('locate-btn').textContent = 'Locatie niet beschikbaar';
    }, {
      enableHighAccuracy: true,
      timeout: 8000,
      maximumAge: 60000,
    });
  });
}

async function drawBoundary() {
  try {
    const response = await fetch('huizen-boundary.geojson');
    if (!response.ok) return;
    const geo = await response.json();
    L.geoJSON(geo, {
      style: { color: '#6b7280', weight: 1, opacity: 0.55, fillOpacity: 0 },
    }).addTo(map);
  } catch (_) {
    // The boundary is decorative only.
  }
}

async function init() {
  setupControls();

  try {
    const response = await fetch('huizen-data.json', { cache: 'no-cache' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();

    passes = ensureDirectPass(data.passes);
    points = Array.isArray(data.locations) ? data.locations : [];

    loadPreferences();
    renderPassChips();
    renderOperatorChips();
    setEnergy(energyKwh, false);

    document.getElementById('stat-total').textContent = points.length;
    document.getElementById('stat-comparable').textContent = data.stats?.comparison_ready
      ?? points.filter(point => Object.keys(point.pricing || {}).length >= 2).length;
    document.getElementById('stat-free').textContent = data.stats?.available_snapshot
      ?? points.filter(point => point.available).length;
    document.getElementById('data-age').textContent = formatAge(data.generated_at);

    const adHoc = data.stats?.adhoc_priced ?? points.filter(point => point.pricing?.direct_pay?.kwh != null).length;
    document.getElementById('footer-stats').textContent = `· ${data.stats?.ndw_priced ?? 0} NDW-tarieven · ${adHoc} ad-hoc tarieven · ${data.stats?.regional_priced ?? 0} regionale indicaties`;

    markers = points.map(pt => {
      const marker = L.marker([pt.lat, pt.lng], { icon: makeIcon(pt) })
        .bindPopup(makePopup(pt), { maxWidth: 330 });
      marker._pid = pt.id;
      marker.on('click', () => selectCardFromMarker(pt.id));
      marker.addTo(map);
      return marker;
    });

    applyFilters();
    drawBoundary();
  } catch (error) {
    console.error(error);
    document.getElementById('list').innerHTML = `<div class="empty">Kon de laadpuntdata niet laden.<br>${escapeHtml(error.message)}</div>`;
  } finally {
    document.getElementById('loader').classList.add('gone');
  }
}

init();
