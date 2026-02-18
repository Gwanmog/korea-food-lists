// --- UTILS ---
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
function esc(s) {
  if (!s) return "";
  return s.toString().replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">");
}

// --- CONFIG & TRANSLATIONS ---
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
  }
};

let currentLang = 'en';

// --- STATE ---
let allFeatures = [];
let clusterGroup = null;
let userLoc = null;
let userMarker = null;
let tileLayer = null;

// --- MAP INIT ---
const map = L.map('map', {
  zoomControl: false,
  tap: false,
  closePopupOnClick: false
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


// --- UI HANDLERS (SAFETY CHECKED) ---
// We check if the element exists (e && ...) before adding listeners
// This prevents the "Frozen" crash if you haven't updated your HTML yet.

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
  michelin: $('f_michelin'),
  blu: $('f_blu'),
  m3: $('f_m3'), m2: $('f_m2'), m1: $('f_m1'), bib: $('f_bib'),
  r3: $('f_r3'), r2: $('f_r2'), r1: $('f_r1'),
};

// Safe attach: Only add listener if the checkbox actually exists
Object.values(filters).forEach(el => {
  if (el) el.onchange = render;
});

const searchInput = $('q');
if (searchInput) searchInput.oninput = render;

// --- FILTER LOGIC ---
function passes(p) {
  const src = (p.source || "").toLowerCase();

  // Safety: If filter checkbox doesn't exist, assume we shouldn't filter by it
  if (filters.michelin && src.includes("michelin") && !filters.michelin.checked) return false;
  if (filters.blu && src.includes("blue") && !filters.blu.checked) return false;

  const cat = (p.category || "").toUpperCase();
  if (src.includes("michelin")) {
    if (filters.m3 && cat.includes("3 STAR") && !filters.m3.checked) return false;
    if (filters.m2 && cat.includes("2 STAR") && !filters.m2.checked) return false;
    if (filters.m1 && cat.includes("1 STAR") && !filters.m1.checked) return false;
    if (filters.bib && cat.includes("BIB") && !filters.bib.checked) return false;
  }
  else if (src.includes("blue")) {
    if (filters.r3 && cat.includes("THREE") && !filters.r3.checked) return false;
    if (filters.r2 && cat.includes("TWO") && !filters.r2.checked) return false;
    if (filters.r1 && cat.includes("ONE") && !filters.r1.checked) return false;
  }

  if (searchInput) {
    const q = searchInput.value.trim().toLowerCase();
    if (q) {
      const hay = [p.name, p.cuisine, p.address, p.description].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
  }
  return true;
}

// --- RENDERING HELPERS ---
function getBadge(p) {
  const s = (p.source || "").toLowerCase();
  const c = (p.category || "").toUpperCase();
  if (s.includes("michelin")) {
    if (c.includes("3")) return "⭐ 3";
    if (c.includes("2")) return "⭐ 2";
    if (c.includes("1")) return "⭐ 1";
    if (c.includes("BIB")) return "😊 Bib";
    return "Mic";