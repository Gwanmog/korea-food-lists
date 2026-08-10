import { logSearchResults, logPinHover, logPinClick } from './interactionLogger.js';
import * as authManager from './authManager.js';
import * as favoritesManager from './favoritesManager.js';
// --- UTILS ---
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
function esc(s) {
  if (!s) return "";
  return s.toString().replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">");
}

// --- CONFIG & TRANSLATIONS ---
// --- DYNAMIC BACKEND CONFIG ---
const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
const API_BASE_URL = isLocal ? 'http://localhost:3000' : 'https://eatmyseoul.onrender.com';
const I18N = {
  en: {
    placeholder: "Search name...",
    filters: "Filters",
    source: "Source",
    award: "Award",
    showList: "List",
    hideList: "Hide List",
    count: "{n} places",
    myLoc: "You",
    searchKakao: "Search Kakao",
    selectAll: "☑ All",
    deselectAll: "☐ None",
    readMore: "Read more ▼",
    showLess: "Show less ▲",
    translating: "Translating... ⏳",
    srcMichelin: "Michelin",
    srcBlu: "Blue Ribbon",
    srcNeon: "Neon Guide",
    tBib: "Bib",
    tSelected: "Selected",
    tVetted: "Vetted",
    bBib: "😊 Bib",
    bMic: "Michelin",
    bBlu: "Blue Ribbon",
    bVetted: "✅ Vetted",
    bNeon: "Neon",
  },
  ko: {
    placeholder: "식당 검색...",
    filters: "필터",
    source: "출처",
    award: "등급",
    showList: "목록",
    hideList: "목록 닫기",
    count: "{n}곳 발견",
    myLoc: "내 위치",
    searchKakao: "카카오맵 검색",
    selectAll: "☑ 전체",
    deselectAll: "☐ 해제",
    readMore: "더 보기 ▼",
    showLess: "접기 ▲",
    translating: "번역 중... ⏳",
    srcMichelin: "미쉐린",
    srcBlu: "블루리본",
    srcNeon: "네온 가이드",
    tBib: "빕구르망",
    tSelected: "셀렉티드",
    tVetted: "검증됨",
    bBib: "😊 빕구르망",
    bMic: "미쉐린",
    bBlu: "블루리본",
    bVetted: "✅ 검증됨",
    bNeon: "네온",
  }
};

let currentLang = 'en';

// --- STATE ---
window.favoritesOnly = false;
let allFeatures = [];
let clusterGroup = null;
let userLoc = null;
let userMarker = null;
let tileLayer = null;
// Persisted translation cache — survives page reloads
const TRANSLATION_CACHE_KEY = 'ems_translation_cache_v1';
const translationCache = (() => {
  try { return JSON.parse(localStorage.getItem(TRANSLATION_CACHE_KEY)) || {}; }
  catch { return {}; }
})();
function saveTranslationCache() {
  try { localStorage.setItem(TRANSLATION_CACHE_KEY, JSON.stringify(translationCache)); }
  catch { /* storage full — no-op, in-memory cache still works */ }
}

// --- MAP INIT ---
const map = L.map('map', {
  zoomControl: false,
  tap: false,
  closePopupOnClick: false,
  maxZoom: 20
}).setView([37.5665, 126.9780], 11);

L.control.zoom({ position: 'bottomright' }).addTo(map);

const tiles = {
  en: 'https://mt0.google.com/vt/lyrs=m&hl=en&x={x}&y={y}&z={z}',
  ko: 'https://mt0.google.com/vt/lyrs=m&hl=ko&x={x}&y={y}&z={z}'
};

function setMapLanguage(lang) {
  if (tileLayer) tileLayer.remove();
  tileLayer = L.tileLayer(tiles[lang], {
    attribution: 'Map data © Google',
    maxZoom: 20
  }).addTo(map);
}

// Init Cluster Group
clusterGroup = L.markerClusterGroup({
  showCoverageOnHover: false,
  maxClusterRadius: 50,
  spiderfyOnMaxZoom: true,
  disableClusteringAtZoom: 17
});
map.addLayer(clusterGroup);

// --- UI HANDLERS ---
const filterToggle = $('filterToggle');
if (filterToggle) filterToggle.onclick = () => $('panel').classList.toggle('closed');

