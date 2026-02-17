function renderPopup(p) {
  // PRIORITY FOR LINKS: 
  // 1. Official Korean Name (from Michelin KR site) -> "정육면체"
  // 2. Algorithmic Address -> "Seoul Seodaemun-gu..."
  // 3. English Name -> "Tasty Cube"
  const searchQuery = p.name_ko || p.korean_query || p.name;
  
  const naverSearch = `https://map.naver.com/p/search/${enc(searchQuery)}`;
  const googleSearch = `https://www.google.com/maps/search/?api=1&query=${enc(p.name + " Seoul")}`;

  let meta = [];
  if (p.cuisine) meta.push(`🍴 ${esc(p.cuisine)}`);
  if (p.price) meta.push(`💰 ${esc(p.price)}`);
  if (p.phone) meta.push(`📞 <a href="tel:${p.phone}" style="color:inherit">${esc(p.phone)}</a>`);

  let actions = [];
  
  if (p.kakao_url && p.kakao_id) {
    actions.push(`<a class="linkbtn kakao" href="${p.kakao_url}" target="_blank">Kakao</a>`);
  } else {
    // If we have a Korean Name, searching the map directly is much more reliable
    // Format: map.kakao.com/link/search/KoreanName
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

  // --- SHOW KOREAN NAME IN POPUP TOO ---
  // If we found the Korean name, show it under the English name
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