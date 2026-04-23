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

// 3. FAISS Retrieval Engine
// allowedIds: optional array of vector_ids to score against (viewport subset).
// When provided, Python scores only those vectors. When null, global top-20 search.
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
                    const vectorIds = JSON.parse(outputData.trim());
                    resolve(vectorIds);
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

    const joinIds = (ids) => {
        const safe = ids.map(id => String(id));
        return placesData.features.filter(f =>
            f.properties.vector_id != null && safe.includes(String(f.properties.vector_id))
        );
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
        console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds);
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
            console.log(`[Step 2] Viewport-scoped FAISS returned ${vectorIds.length} IDs:`, vectorIds);
            bestMatches = joinIds(vectorIds).map(toRow);
        }

        if (!bestMatches || bestMatches.length === 0) {
            // Plan C: nothing matched in view — fall back to global and say so
            console.log(`[Step 1C] Plan C: no in-view matches — falling back to global FAISS`);
            vectorIds = await searchFAISS(userQuery);
            console.log(`[Step 2C] Global FAISS returned ${vectorIds.length} IDs:`, vectorIds);
            bestMatches  = joinIds(vectorIds).map(toRow);
            locationNote = "NOTE: There were no strong matches in the user's current map view. The results below are from elsewhere in Seoul. Start your response by briefly letting the user know you couldn't find anything on their current screen, but found some good options in other neighbourhoods — and suggest they pan the map.";
        } else {
            locationNote = "All options below are within the user's current map view.";
        }

    } else {
        // No location context at all — global search
        console.log(`[Step 1] No location context — global FAISS search`);
        vectorIds = await searchFAISS(userQuery);
        console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds);
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