const listToggle = $('listToggle');
if (listToggle) listToggle.onclick = () => $('listWrap').classList.toggle('collapsed');

const themeBtn = $('themeBtn');
if (themeBtn) themeBtn.onclick = () => {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  themeBtn.textContent = isDark ? '🌙' : '☀️';
};

const langBtn = $('langBtn');
if (langBtn) langBtn.onclick = () => {
  currentLang = currentLang === 'en' ? 'ko' : 'en';
  langBtn.textContent = currentLang === 'en' ? 'KR' : 'EN';
  setMapLanguage(currentLang);

  const t = I18N[currentLang];
  if ($('q')) $('q').placeholder = t.placeholder;
  if (filterToggle) filterToggle.textContent = t.filters;
  if (listToggle) listToggle.textContent = t.showList;

  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (t[key]) el.textContent = t[key];
  });

  updateSelectAllBtn();
  render();
};

const locBtn = $('locBtn');
if (locBtn) locBtn.onclick = () => {
  if (!navigator.geolocation) return alert("Geolocation not supported");
  locBtn.disabled = true;
  navigator.geolocation.getCurrentPosition(
    pos => {
      const { latitude, longitude } = pos.coords;
      userLoc = { lat: latitude, lon: longitude };
      if (userMarker) userMarker.remove();
      userMarker = L.marker([latitude, longitude]).addTo(map).bindPopup(I18N[currentLang].myLoc);
      map.setView([latitude, longitude], 14);
      locBtn.disabled = false;
      render();
    },
    err => {
      alert("Loc failed: " + err.message);
      locBtn.disabled = false;
    }
  );
};

// --- FILTERS ---
const filters = {
  michelin: $('f_michelin'), blu: $('f_blu'), neon: $('f_neon'),
  m3: $('f_m3'), m2: $('f_m2'), m1: $('f_m1'), bib: $('f_bib'), m_sel: $('f_m_sel'),
  r3: $('f_r3'), r2: $('f_r2'), r1: $('f_r1'),
  n3: $('f_n3'), n2: $('f_n2'), n1: $('f_n1'), n_vetted: $('f_n_vetted')
};

const filterChildren = {
  michelin: ['m3', 'm2', 'm1', 'bib', 'm_sel'],
  blu: ['r3', 'r2', 'r1'],
  neon: ['n3', 'n2', 'n1', 'n_vetted']
};

// Complete tier -> pill mapping for every guide.
//
// These have to be EXHAUSTIVE. The filters used to work by exclusion ("hide this if its
// pill is off"), which silently let through any tier that matched none of the patterns —
// and "Selected" had no pill at all, so 108 of 200 Michelin restaurants could never be
// filtered. Ticking only ⭐⭐⭐ still showed 57 places. Matching is now inclusive: a pin
// shows only if its own tier's pill is ticked.
const TIER_FILTER = {
  michelin: {
    '3 STARS': 'm3',
    '2 STARS': 'm2',
    '1 STAR': 'm1',
    'BIB GOURMAND': 'bib',
    'SELECTED': 'm_sel'
  },
  blueribbon: {
    'RIBBON_THREE': 'r3',
    'RIBBON_TWO': 'r2',
    'RIBBON_ONE': 'r1'
  },
  neon: {
    'NEON_3': 'n3',
    'NEON_2': 'n2',
    'NEON_1': 'n1',
    'NEON_VETTED': 'n_vetted'
  }
};

function handleFilterChange(key) {
  // Parent toggled → cascade to children
  if (filterChildren[key]) {
    const state = filters[key].checked;
    filterChildren[key].forEach(childKey => {
      if (filters[childKey]) filters[childKey].checked = state;
    });
  }
  // Child toggled → the parent simply reflects "is ANY tier of this guide showing?".
  // It used to only re-check when EVERY child was checked, so unchecking all tiers
  // switched the source off and checking one tier back on left the source stuck off —
  // and since passes() gates on the source, the map went blank until you re-ticked
  // every tier. Same rule in both directions removes the stickiness.
  for (const [parentKey, children] of Object.entries(filterChildren)) {
    if (children.includes(key) && filters[parentKey]) {
      filters[parentKey].checked = children.some(k => filters[k]?.checked);
    }
  }
  updateSelectAllBtn();
  render();
}

