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
    placeholder: "Search name, cuisine...",
    filters: "Filters",
    source: "Source",
    award: "Award",
    showList: "Show List",
    hideList: "Hide List",
    count: "{n} places",
    myLoc: "You",
    searchKakao: "Search Kakao",
  },
  ko: {
    placeholder: "식당 이름, 요리 검색...",
    filters: "필터",
    source: "출처",
    award: "등급",
    showList: "목록 보기",
    hideList: "목록 숨기기",
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
// Added maxZoom: 20 here ↓
const map = L.map('map', { zoomControl: false, maxZoom: 20 }).setView([37.5665, 126.9780], 12);
L.control.zoom({ position: 'bottomright' }).addTo(map);

// Define Tile Layers (Google Maps)
const tiles = {
  en: 'https://mt0.google.com/vt/lyrs=m&hl=en&x={x}&y={y}&z={z}', // English Labels
  ko: 'https://mt0.google.com/vt/lyrs=m&hl=ko&x={x}&y={y}&z={z}'  // Korean Labels
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
$('filterToggle').onclick = () => {
  $('panel').classList.toggle('closed');
};

$('listToggle').onclick = () => {
  $('listWrap').classList.toggle('collapsed');
  updateListButtonText();
};

function updateListButtonText() {
  const isCollapsed = $('listWrap').classList.contains('collapsed');
  $('listToggle').textContent = isCollapsed ? I18N[currentLang].showList : I18N[currentLang].hideList;
}

// Language Toggle Handler
$('langBtn').onclick = () => {
  // Toggle State
  currentLang = currentLang === 'en' ? 'ko' : 'en';
  $('langBtn').textContent = currentLang === 'en' ? 'KR' : 'EN';

  // 1. Update Map Tiles
  setMapLanguage(currentLang);

  // 2. Update UI Text
  const t = I18N[currentLang];
  $('q').placeholder = t.placeholder;
  $('filterToggle').textContent = t.filters;

  // Update elements with data-i18n attribute
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (t[key]) el.textContent = t[key];
  });

  updateListButtonText();
  render(); // Re-render to update counts and popups
};

$('locBtn').onclick = () => {
  if (!navigator.geolocation) return alert("Geolocation not supported");
  $('locBtn').disabled = true;
  navigator.geolocation.getCurrentPosition(
    pos => {
      const { latitude, longitude } = pos.coords;
      userLoc = { lat: latitude, lon: longitude };
      if (userMarker) userMarker.remove();
      userMarker = L.marker([latitude, longitude]).addTo(map).bindPopup(I18N[currentLang].myLoc);
      map.setView([latitude, longitude], 14);
      $('locBtn').disabled = false;
      render();
    },
    err => {
      alert("Loc failed: " + err.message);
      $('locBtn').disabled = false;
    }
  );
};

const filters = {
  michelin: $('f_michelin'),
  blu: $('f_blu'),
  m3: $('f_m3'), m2: $('f_m2'), m1: $('f_m1'), bib: $('f_bib'),
  r3: $('f_r3'), r2: $('f_r2'), r1: $('f_r1'),
};

Object.values(filters).forEach(el => el.onchange = render);
$('q').oninput = render;

// --- FILTER LOGIC ---
function passes(p) {
  // 1. Source
  const src = (p.source || "").toLowerCase();
  if (src.includes("michelin") && !filters.michelin.checked) return false;
  if (src.includes("blue") && !filters.blu.checked) return false;

  // 2. Category/Award
  const cat = (p.category || "").toUpperCase();

  if (src.includes("michelin")) {
    if (cat.includes("3 STAR") && !filters.m3.checked) return false;
    if (cat.includes("2 STAR") && !filters.m2.checked) return false;
    if (cat.includes("1 STAR") && !filters.m1.checked) return false;
    if (cat.includes("BIB") && !filters.bib.checked) return false;
  }
  else if (src.includes("blue")) {
    if (cat.includes("THREE") && !filters.r3.checked) return false;
    if (cat.includes("TWO") && !filters.r2.checked) return false;
    if (cat.includes("ONE") && !filters.r1.checked) return false;
  }

  // 3. Search Text
  const q = $('q').value.trim().toLowerCase();
  if (q) {
    const hay = [p.name, p.cuisine, p.address].join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }

  return true;
}

// --- RENDERING ---
function getBadge(p) {
  const s = (p.source || "").toLowerCase();
  const c = (p.category || "").toUpperCase();
  if (s.includes("michelin")) {
    if (c.includes("3")) return "⭐ 3";
    if (c.includes("2")) return "⭐ 2";
    if (c.includes("1")) return "⭐ 1";
    if (c.includes("BIB")) return "😊 Bib";
    return "Mic";
  }
  if (s.includes("blue")) {
    if (c.includes("THREE")) return "🎀 3";
    if (c.includes("TWO")) return "🎀 2";
    if (c.includes("ONE")) return "🎀 1";
    return "Blu";
  }
  return "";
}

