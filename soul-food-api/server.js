require('dotenv').config();
const express = require('express');
const cors = require('cors');
const rateLimit = require('express-rate-limit');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const app = express();
const port = 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(path.resolve(process.cwd(), 'site')));

// Rate limiting — 10 searches per minute per IP, 30 translates per minute
const searchLimiter = rateLimit({ windowMs: 60 * 1000, max: 10, standardHeaders: true, legacyHeaders: false });
const translateLimiter = rateLimit({ windowMs: 60 * 1000, max: 30, standardHeaders: true, legacyHeaders: false });

// In-memory search cache — keyed by normalized query string, cleared every 6 hours
const searchCache = new Map();
setInterval(() => searchCache.clear(), 6 * 60 * 60 * 1000);

// 1. Connect to Gemini
const apiKey = process.env.GEMINI_API_KEY;
if (!apiKey) {
  console.error("ERROR: GEMINI_API_KEY is missing from .env file!");
  process.exit(1);
}

const genAI = new GoogleGenerativeAI(apiKey);
const embeddingModel = genAI.getGenerativeModel({ model: "gemini-embedding-001" });
const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

// 2. 🚀 ROBUST PATHING: Load the GeoJSON into memory ONCE at startup
// process.cwd() ensures it always looks from the root of the Docker container
const geojsonPath = path.resolve(process.cwd(), 'site/places.geojson');

if (!fs.existsSync(geojsonPath)) {
    console.error(`🚨 CRITICAL ERROR: Could not find GeoJSON at ${geojsonPath}`);
    process.exit(1);
}

const placesData = JSON.parse(fs.readFileSync(geojsonPath, 'utf8'));
console.log(`🗺️ Loaded map data with ${placesData.features.length} locations.`);

// ---- Award-aware re-ranking -------------------------------------------------
// FAISS ranks on semantic similarity alone — it has no idea how good a restaurant is.
// Measured before this existed: the query "제육볶음 맛집" returned 18 Neon Vetted places
// and only 2 of the 8 제육볶음 places that actually hold a heart.
//
// The tier is deliberately a THUMB ON THE SCALE, not a trump card: a Vetted place that
// genuinely nails the dish should still beat a 1-heart place that's only loosely related.
// TIER_WEIGHT is small relative to the similarity spread for that reason.
const TIER_WEIGHT = {
  '3 STARS': 1.00, '2 STARS': 0.85, '1 STAR': 0.70,
  'RIBBON_THREE': 0.85, 'RIBBON_TWO': 0.55, 'RIBBON_ONE': 0.35,
  'BIB GOURMAND': 0.45, 'SELECTED': 0.25,
  'NEON_3': 0.90, 'NEON_2': 0.60, 'NEON_1': 0.35, 'NEON_VETTED': 0.0,
};
// How much award standing can move a result. At 0.12 a top award is worth roughly a
// tenth of the similarity range — enough to lift a strong match into view, not enough
// to drag in something unrelated.
const TIER_INFLUENCE = 0.12;

function awardWeight(props) {
  let best = 0;
  const consider = tier => {
    const w = TIER_WEIGHT[(tier || '').trim().toUpperCase()];
    if (typeof w === 'number' && w > best) best = w;
  };
  // Take the strongest award the restaurant holds across every guide.
  if (Array.isArray(props.awards)) props.awards.forEach(a => consider(a.tier));
  consider(props.category);
  consider(props.tier);
  return best;
}

// ---- Lexical retrieval (hybrid half) ----------------------------------------
// Dense vectors are weak on exact tokens. After putting location into the embeddings
// (4.1), "강남 삼겹살" still only returned 6/20 results actually in 강남구 — a neighbourhood
// name is a literal string to match, not a concept to approximate. This is the lexical
// half of hybrid retrieval; the two rankings are fused below with RRF.
//
// Substring containment rather than word tokenisation, deliberately: Korean doesn't
// delimit morphemes with spaces, so "강남" must match inside "강남구" and "삼겹살" inside
// "삼겹살집". A tokeniser would miss both without a morphological analyser.
const LEXICAL_FIELDS = [
    ['name', 3.0], ['name_ko', 3.0],
    ['neighborhood', 2.5], ['district', 2.5],
    ['cuisine', 2.0], ['cuisine_ko', 2.0],
    ['address_ko', 1.0], ['address', 1.0],
];

const lexicalDocs = placesData.features.map(f => {
    const parts = [];
    for (const [field, weight] of LEXICAL_FIELDS) {
        const v = (f.properties[field] || '').toString().toLowerCase();
        if (v) parts.push([v, weight]);
    }
    return { f, parts };
});