Object.entries(filters).forEach(([key, el]) => {
  if (el) el.onchange = () => handleFilterChange(key);
});

function updateSelectAllBtn() {
  const btn = $('selectAllBtn');
  if (!btn) return;
  const t = I18N[currentLang];
  const allChecked = Object.values(filters).every(el => !el || el.checked);
  btn.textContent = allChecked ? t.deselectAll : t.selectAll;
}

const selectAllBtn = $('selectAllBtn');
if (selectAllBtn) {
  selectAllBtn.onclick = () => {
    const anyUnchecked = Object.values(filters).some(el => el && !el.checked);
    Object.values(filters).forEach(el => { if (el) el.checked = anyUnchecked; });
    updateSelectAllBtn();
    render();
  };
}

// --- FILTER LOGIC ---
function passes(p) {
  const src = (p.source || "").toLowerCase();
  const cat = (p.category || "").toUpperCase();

  // NOTE: source visibility is decided inside guideVisible() below, not up front.
  // Rejecting a pin because ONE of its guides is unticked was wrong for merged pins:
  // unticking Blue Ribbon also hid all 92 Michelin+Blue Ribbon restaurants, even though
  // they are still Michelin restaurants and Michelin was ticked.
  const awards = Array.isArray(p.awards) ? p.awards : [];
  const tierOf = guide => {
    const a = awards.find(x => x.guide === guide);
    return (a && a.tier ? a.tier : "").trim().toUpperCase();
  };

  // A pin is visible if ANY guide it holds is showing at a ticked tier. Checked
  // per-guide (not as an if/else chain) so a pin carrying several guides' awards
  // honours every one of their filters.
  let anyGuideVisible = false;

  const guideVisible = (guideKey, sourceMatch, tierValue) => {
    if (!sourceMatch) return;
    if (filters[guideKey] && !filters[guideKey].checked) return;
    const pillKey = TIER_FILTER[guideKey === 'blu' ? 'blueribbon' : guideKey][tierValue];
    if (!pillKey) {
      // Unmapped tier: show it rather than hiding data silently, but it means
      // TIER_FILTER is missing an entry — add it rather than relying on this.
      anyGuideVisible = true;
      return;
    }
    if (filters[pillKey] && !filters[pillKey].checked) return;
    anyGuideVisible = true;
  };

  guideVisible('michelin', src.includes('michelin'), tierOf('michelin') || cat);
  guideVisible('blu', src.includes('blue'), tierOf('blueribbon') || cat);
  guideVisible('neon', src.includes('neon'), (p.tier || '').toUpperCase());

  if (!anyGuideVisible) return false;

  if (window.favoritesOnly && !favoritesManager.isFavorited(p.vector_id)) return false;

  // Use the global state from the Omnibox instead of the old DOM element!
  if (window.currentSearchQuery) {
    const hay = [p.name, p.cuisine, p.address, p.description].join(" ").toLowerCase();
    if (!hay.includes(window.currentSearchQuery)) return false;
  }
  return true;
}

// --- RENDERING HELPERS ---
function getBadge(p) {
  const s = (p.source || "").toLowerCase();
  const c = (p.category || "").toUpperCase();
  const d = (p.description || "");

  const t = I18N[currentLang];

  if (s.includes("michelin")) {
    if (c.includes("3")) return "⭐ 3";
    if (c.includes("2")) return "⭐ 2";
    if (c.includes("1")) return "⭐ 1";
    if (c.includes("BIB")) return t.bBib;
    return t.bMic;
  }
  if (s.includes("blue")) {
    if (c.includes("THREE")) return "🎀 3";
    if (c.includes("TWO")) return "🎀 2";
    if (c.includes("ONE")) return "🎀 1";
    return t.bBlu;
  }
  if (s.includes("neon")) {
    if (p.tier === "NEON_3") return "💖 3";
    if (p.tier === "NEON_2") return "💖 2";
    if (p.tier === "NEON_1") return "💖 1";
    if (p.tier === "NEON_VETTED") return t.bVetted;
    return t.bNeon;
  }
  return "";
}