function renderPopup(p) {
  const naverSearch = `https://m.map.naver.com/search2/search.naver?query=${enc(p.name)}`;
  const googleSearch = `https://www.google.com/maps/search/?api=1&query=${enc(p.name + " Seoul")}`;

  let meta = [];
  if (p.cuisine) meta.push(`🍴 ${esc(p.cuisine)}`);
  if (p.price) meta.push(`💰 ${esc(p.price)}`);
  if (p.year) meta.push(`📅 ${esc(p.year)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  let actions = [];

  // Kakao Button
  if (p.kakao_url) {
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao</a>`);
  } else {
    actions.push(`<a class="linkbtn kakao" href="https://map.kakao.com/link/search/${enc(p.name)}" target="_blank">${I18N[currentLang].searchKakao}</a>`);
  }

  actions.push(`<a class="linkbtn naver" href="${naverSearch}" target="_blank">Naver</a>`);
  actions.push(`<a class="linkbtn" href="${googleSearch}" target="_blank">Google</a>`);

  return `
    <div class="popup-title">${esc(p.name)}</div>
    <div class="popup-meta">
      ${meta.join("<br>")}
      <br><span style="opacity:0.7; font-size:11px;">${esc(p.address || "")}</span>
    </div>
    <div class="popup-actions">${actions.join("")}</div>
  `;
}

function render() {
  // Filter
  const visible = allFeatures.filter(f => passes(f.properties));

  // Sort
  visible.sort((a, b) => {
    const score = p => {
      let s = 0;
      const c = (p.category || "").toUpperCase();
      if (c.includes("3 STAR") || c.includes("RIBBON_THREE")) s += 30;
      if (c.includes("2 STAR") || c.includes("RIBBON_TWO")) s += 20;
      if (c.includes("1 STAR") || c.includes("RIBBON_ONE")) s += 10;
      if (c.includes("BIB")) s += 5;
      return s;
    };
    return score(b.properties) - score(a.properties);
  });

  // Count Text
  const t = I18N[currentLang];
  $('count').textContent = t.count.replace("{n}", visible.length);

  // 1. Clear Clusters
  clusterGroup.clearLayers();

  // 2. Create Leaflet GeoJSON layer
  const geoJsonLayer = L.geoJSON({ type: "FeatureCollection", features: visible }, {
    pointToLayer: (feature, latlng) => {
      const p = feature.properties;
      const isMich = (p.source || "").includes("michelin");
      const color = isMich ? "#bd2333" : "#2b70c9";

      return L.circleMarker(latlng, {
        radius: 6,
        fillColor: color,
        color: "#fff",
        weight: 1,
        opacity: 1,
        fillOpacity: 0.8
      });
    },
    onEachFeature: (feature, layer) => {
      layer.bindPopup(renderPopup(feature.properties));
    }
  });

  // 3. Add to Cluster Group
  clusterGroup.addLayer(geoJsonLayer);

  // 4. List View Update
  const listEl = $('list');
  listEl.innerHTML = "";

  visible.slice(0, 100).forEach(f => {
    const p = f.properties;
    const div = document.createElement('div');
    div.className = 'list-item';
    div.innerHTML = `
      <div class="item-header">
        <span class="item-name">${esc(p.name)}</span>
        <span class="tag ${p.source.includes('michelin') ? 'michelin' : 'blue'}">${getBadge(p)}</span>
      </div>
      <div class="item-meta">
        ${p.cuisine ? `<span>${esc(p.cuisine)}</span>` : ''}
      </div>
    `;
    div.onclick = () => {
      const lat = f.geometry.coordinates[1];
      const lon = f.geometry.coordinates[0];
      map.setView([lat, lon], 16);
      setTimeout(() => {
        clusterGroup.eachLayer(l => {
          if (l.feature === f) l.openPopup();
        });
      }, 300);

      if (window.innerWidth < 640) {
        $('listWrap').classList.add('collapsed');
        updateListButtonText();
      }
    };
    listEl.appendChild(div);
  });
}

// --- BOOT ---
async function init() {
  setMapLanguage('en'); // Default to English tiles
  try {
    const res = await fetch('./places.geojson');
    if (!res.ok) throw new Error("Failed to load data");
    const data = await res.json();
    allFeatures = data.features || [];
    render();
  } catch (e) {
    alert("Error loading map data: " + e.message);
  }
}

init();