function lexicalRank(query, pool = null) {
    // Tokens of 2+ chars; 1-char fragments match far too much in Korean.
    const tokens = query.toLowerCase().split(/[\s,./()]+/).filter(t => t.length >= 2);
    if (!tokens.length) return [];

    const allowed = pool ? new Set(pool) : null;
    const scored = [];
    for (const doc of lexicalDocs) {
        if (allowed && !allowed.has(Number(doc.f.properties.vector_id))) continue;
        let score = 0, matched = 0;
        for (const tok of tokens) {
            let best = 0;
            for (const [text, weight] of doc.parts) {
                if (text.includes(tok) && weight > best) best = weight;
            }
            if (best > 0) matched++;
            score += best;
        }
        if (score > 0) scored.push({ f: doc.f, score, matched });
    }
    if (!scored.length) return [];

    // Keep only the best token coverage available. Without this, a two-token query like
    // "종로 국밥" surfaces 6 places matching both (all correctly in 종로구) and then 244
    // matching only "국밥" — and those partial matches, sitting at lexical ranks 7-40,
    // still earn enough RRF credit to push the correct results out. Measured: that
    // naive version made 종로 국밥 *worse* than semantic alone (8/20 -> 6/20).
    const best = Math.max(...scored.map(s => s.matched));
    const full = scored.filter(s => s.matched === best);
    full.sort((a, b) => b.score - a.score);
    return full;
}

// Reciprocal Rank Fusion. Combines rankings without needing their scores to share a
// scale — a cosine distance and a token-overlap count aren't comparable, and normalising
// them against each other is where naive hybrid search usually goes wrong.
const RRF_K = 60;

// 3. FAISS Retrieval Engine
// allowedIds: optional array of vector_ids to score against (viewport subset).
// When provided, Python scores only those vectors. When null, global search.
async function searchFAISS(query, allowedIds = null) {
    try {
        const result = await embeddingModel.embedContent(query);
        const vector = result.embedding.values;

        return new Promise((resolve) => {
            const pythonScript = path.resolve(process.cwd(), 'search_vectors.py');
            const faissIndexPath = path.resolve(process.cwd(), 'data/restaurant_vectors.index');
            // 🚨 MUST be python3 for the Linux Docker container!
            const pythonProcess = spawn('python3', [pythonScript, faissIndexPath]);

            // Kill the subprocess and resolve empty if it takes too long
            const timeout = setTimeout(() => {
                console.error("❌ FAISS subprocess timed out — killing process");
                pythonProcess.kill();
                resolve([]);
            }, 10000);

            const payload = allowedIds
                ? JSON.stringify({ vector, allowed_ids: allowedIds })
                : JSON.stringify(vector);
            pythonProcess.stdin.write(payload);
            pythonProcess.stdin.end();

            let outputData = '';
            pythonProcess.stdout.on('data', (data) => outputData += data.toString());
            pythonProcess.stderr.on('data', (data) => console.error(`[Python stderr]: ${data}`));

            pythonProcess.on('close', () => {
                clearTimeout(timeout);
                try {
                    const parsed = JSON.parse(outputData.trim());
                    // search_vectors.py now returns [{id, similarity}]. Tolerate the old
                    // bare-id array so a stale script can't take the endpoint down.
                    const candidates = parsed.map(x =>
                        typeof x === 'object' && x !== null
                            ? { id: Number(x.id), similarity: Number(x.similarity) || 0 }
                            : { id: Number(x), similarity: 0 }
                    );
                    resolve(candidates);
                } catch (error) {
                    console.error("❌ Failed to parse FAISS results:", error);
                    resolve([]);
                }
            });
        });
    } catch (error) {
        console.error("❌ Failed to generate embedding in Node:", error);
        return [];
    }
}