function renderPopup(p) {
  const searchQuery = p.name_ko || p.korean_query || p.name;
  const naverSearch = `https://map.naver.com/p/search/${enc(searchQuery)}`;
  const googleSearch = `https://www.google.com/maps/search/${enc(p.name + " Seoul")}`;

  let meta = [];
  // Cuisine in the reader's language. Sources disagree on language, so this line used
  // to be mixed English/Korean for everyone regardless of the toggle.
  const cuisineLabel = (currentLang === 'ko')
    ? (p.cuisine_ko || p.cuisine)
    : (p.cuisine || p.cuisine_ko);
  if (cuisineLabel) meta.push(`🍴 ${esc(cuisineLabel)}`);
  if (p.price) meta.push(`💰 ${esc(p.price)}`);
  // The address was never shown in the popup at all. Prefer the reader's language,
  // fall back to whichever we have.
  const addr = (currentLang === 'ko')
    ? (p.address_ko || p.address)
    : (p.address || p.address_ko);
  if (addr) meta.push(`📍 ${esc(addr)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  // Save + Share buttons
  const vectorId = p.vector_id;
  const isSaved = vectorId !== undefined ? favoritesManager.isFavorited(vectorId) : false;
  const saveBtn = vectorId !== undefined
    ? `<button class="linkbtn save-btn${isSaved ? ' saved' : ''}" onclick="window.toggleFavorite(${vectorId}, this)">${isSaved ? '❤️ Saved' : '🤍 Save'}</button>`
    : '';
  const shareBtn = vectorId !== undefined
    ? `<button class="linkbtn share-btn" onclick="window.shareRestaurant(${vectorId}, '${esc(p.name).replace(/'/g, "\\'")}')">🔗 Share</button>`
    : '';

  let actions = [];
  if (p.kakao_url && p.kakao_id) {
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao</a>`);
  } else {
    const searchUrl = `https://map.kakao.com/link/search/${enc(searchQuery)}`;
    actions.push(`<a class="linkbtn kakao" href="${searchUrl}" target="_blank">${I18N[currentLang].searchKakao}</a>`);
  }
  actions.push(`<a class="linkbtn naver" href="${naverSearch}" target="_blank">Naver</a>`);
  actions.push(`<a class="linkbtn" href="${googleSearch}" target="_blank">Google</a>`);

  let descHtml = "";
  if (p.description) {
    // Every restaurant already ships a Korean description in places.geojson. We used to
    // ignore it in KR mode and fetch the same string back over the network — a round trip
    // for text the browser already had. Use the local field; only fall back to /translate
    // for the rare entry that genuinely lacks one.
    const wantKo = currentLang === 'ko';
    const localKo = (p.description_ko || "").trim();
    const needsFetch = wantKo && !localKo;
    const fullText = (wantKo && localKo) ? localKo : p.description;

    const isLong = fullText.length > 300;
    const shortText = isLong ? fullText.substring(0, 300) + "..." : fullText;
    // Escape for use inside HTML attribute (also escape quotes)
    const attrEsc = s => esc(s).replace(/"/g, '&quot;');
    const t = I18N[currentLang];

    if (needsFetch) {
      descHtml = `<div class="popup-desc" data-needs-translate="1" data-en="${attrEsc(p.description)}" data-name="${attrEsc(p.name)}">
        <span class="desc-text desc-loading">${t.translating}</span>
      </div>`;
    } else {
      // Expand/collapse now works in Korean too — the KR branch previously had no expand
      // button at all, and toggleDesc would have expanded to the English text.
      descHtml = `<div class="popup-desc" data-en="${attrEsc(fullText)}" data-name="${attrEsc(p.name)}">
        <span class="desc-text">${esc(shortText)}</span>
        ${isLong ? `<button class="desc-expand-btn" onclick="window.toggleDesc(this)">${t.readMore}</button>` : ''}
      </div>`;
    }
  }

  // Lead with the name in the language the user is actually reading. 100% of Michelin and
  // ~50% of Blue Ribbon names are romanised, so a Korean user was reading "Bongsanok" as
  // the headline with 봉산옥 demoted to a grey subtitle.
  const primaryName = (currentLang === 'ko' && p.name_ko) ? p.name_ko : p.name;
  const secondaryName = (currentLang === 'ko' && p.name_ko) ? p.name : p.name_ko;
  let titleHtml = `<div class="popup-title">${esc(primaryName)}</div>`;
  if (secondaryName && secondaryName !== primaryName) {
    titleHtml += `<div style="font-size:12px; color:#888; margin-top:-4px; margin-bottom:6px;">${esc(secondaryName)}</div>`;
  }

  return `
    ${titleHtml}
    <div class="popup-meta">
      ${meta.join("<br>")}
    </div>
    ${descHtml}
    <div class="popup-actions">
      ${saveBtn}${shareBtn}
      ${actions.join("")}
    </div>
  `;
}

// --- MAIN RENDER LOOP ---
function render() {
  // 🚨 Prevent the auto-pan from destroying the open popup!
  if (document.querySelector('.leaflet-popup')) return;

  const bounds = map.getBounds();

  const visible = allFeatures.filter(f => {
    const lat = f.geometry.coordinates[1];
    const lon = f.geometry.coordinates[0];
    const inView = bounds.contains([lat, lon]);
    return inView && passes(f.properties);
  });

  visible.sort((a, b) => {
    const score = p => {
      let s = 0;
      const c = (p.category || "").toUpperCase();
      if (c.includes("3 STAR") || c.includes("RIBBON_THREE") || p.tier === "NEON_3") s += 30;
      if (c.includes("2 STAR") || c.includes("RIBBON_TWO") || p.tier === "NEON_2") s += 20;
      if (c.includes("1 STAR") || c.includes("RIBBON_ONE") || p.tier === "NEON_1") s += 10;
      if (c.includes("BIB")) s += 5;
      return s;
    };
    return score(b.properties) - score(a.properties);
  });

  const t = I18N[currentLang];
  const countEl = $('count');
  if (countEl) countEl.textContent = t.count.replace("{n}", visible.length);

  clusterGroup.clearLayers();

  const geoJsonLayer = L.geoJSON({ type: "FeatureCollection", features: visible }, {
    pointToLayer: (feature, latlng) => {
      const p = feature.properties;
      const isMich = (p.source || "").includes("michelin");
      const isNeon = (p.source || "").includes("neon");

      const color = isNeon ? "#facc15" : (isMich ? "#bd2333" : "#2b70c9");
      const isMobile = window.innerWidth < 640;
      const baseRadius = isMobile ? 10 : 6;
      const neonRadius = isMobile ? 13 : 8;

      const marker = L.circleMarker(latlng, {
        radius: isNeon ? neonRadius : baseRadius,
        fillColor: color,
        color: isNeon ? "#000000" : "#ffffff",
        weight: isNeon ? 2 : 1.5, // Slightly thicker border makes it easier to tap
        opacity: 1,
        fillOpacity: 0.9
      });
      feature.layer = marker;
      // --- DEEPFM: TRACK PIN HOVERS AND CLICKS ---
      marker.on('mouseover', () => {
        if (p.vector_id !== undefined) logPinHover(p.vector_id);
      });

      marker.on('click', () => {
        if (p.vector_id !== undefined) logPinClick(p.vector_id);
      });
      // -------------------------------------------

      return marker;
    },
    onEachFeature: (feature, layer) => {
      layer.bindPopup(renderPopup(feature.properties));
    }
  });

  clusterGroup.addLayer(geoJsonLayer);

  const listEl = $('list');
  if (listEl) {
    listEl.innerHTML = "";

    visible.slice(0, 100).forEach(f => {
      const p = f.properties;

      // Determine sidebar badge color
      let tagClass = 'blue';
      if (p.source.includes('michelin')) tagClass = 'michelin';
      else if (p.source.includes('neon')) tagClass = 'neon';

      const div = document.createElement('div');
      div.className = 'list-item';
      div.innerHTML = `
        <div class="item-header">
          <span class="item-name">${esc(p.name)}</span>
          <span class="tag ${tagClass}">${getBadge(p)}</span>
        </div>
        <div class="item-meta">
          ${p.cuisine ? `<span>${esc(p.cuisine)}</span>` : ''}
        </div>
      `;

      div.onmouseenter = () => {
        if (f.layer) {
          f.layer.setStyle({ radius: 12, color: '#ffff00', weight: 3, fillOpacity: 1 });
          f.layer.bringToFront();
        }
      };
      div.onmouseleave = () => {
        if (f.layer) {
          const isNeon = (p.source || "").includes("neon");
          f.layer.setStyle({
              radius: isNeon ? 8 : 6,
              color: isNeon ? '#000' : '#fff',
              weight: isNeon ? 2 : 1,
              fillOpacity: 0.9
          });
        }
      };

// CLICK HANDLER
      div.onclick = () => {
        // --- DEEPFM: TRACK THE BATCH (1 Click, 99 Zeros) ---
        if (p.vector_id !== undefined) {
          // Grab all vector IDs currently visible in the sidebar
          const allVisibleIds = visible
            .slice(0, 100)
            .map(item => item.properties.vector_id)
            .filter(id => id !== undefined);

          // Fire the batch log!
          logSearchResults(
            allVisibleIds,           // The Sea of Zeros
            p.vector_id,             // The chosen one
            window.currentSearchQuery // The active filter (if any)
          );
        }
        // ---------------------------------------------------

        window.openRestaurantPopup(p.name);
        if (window.innerWidth < 640 && $('listWrap')) {
          $('listWrap').classList.add('collapsed');
        }
      };

      listEl.appendChild(div);
    });
  }
}

map.on('moveend', render);
map.on('popupclose', render);

// --- POPUP TRANSLATION (KR mode) ---
map.on('popupopen', async (e) => {
  if (currentLang !== 'ko') return;
  const popupEl = e.popup.getElement();
  if (!popupEl) return;
  const descEl = popupEl.querySelector('.popup-desc');
  if (!descEl) return;
  // Rendered straight from places.geojson's description_ko — nothing to fetch.
  if (descEl.getAttribute('data-needs-translate') !== '1') return;
  const englishText = descEl.getAttribute('data-en');
  const restaurantName = descEl.getAttribute('data-name');
  if (!englishText) return;
  const textEl = descEl.querySelector('.desc-text');
  if (!textEl) return;

  if (translationCache[restaurantName]) {
    textEl.textContent = translationCache[restaurantName];
    textEl.classList.remove('desc-loading');
    return;
  }

  try {
    const response = await fetch(`${API_BASE_URL}/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: englishText })
    });
    const data = await response.json();
    translationCache[restaurantName] = data.translated;
    saveTranslationCache();
    textEl.textContent = data.translated;
    textEl.classList.remove('desc-loading');
  } catch {
    textEl.textContent = englishText;
    textEl.classList.remove('desc-loading');
  }
});

// --- EXPAND/COLLAPSE POPUP DESCRIPTION ---
window.toggleDesc = function(btn) {
  const descEl = btn.closest('.popup-desc');
  const textEl = descEl.querySelector('.desc-text');
  const fullText = descEl.getAttribute('data-en');
  const t = I18N[currentLang];
  if (btn.dataset.expanded === '1') {
    textEl.textContent = fullText.substring(0, 300) + "...";
    btn.textContent = t.readMore;
    btn.dataset.expanded = '0';
  } else {
    textEl.textContent = fullText;
    btn.textContent = t.showLess;
    btn.dataset.expanded = '1';
  }
};

// --- TOAST ---
function showToast(message) {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('visible'));
  setTimeout(() => {
    toast.classList.remove('visible');
    setTimeout(() => toast.remove(), 300);
  }, 2200);
}

// --- SHARE (called from popup button) ---
window.shareRestaurant = function(vectorId, name) {
  const url = `${window.location.origin}${window.location.pathname}?place=${vectorId}`;
  if (navigator.share) {
    navigator.share({ title: name, text: `Check out ${name} on Eat My Seoul`, url }).catch(() => {});
  } else {
    navigator.clipboard.writeText(url)
      .then(() => showToast('Link copied to clipboard!'))
      .catch(() => prompt('Copy this link:', url));
  }
};

// --- FAVOURITES TOGGLE (called from popup button) ---
window.toggleFavorite = async function(vectorId, btn) {
  const user = authManager.getUser();
  if (!user) {
    authManager.showLoginModal();
    return;
  }

  btn.disabled = true;
  const saved = await favoritesManager.toggleFavorite(user.id, vectorId);
  btn.textContent = saved ? '❤️ Saved' : '🤍 Save';
  btn.classList.toggle('saved', saved);
  btn.disabled = false;

  // If we're in favourites-only mode and just unsaved, re-render
  if (window.favoritesOnly && !saved) render();
};

// --- FAVOURITES FILTER BUTTON ---
const favBtn = $('favBtn');
if (favBtn) {
  favBtn.addEventListener('click', () => {
    window.favoritesOnly = !window.favoritesOnly;
    favBtn.textContent = window.favoritesOnly ? '❤️' : '🤍';
    favBtn.classList.toggle('active', window.favoritesOnly);
    render();
  });
}

// --- INIT ---
async function init() {
  setMapLanguage('en');

  // Auth: init and listen for login/logout
  authManager.init();
  authManager.onAuthStateChange(async (user) => {
    if (user) {
      await favoritesManager.load(user.id);
    } else {
      favoritesManager.clear();
      // If we were in favourites-only mode, exit it on logout
      if (window.favoritesOnly) {
        window.favoritesOnly = false;
        const fb = $('favBtn');
        if (fb) { fb.textContent = '🤍'; fb.classList.remove('active'); }
      }
    }
    render();
  });

  try {
    const res = await fetch('./places.geojson');
    if (!res.ok) throw new Error("Failed to load data");
    const data = await res.json();
    allFeatures = data.features || [];
    render();

    // Deep-link: ?place=vectorId opens that restaurant's popup on load
    const placeId = new URLSearchParams(window.location.search).get('place');
    if (placeId) {
      const feature = allFeatures.find(f => String(f.properties.vector_id) === placeId);
      if (feature) {
        window.history.replaceState({}, '', window.location.pathname);
        window.openRestaurantPopup(feature.properties.name);
      }
    }
  } catch (e) {
    console.error(e);
    alert("Error loading map data: " + e.message);
  }
}
init();

// --- THE OMNIBOX HYBRID ROUTER ---
const omniInput = $('omniInput');
const omniSend = $('omniSend');
const omniClose = $('omniClose');
const omnibox = $('omnibox');
const omniChat = $('omniChat');
const omniMessages = $('omniMessages');
const omniFilterTag = $('omniFilterTag');

// Add a global state for the local keyword filter
window.currentSearchQuery = "";

// Show an active-filter pill in the input row for keyword searches.
// Clicking its × clears the filter without touching chat history.
function showFilterTag(text) {
  omniFilterTag.innerHTML = '';
  const label = document.createElement('span');
  label.className = 'tag-text';
  label.textContent = text;
  const clear = document.createElement('button');
  clear.className = 'tag-clear';
  clear.textContent = '×';
  clear.title = 'Clear filter';
  clear.addEventListener('click', clearOmniFilter);
  omniFilterTag.appendChild(label);
  omniFilterTag.appendChild(clear);
  omniFilterTag.classList.remove('hidden');
}

function clearOmniFilter() {
  window.currentSearchQuery = "";
  omniFilterTag.innerHTML = '';
  omniFilterTag.classList.add('hidden');
  render();
}

// UI Toggles

// Re-expand chat on focus only if there is AI conversation history.
// Keyword-only users don't need the chat panel to pop up every time.
omniInput.addEventListener('focus', () => {
  const hasAIHistory = omniMessages.children.length > 1;
  if (hasAIHistory) {
    omnibox.classList.add('expanded');
    omniChat.classList.remove('hidden');
    omniClose.classList.remove('hidden');
  }
});

// Minimize only — preserve filter state and chat history.
function minimizeOmni() {
  omnibox.classList.remove('expanded');
  omniChat.classList.add('hidden');
  omniClose.classList.add('hidden');
}

omniClose.addEventListener('click', minimizeOmni);

// Clicking anywhere outside the omnibox minimizes it.
document.addEventListener('click', (e) => {
  if (omnibox.classList.contains('expanded') && !omnibox.contains(e.target)) {
    minimizeOmni();
  }
});

function addOmniMessage(text, type) {
  const div = document.createElement('div');
  div.className = `message ${type}`;

  // Parse links
  let formatted = text.replace(/\n/g, '<br>');
  formatted = formatted.replace(/\[\[(.*?)\]\]/g, (match, name) => {
    // Escaping the name is crucial so names like "O'reilly" don't break the JS string
    const safeName = name.replace(/'/g, "\\'");
    return `<span class="chat-link" onclick="window.openRestaurantPopup('${safeName}')">${name}</span>`;
  });

  div.innerHTML = formatted;
  omniMessages.appendChild(div);
  omniMessages.scrollTop = omniMessages.scrollHeight;
  return div;
}

async function handleOmniSubmit() {
  const text = omniInput.value.trim();
  if (!text) return;

  omniInput.value = '';

  const isSimpleKeyword = text.split(' ').length <= 2 && !text.includes('?');

  if (isSimpleKeyword) {
    // Keyword filter: silently filter the map and show an active-filter pill.
    // Don't touch chat history so the AI conversation stays clean.
    window.currentSearchQuery = text.toLowerCase();
    render();
    showFilterTag(text);
    omnibox.classList.remove('expanded');
    omniChat.classList.add('hidden');
    omniClose.classList.add('hidden');
  } else {
    // AI mode: log user message, clear any active keyword filter, expand chat.
    addOmniMessage(text, 'user');
    clearOmniFilter();
    render();
    omnibox.classList.add('expanded');
    omniChat.classList.remove('hidden');
    omniClose.classList.remove('hidden');

    const loadingMsg = addOmniMessage('Scanning restaurants in your current view...', 'ai loading');

    // 🚨 THE FIX: Get the current map window coordinates
    const bounds = map.getBounds();
    const mapWindow = {
      north: bounds.getNorth(),
      south: bounds.getSouth(),
      east: bounds.getEast(),
      west: bounds.getWest()
    };

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Send the map window coordinates along with the query
        body: JSON.stringify({
          userQuery: text,
          language: currentLang,
          mapWindow: mapWindow
        })
      });

      const data = await response.json();
      loadingMsg.remove();

      if (data.reply) {
        addOmniMessage(data.reply, 'ai');
      } else {
        addOmniMessage("Sorry, I couldn't find anything in this area.", 'ai');
      }
    } catch (err) {
      loadingMsg.remove();
      addOmniMessage("Error connecting to the AI brain.", 'ai');
    }
  }
}

omniSend.addEventListener('click', handleOmniSubmit);
omniInput.addEventListener('keypress', (e) => {
  if (e.key === 'Enter') handleOmniSubmit();
});
window.openRestaurantPopup = function(name) {
    if (!name) return;

    const normalize = s => s ? s.toLowerCase().replace(/[\s\-·•]+/g, '') : '';
    const needle = normalize(name);
    const feature = allFeatures.find(f => {
        const p = f.properties;
        // 1. Exact match
        if (p.name === name || p.name_ko === name) return true;
        // 2. Case-insensitive
        if (p.name?.toLowerCase() === name.toLowerCase()) return true;
        // 3. Normalized (ignore spaces/hyphens)
        if (normalize(p.name) === needle || normalize(p.name_ko) === needle) return true;
        // 4. One contains the other (handles truncation or extra words)
        if (p.name && (p.name.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(p.name.toLowerCase()))) return true;
        return false;
    });

    if (!feature) {
        console.warn(`Could not find restaurant: ${name}`);
        return;
    }

    const [lon, lat] = feature.geometry.coordinates;

    // 1. Minimize the chat so the map is visible. Don't clear filter state.
    if (omnibox)   omnibox.classList.remove('expanded');
    if (omniChat)  omniChat.classList.add('hidden');
    if (omniClose) omniClose.classList.add('hidden');
    if (window.innerWidth < 640 && $('listWrap')) {
        $('listWrap').classList.add('collapsed');
    }

    // 2. Wait for the fly animation to finish, then open the popup.
    //    render() fires first (registered earlier), so the marker is in the
    //    cluster by the time our once-listener runs.
    map.once('moveend', () => {
        clusterGroup.eachLayer(layer => {
            if (layer.feature && (
                layer.feature.properties.name === name ||
                layer.feature.properties.name_ko === name
            )) {
                layer.openPopup();
            }
        });
    });

    // 3. Fly to the restaurant (triggers moveend when done)
    map.flyTo([lat, lon], 17, { animate: true, duration: 1.5 });
};