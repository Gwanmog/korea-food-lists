// --- UTILS ---
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
function esc(s) {
  if (!s) return "";
  return s.toString().replace(/&/g, "&").replace(/</g, "<").replace(/>/g, ">");
}

// --- STATE ---
let allFeatures = [];
let clusterGroup = null; // Replaces simple LayerGroup
let userLoc = null;
let userMarker = null;

// --- MAP INIT ---
const map = L.map('map', { zoomControl: false }).setView([37.5665, 126.9780], 12);
L.control.zoom({ position: 'bottomright' }).addTo(map);

// Default Tiles
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '© OpenStreetMap, © CartoDB',
  subdomains: 'abcd',
  maxZoom: 20
}).addTo(map);

// Init Cluster Group
clusterGroup = L.markerClusterGroup({
  showCoverageOnHover: false,
  maxClusterRadius: 50, // Smaller radius = more clusters
  spiderfyOnMaxZoom: true,
  disableClusteringAtZoom: 17 // Stop clustering when zoomed in close
});
map.addLayer(clusterGroup);


// --- UI HANDLERS ---
$('filterToggle').onclick = () => {
  $('panel').classList.toggle('closed');
};

$('listToggle').onclick = () => {
  $('listWrap').classList.toggle('collapsed');
  $('listToggle').textContent = $('listWrap').classList.contains('collapsed') ? 'Show List' : 'Hide List';
};

$('themeBtn').onclick = () => {
  document.body.classList.toggle('dark');
  const isDark = document.body.classList.contains('dark');
  $('themeBtn').textContent = isDark ? '🌙' : '☀️';

  const url = isDark
    ? 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'
    : 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png';

  map.eachLayer(l => {
      if (l instanceof L.TileLayer) l.setUrl(url);
  });
};

$('locBtn').onclick = () => {
  if (!navigator.geolocation) return alert("Geolocation not supported");
  $('locBtn').disabled = true;
  navigator.geolocation.getCurrentPosition(
    pos => {
      const { latitude, longitude } = pos.coords;
      userLoc = { lat: latitude, lon: longitude };
      if (userMarker) userMarker.remove();
      userMarker = L.marker([latitude, longitude]).addTo(map).bindPopup("You");
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
  // Year is still shown as metadata, but filter is removed
  if (p.year) meta.push(`📅 ${esc(p.year)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  let actions = [];

  if (p.kakao_url) {
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao Map</a>`);
  } else {
    actions.push(`<a class="linkbtn kakao" href="https://map.kakao.com/link/search/${enc(p.name)}" target="_blank">Search Kakao</a>`);
  }

  actions.push(`<a class="linkbtn naver" href="${naverSearch}" target="_blank">Naver Map</a>`);
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

  // Sort (Best awards first)
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

  $('count').textContent = `${visible.length} places`;

  // 1. Clear Clusters
  clusterGroup.clearLayers();

  // 2. Create Leaflet GeoJSON layer (but don't add to map directly)
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
      // Zoom to point
      const lat = f.geometry.coordinates[1];
      const lon = f.geometry.coordinates[0];

      // We must zoom to the marker, then pop it up.
      // With clusters, we might need to zoom nicely.
      map.setView([lat, lon], 16);

      // Small delay to let cluster expand if needed
      setTimeout(() => {
        clusterGroup.eachLayer(l => {
          if (l.feature === f) l.openPopup();
        });
      }, 300);

      if (window.innerWidth < 640) $('listWrap').classList.add('collapsed');
    };
    listEl.appendChild(div);
  });
}

// --- BOOT ---
async function init() {
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