// 4. The Omnibox AI Route
app.post('/chat', searchLimiter, async (req, res) => {
  try {
    const { userQuery, language, mapWindow } = req.body;
    const targetLang = language === 'ko' ? 'Korean' : 'English';

    // Serve from cache if available (language-aware key)
    const cacheKey = `${language || 'en'}:${userQuery.trim().toLowerCase()}`;
    if (searchCache.has(cacheKey)) {
      console.log(`[Cache HIT] "${userQuery}"`);
      return res.json(searchCache.get(cacheKey));
    }

    console.log(`[AI Request] Lang: ${targetLang} | User asked: "${userQuery}"`);

    // 2. THE JOIN helper
    const toRow = f => ({
        name: f.properties.name,
        cuisine: f.properties.cuisine,
        award: f.properties.category,
        desc: f.properties.description ? f.properties.description.substring(0, 300) : "",
        address: f.properties.address_ko || f.properties.address || null,
        lat: f.geometry.coordinates[1],
        lon: f.geometry.coordinates[0]
    });

    // Join FAISS candidates back to features, re-rank by similarity blended with award
    // standing, and trim to what we actually send the model.
    const FINAL_RESULTS = 20;
    const byVectorId = new Map();
    for (const f of placesData.features) {
        if (f.properties.vector_id != null) byVectorId.set(Number(f.properties.vector_id), f);
    }

    // Fuse the semantic ranking with the lexical one via RRF, then let award standing
    // nudge the result. Award influence is scaled to RRF's range (a rank-1 document
    // contributes 1/61 ≈ 0.0164) so it stays the thumb on the scale it was in 4.4b.
    const RRF_TIER_INFLUENCE = 0.004;

    const joinIds = (candidates, allowedPool = null) => {
        const fused = new Map();  // vector_id -> {f, score}

        const addRanking = (features) => {
            features.forEach((f, i) => {
                const key = Number(f.properties.vector_id);
                const entry = fused.get(key) || { f, score: 0 };
                entry.score += 1 / (RRF_K + i + 1);
                fused.set(key, entry);
            });
        };

        addRanking(candidates.map(c => byVectorId.get(c.id)).filter(Boolean));
        addRanking(lexicalRank(userQuery, allowedPool).slice(0, 40).map(s => s.f));

        const out = [...fused.values()];
        for (const e of out) e.score += RRF_TIER_INFLUENCE * awardWeight(e.f.properties);
        out.sort((a, b) => b.score - a.score);
        return out.slice(0, FINAL_RESULTS).map(e => e.f);
    };

    // 3. LOCATION FILTER — three plans:
    //   Plan B: user named a neighbourhood → global FAISS search, trust their words
    //   Plan A: map window provided → FAISS scoped to in-view restaurants (correct order: location first, then semantic rank)
    //   Plan C: map window provided but no in-view results → global FAISS, tell user results are from elsewhere
    const SEOUL_NEIGHBOURHOODS = [
        // English
        'gangnam','hongdae','itaewon','sinchon','insadong','myeongdong','jongno',
        'mapo','yeonnam','hapjeong','mangwon','euljiro','seongsu','gwanghwamun',
        'dongdaemun','noryangjin','yeouido','apgujeong','cheongdam','seocho',
        'banpo','bukchon','seochon','mullae','sangwang','nowon','dobong',
        'sincheon','sadang','konkuk','건대','혜화','daehangno',
        // Korean
        '강남','홍대','이태원','신촌','인사동','명동','종로','마포','연남',
        '합정','망원','을지로','성수','광화문','동대문','노량진','여의도',
        '압구정','청담','서초','반포','북촌','서촌','문래','노원','도봉',
        '신천','사당','건대입구','대학로'
    ];

    const queryLower = userQuery.toLowerCase();
    const userNamedLocation = SEOUL_NEIGHBOURHOODS.some(n => queryLower.includes(n));

    let bestMatches, locationNote, vectorIds;

    if (userNamedLocation) {
        // Plan B: named neighbourhood — global search, Gemini checks coordinates
        console.log(`[Step 1] Plan B: named location — global FAISS search`);
        vectorIds = await searchFAISS(userQuery);
        console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
        bestMatches  = joinIds(vectorIds).map(toRow);
        locationNote = "The user has named a specific neighbourhood. Recommend only restaurants that match that area per their request.";

    } else if (mapWindow) {
        // Plan A: scope FAISS to restaurants already in the viewport
        const inViewFeatures = placesData.features.filter(f => {
            const [lon, lat] = f.geometry.coordinates;
            return lat >= mapWindow.south && lat <= mapWindow.north &&
                   lon >= mapWindow.west  && lon <= mapWindow.east;
        });
        const inViewIds = inViewFeatures
            .map(f => f.properties.vector_id)
            .filter(id => id != null)
            .map(Number);

        console.log(`[Step 1] Plan A: ${inViewIds.length} in-view restaurants with vectors`);

        if (inViewIds.length > 0) {
            vectorIds = await searchFAISS(userQuery, inViewIds);
            console.log(`[Step 2] Viewport-scoped FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
            // Pass the viewport pool so the lexical half is scoped too — otherwise it
            // would pull in matches from outside the map the user is looking at.
            bestMatches = joinIds(vectorIds, inViewIds).map(toRow);
        }

        if (!bestMatches || bestMatches.length === 0) {
            // Plan C: nothing matched in view — fall back to global and say so
            console.log(`[Step 1C] Plan C: no in-view matches — falling back to global FAISS`);
            vectorIds = await searchFAISS(userQuery);
            console.log(`[Step 2C] Global FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
            bestMatches  = joinIds(vectorIds).map(toRow);
            locationNote = "NOTE: There were no strong matches in the user's current map view. The results below are from elsewhere in Seoul. Start your response by briefly letting the user know you couldn't find anything on their current screen, but found some good options in other neighbourhoods — and suggest they pan the map.";
        } else {
            locationNote = "All options below are within the user's current map view.";
        }

    } else {
        // No location context at all — global search
        console.log(`[Step 1] No location context — global FAISS search`);
        vectorIds = await searchFAISS(userQuery);
        console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
        bestMatches  = joinIds(vectorIds).map(toRow);
        locationNote = "";
    }

    const prompt = `
      You are a local foodie expert in Seoul.
      The user is looking for a recommendation.

      **IMPORTANT:** You must reply in **${targetLang}**.
      **CRITICAL INSTRUCTION:** When you recommend a restaurant from the list, you MUST wrap its exact name in double brackets like this: [[Restaurant Name]].

      ${locationNote}

      User's Request: "${userQuery}"

      Here are the best matches from our database (each includes its real GPS coordinates and address):
      ${JSON.stringify(bestMatches)}

      RULES:
      - If the user asked for a specific neighbourhood, check the lat/lon and address of each restaurant. Only recommend ones that are genuinely in or very close to that area. Do NOT claim a restaurant is in a neighbourhood if its address says otherwise.
      - Never invent or assume a restaurant's location — use only the address and coordinates provided.
      - Based ONLY on the list above, recommend the top 1-3 best matches.
      - Explain WHY each fits their request based on the description.
      - Keep it brief, accurate, and friendly.
      - IMPORTANT: Our database does NOT contain operating hours or closed-day information. If the user asks about a specific day or time (e.g. "open on Monday"), briefly acknowledge you cannot confirm hours, then recommend the best matches based on food type and quality alone. Never say you found zero results just because hours data is missing.
    `;

    console.log(`[Step 3] Sending ${bestMatches.length} matches to Gemini chat...`);
    const result = await model.generateContent(prompt);
    const response = await result.response;
    console.log(`[Step 4] Gemini chat responded OK`);

    const responseData = { reply: response.text() };
    searchCache.set(cacheKey, responseData);
    res.json(responseData);

  } catch (error) {
    console.error("Error generating AI response:", error);
    res.status(500).json({ error: "Something went wrong with the AI." });
  }
});

// Build a lookup map from English description → cached Korean translation
// so /translate never needs to call the API for known restaurants
const descriptionKoCache = new Map();
for (const feature of placesData.features) {
  const p = feature.properties;
  if (p.description && p.description_ko) {
    descriptionKoCache.set(p.description.trim(), p.description_ko);
  }
}
console.log(`Loaded ${descriptionKoCache.size} cached Korean translations.`);

// 5. Lightweight translation endpoint (used for popup descriptions in KR mode)
app.post('/translate', translateLimiter, async (req, res) => {
  try {
    const { text } = req.body;
    if (!text) return res.status(400).json({ error: "Missing text" });

    // Serve from GeoJSON cache first — no API call needed
    const cached = descriptionKoCache.get(text.trim());
    if (cached) {
      return res.json({ translated: cached });
    }

    // Fallback to Gemini for any text not in our dataset
    const result = await model.generateContent(
      `Translate the following restaurant description into natural Korean. Return ONLY the Korean translation, no explanations or extra text:\n\n${text}`
    );
    const translated = result.response.text().trim();
    descriptionKoCache.set(text.trim(), translated); // cache for next time
    res.json({ translated });
  } catch (error) {
    console.error("Translation error:", error);
    res.status(500).json({ error: "Translation failed" });
  }
});

// 6. Kakao OIDC Token Exchange
// Receives the one-time `code` from the frontend, exchanges it with Kakao for
// tokens, and returns the `id_token` so the frontend can call supabase.auth.signInWithIdToken.
app.post('/auth/kakao/token', async (req, res) => {
  const { code, redirect_uri } = req.body;
  if (!code || !redirect_uri) {
    return res.status(400).json({ error: 'Missing code or redirect_uri' });
  }

  const kakaoRestApiKey = process.env.KAKAO_REST_API_KEY;
  if (!kakaoRestApiKey) {
    console.error('ERROR: KAKAO_REST_API_KEY is missing from environment!');
    return res.status(500).json({ error: 'Server misconfiguration' });
  }

  try {
    const params = new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: kakaoRestApiKey,
      redirect_uri,
      code,
      ...(process.env.KAKAO_CLIENT_SECRET && { client_secret: process.env.KAKAO_CLIENT_SECRET }),
    });

    const kakaoRes = await fetch('https://kauth.kakao.com/oauth/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: params.toString(),
    });

    const data = await kakaoRes.json();

    if (!kakaoRes.ok || !data.id_token) {
      console.error('[Kakao] Token exchange failed:', data);
      return res.status(400).json({ error: data.error_description || 'Kakao token exchange failed' });
    }

    res.json({ id_token: data.id_token });
  } catch (err) {
    console.error('[Kakao] Token exchange error:', err);
    res.status(500).json({ error: 'Token exchange failed' });
  }
});

app.listen(port, () => {
  console.log(`🚀 Server is running on port ${port}`);
});