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
const map = L.map('map', { zoomControl: false, maxZoom: 20 }).setView([37.5665, 126.9780], 12);
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
$('filterToggle').onclick = () => $('panel').classList.toggle('closed');

$('listToggle').onclick = () => {
  $('listWrap').classList.toggle('collapsed');
};

$('themeBtn').onclick = () => {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  $('themeBtn').textContent = isDark ? '🌙' : '☀️';
};

$('langBtn').onclick = () => {
  currentLang = currentLang === 'en' ? 'ko' : 'en';
  $('langBtn').textContent = currentLang === 'en' ? 'KR' : 'EN';
  setMapLanguage(currentLang);

  const t = I18N[currentLang];
  $('q').placeholder = t.placeholder;
  $('filterToggle').textContent = t.filters;
  $('listToggle').textContent = t.showList;

  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (t[key]) el.textContent = t[key];
  });

  render();
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

function passes(p) {
  const src = (p.source || "").toLowerCase();
  if (src.includes("michelin") && !filters.michelin.checked) return false;
  if (src.includes("blue") && !filters.blu.checked) return false;

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

  const q = $('q').value.trim().toLowerCase();
  if (q) {
    const hay = [p.name, p.cuisine, p.address, p.description].join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return true;
}

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
  // --- SMART SEARCH LINKS ---
  // Use the "Algorithmic Korean Address" if available, otherwise name
  const searchQuery = p.korean_query || p.name;

  const naverSearch = `https://map.naver.com/p/search/${enc(searchQuery)}`;
  const googleSearch = `https://www.google.com/maps/search/?api=1&query=${enc(p.name + " Seoul")}`;

  let meta = [];
  if (p.cuisine) meta.push(`🍴 ${esc(p.cuisine)}`);
  if (p.price) meta.push(`💰 ${esc(p.price)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  let actions = [];

  if (p.kakao_url && p.kakao_id) {
    // Valid ID -> Store Page
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao</a>`);
  } else {
    // No ID -> Pin Drop
    const lat = p.geometry ? p.geometry.coordinates[1] : p.latitude;
    const lon = p.geometry ? p.geometry.coordinates[0] : p.longitude;
    // We use "link/to/Name,Lat,Lon" for directions/marker
    const pinUrl = `https://map.kakao.com/link/map/${enc(p.name)},${lat},${lon}`;
    actions.push(`<a class="linkbtn kakao" href="${pinUrl}" target="_blank">${I18N[currentLang].searchKakao}</a>`);
  }

  actions.push(`<a class="linkbtn naver" href="${naverSearch}" target="_blank">Naver</a>`);
  actions.push(`<a class="linkbtn" href="${googleSearch}" target="_blank">Google</a>`);

  let descHtml = "";
  if (p.description) {
    const shortDesc = p.description.length > 300 ? p.description.substring(0, 300) + "..." : p.description;
    descHtml = `<div class="popup-desc">${esc(shortDesc)}</div>`;
  }

  return `
    <div class="popup-title">${esc(p.name)}</div>
    <div class="popup-meta">
      ${meta.join("<br>")}
    </div>
    ${descHtml}
    <div class="popup-actions">${actions.join("")}</div>
  `;
}

function render() {
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
      if (c.includes("3 STAR") || c.includes("RIBBON_THREE")) s += 30;
      if (c.includes("2 STAR") || c.includes("RIBBON_TWO")) s += 20;
      if (c.includes("1 STAR") || c.includes("RIBBON_ONE")) s += 10;
      if (c.includes("BIB")) s += 5;
      return s;
    };
    return score(b.properties) - score(a.properties);
  });

  const t = I18N[currentLang];
  $('count').textContent = t.count.replace("{n}", visible.length);

  clusterGroup.clearLayers();

  // 1. Create Layers & Store References
  const layerMap = new Map(); // Link Feature -> Leaflet Layer

  const geoJsonLayer = L.geoJSON({ type: "FeatureCollection", features: visible }, {
    pointToLayer: (feature, latlng) => {
      const p = feature.properties;
      const isMich = (p.source || "").includes("michelin");
      const color = isMich ? "#bd2333" : "#2b70c9";

      const marker = L.circleMarker(latlng, {
        radius: 6,
        fillColor: color,
        color: "#fff",
        weight: 1,
        opacity: 1,
        fillOpacity: 0.8
      });

      // Save reference for hover effect
      feature.layer = marker;
      return marker;
    },
    onEachFeature: (feature, layer) => {
      layer.bindPopup(renderPopup(feature.properties));
    }
  });

  clusterGroup.addLayer(geoJsonLayer);

  // 2. Build List with Hover Logic
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

    // --- HOVER EFFECT ---
    div.onmouseenter = () => {
      if (f.layer) {
        f.layer.setStyle({ radius: 12, color: '#ffff00', weight: 3, fillOpacity: 1 });
        f.layer.bringToFront();
      }
    };
    div.onmouseleave = () => {
      if (f.layer) {
        f.layer.setStyle({ radius: 6, color: '#fff', weight: 1, fillOpacity: 0.8 });
      }
    };
    // --------------------

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
      }
    };
    listEl.appendChild(div);
  });
}

map.on('moveend', render);

async function init() {
  setMapLanguage('en');
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