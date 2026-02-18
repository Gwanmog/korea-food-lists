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

// --- FILTER LOGIC ---
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
  // PRIORITY: Official Korean Name > Algorithmic Query > English Name
  const searchQuery = p.name_ko || p.korean_query || p.name;
  
  const naverSearch = `https://map.naver.com/p/search/${enc(searchQuery)}`;
  // Fixed Google Link:
  const googleSearch = `https://www.google.com/maps/search/?api=1&query=${enc(p.name + " Seoul")}`;

  let meta = [];
  if (p.cuisine) meta.push(`🍴 ${esc(p.cuisine)}`);
  if (p.price) meta.push(`💰 ${esc(p.price)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  let actions = [];
  
  if (p.kakao_url && p.kakao_id) {
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao</a>`);
  } else {
    // If no ID, search by the Korean Name (best chance of finding it)
    const searchUrl = `https://map.kakao.com/link/search/${enc(searchQuery)}`;
    actions.push(`<a class="linkbtn kakao" href="${searchUrl}" target="_blank">${I18N[currentLang].searchKakao}</a>`);
  }

  actions.push(`<a class="linkbtn naver" href="${naverSearch}" target="_blank">Naver</a>`);
  actions.push(`<a class="linkbtn" href="${googleSearch}" target="_blank">Google</a>`);

  let descHtml = "";
  if (p.description) {
    const shortDesc = p.description.length > 300 ? p.description.substring(0, 300) + "..." : p.description;
    descHtml = `<div class="popup-desc">${esc(shortDesc)}</div>`;
  }

  // Show Korean Name in title if available
  let titleHtml = `<div class="popup-title">${esc(p.name)}</div>`;
  if (p.name_ko && p.name_ko !== p.name) {
    titleHtml += `<div style="font-size:12px; color:#888; margin-top:-4px; margin-bottom:6px;">${esc(p.name_ko)}</div>`;
  }

  return `
    ${titleHtml}
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

  // Create Layers & Store References
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

  // Build List with Hover Logic
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
    
    // Hover Logic
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

// Re-render when map moves
map.on('moveend', render);

// Boot
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
// --- CHATBOT LOGIC ---
const chatWindow = document.getElementById('chatWindow');
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const chatSendBtn = document.getElementById('chatSend');
const chatToggleBtn = document.getElementById('chatToggle');
const chatCloseBtn = document.getElementById('chatClose');

// 1. Toggle Chat Window
if (chatToggleBtn) {
  chatToggleBtn.onclick = () => {
    chatWindow.classList.toggle('hidden');
    if (!chatWindow.classList.contains('hidden')) {
      chatInput.focus();
    }
  };
}

if (chatCloseBtn) {
  chatCloseBtn.onclick = () => {
    chatWindow.classList.add('hidden');
  };
}

// 2. Send Message Function
async function sendMessage() {
  const text = chatInput.value.trim();
  if (!text) return;

  // A. Add User Message to UI
  addMessage(text, 'user');
  chatInput.value = '';
  chatInput.disabled = true;
  chatSendBtn.disabled = true;

  // B. Gather Visible Restaurants
  const bounds = map.getBounds();
  const visibleRestaurants = allFeatures
    .filter(f => {
      const lat = f.geometry.coordinates[1];
      const lon = f.geometry.coordinates[0];
      return bounds.contains([lat, lon]);
    })
    .map(f => ({
      name: f.properties.name,
      cuisine: f.properties.cuisine,
      price: f.properties.price,
      award: f.properties.category,
      desc: f.properties.description ? f.properties.description.substring(0, 100) : ""
    }))
    .slice(0, 50); // Limit context size

  if (visibleRestaurants.length === 0) {
    addMessage("I don't see any restaurants on your screen! Move the map to an area with food first.", 'ai');
    chatInput.disabled = false;
    chatSendBtn.disabled = false;
    return;
  }

  // C. Talk to Local API
  try {
    // Create temporary loading bubble
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'message ai loading';
    loadingDiv.textContent = 'Thinking...';
    chatMessages.appendChild(loadingDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

    const response = await fetch('https://eatmyseoul.onrender.com/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        userQuery: text,
        language: currentLang, // uses the global 'currentLang' variable from your app.js
        restaurants: visibleRestaurants
      })
    });

    const data = await response.json();

    // Remove loading bubble
    loadingDiv.remove();

    if (data.reply) {
      addMessage(data.reply, 'ai');
    } else {
      addMessage("Sorry, I got confused. Try again.", 'ai');
    }

  } catch (err) {
    // Remove loading bubble if it exists
    const loader = document.querySelector('.message.loading');
    if (loader) loader.remove();

    addMessage("Error connecting to AI. Is your local server running?", 'ai');
    console.error(err);
  }

  chatInput.disabled = false;
  chatSendBtn.disabled = false;
  chatInput.focus();
}

// 3. UI Helpers
function addMessage(text, sender) {
  const div = document.createElement('div');
  div.className = `message ${sender}`;

  // 1. Convert newlines to breaks
  let formatted = text.replace(/\n/g, '<br>');

  // 2. Convert [[Name]] to clickable links
  // We use a regex to find anything between [[ and ]]
  formatted = formatted.replace(/\[\[(.*?)\]\]/g, (match, name) => {
    // Create a special span that looks like a link
    return `<span class="chat-link" onclick="openRestaurantPopup('${esc(name)}')">${name}</span>`;
  });

  div.innerHTML = formatted;
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Global helper to open popup from chat
window.openRestaurantPopup = (name) => {
  // Find the feature in allFeatures
  const target = allFeatures.find(f => f.properties.name === name);

  if (target) {
    // Zoom to it
    const lat = target.geometry.coordinates[1];
    const lon = target.geometry.coordinates[0];
    map.setView([lat, lon], 16);

    // Open the popup (using the cluster group logic)
    setTimeout(() => {
      clusterGroup.eachLayer(l => {
        if (l.feature === target) {
          l.openPopup();
        }
      });
    }, 300); // Small delay to allow zoom to finish
  } else {
    alert("Could not find " + name + " on the map.");
  }
};

// 4. Event Listeners
if (chatSendBtn) {
  chatSendBtn.onclick = sendMessage;
}
if (chatInput) {
  chatInput.onkeypress = (e) => {
    if (e.key === 'Enter') sendMessage();
  };
}