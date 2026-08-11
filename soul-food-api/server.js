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

// Which Python runs search_vectors.py. Prefer an explicit override, then a local venv,
// then the platform default. PYTHON_BIN can be set in the environment if none fit.
const PYTHON_BIN = (() => {
    if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
    const venv = process.platform === 'win32'
        ? path.resolve(process.cwd(), '.venv/Scripts/python.exe')
        : path.resolve(process.cwd(), '.venv/bin/python');
    if (fs.existsSync(venv)) return venv;
    return process.platform === 'win32' ? 'python' : 'python3';
})();
console.log(`🐍 Vector search will use: ${PYTHON_BIN}`);

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

// ---- LLM rerank -------------------------------------------------------------
// Measured before building: across the eval queries, 127 correct-district results sat at
// fused ranks 21-50 versus 109 in the top 20. The right answers exist, they're just below
// the cut — so a rerank has real material to promote rather than being latency for its
// own sake. Uses flash-lite: this is a selection task, not a writing one.
const RERANK_POOL = 50;
// Module scope on purpose: rerankCandidates() below needs it, and it previously lived
// inside the /chat handler — a ReferenceError that the rerank's own try/catch would have
// swallowed, leaving the feature silently disabled while appearing to work.
const FINAL_RESULTS = 20;
// flash, not flash-lite. Measured across the eval set (dish-match / location-match):
//   fused, no rerank        54% / 78%
//   flash-lite, loc-first   45% / 87%
//   flash-lite, food-first  49% / 77%
//   flash,      food-first  72% / 69%   <- shipped
// flash-lite degraded dish relevance in every configuration tried. flash trades some
// location accuracy for a large dish gain, which is the right direction for a food app:
// someone asking for 삼겹살 in 강남 is better served 삼겹살 in 송파 than 곱창 in 강남 — and
// part of that location dip is a data-coverage artefact (only 3 삼겹살 places exist in
// 강남구 at all, versus 69 in 송파구).
const rerankModel = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });

async function rerankCandidates(query, features) {
    if (features.length <= FINAL_RESULTS) return features;
    try {
        // Show BOTH cuisine languages. Queries are usually Korean but `cuisine` is English
        // since 5.5.4, so a 국밥 query was being matched against the string "Gukbap".
        const lines = features.map((f, i) => {
            const p = f.properties;
            const where = [p.neighborhood, p.district].filter(Boolean).join(', ');
            const food = [...new Set([p.cuisine_ko, p.cuisine].filter(Boolean))].join(' / ');
            return `${i}. ${p.name_ko || p.name} | ${food || '?'} | ${where || '?'}`;
        }).join('\n');

        // Food first, location as tiebreaker. The reverse ordering was measured and lost
        // 9 points of dish relevance — it returned 곱창 restaurants for "종로 국밥" because
        // they were in the right district.
        const prompt = `Rank these Seoul restaurants by how well each matches the request.

Request: "${query}"

FOOD COMES FIRST. If the request names a dish or cuisine, a restaurant that does not
serve it is a poor match no matter where it is. Rank every restaurant serving the
requested food above every restaurant that does not.

Use location only to order restaurants that already serve the right food: among those,
ones in the named area rank higher.

If no specific dish is named, rank by location and overall fit.

Candidates (index. name | cuisine | area):
${lines}

Return ONLY a JSON array of the ${FINAL_RESULTS} best indices, most relevant first.
Example: [12, 3, 41]`;

        const result = await rerankModel.generateContent({
            contents: [{ role: 'user', parts: [{ text: prompt }] }],
            generationConfig: { responseMimeType: 'application/json', temperature: 0 },
        });
        const idx = JSON.parse(result.response.text());
        if (!Array.isArray(idx) || !idx.length) throw new Error('empty rerank');

        const picked = [];
        const seen = new Set();
        for (const i of idx) {
            const n = Number(i);
            if (Number.isInteger(n) && n >= 0 && n < features.length && !seen.has(n)) {
                seen.add(n);
                picked.push(features[n]);
            }
        }
        // Backfill from the fused order if the model returned too few.
        for (const f of features) {
            if (picked.length >= FINAL_RESULTS) break;
            if (!picked.includes(f)) picked.push(f);
        }
        console.log(`[Rerank] ${features.length} candidates -> ${picked.length} reranked`);
        return picked.slice(0, FINAL_RESULTS);
    } catch (err) {
        // Never let reranking break search — fall back to the fused order.
        console.error('[Rerank] failed, using fused order:', err.message);
        return features.slice(0, FINAL_RESULTS);
    }
}

// ---- Named-area filtering ---------------------------------------------------
// Naming a neighbourhood used to do nothing but flip a sentence in the prompt: the
// search stayed global and Gemini was *asked* to check each address. It doesn't reliably
// hold. Reported case — "chicken in Hongdae" returned 치킨인더키친 경복궁역점, which is in
// 종로구, 4.9km away, described as a Hongdae spot. The candidate list has to be filtered
// before the model ever sees it; a rule the model can break isn't a filter.
//
// Two kinds of area, because Seoul names work two ways. A 구 is an administrative unit
// and the dataset already carries it per restaurant, so match the field exactly. Anywhere
// smaller (홍대, 성수, 연남) has no administrative boundary at all — it's a walkable blob
// around a landmark — so match a radius. Using a radius for 종로 pulled in more 중구
// restaurants than 종로구 ones; using a district for 홍대 would swallow all of 마포구.
const DISTRICT_AREAS = [
    { aliases: ['gangnam', '강남'],   district: '강남구' },
    { aliases: ['seocho', '서초'],    district: '서초구' },
    { aliases: ['jongno', '종로'],    district: '종로구' },
    { aliases: ['mapo', '마포'],      district: '마포구' },
    { aliases: ['yongsan', '용산'],   district: '용산구' },
    { aliases: ['songpa', '송파'],    district: '송파구' },
    { aliases: ['seongbuk', '성북'],  district: '성북구' },
    { aliases: ['nowon', '노원'],     district: '노원구' },
    { aliases: ['dobong', '도봉'],    district: '도봉구' },
];

// Radii are hand-set per area and checked against the dataset: each one is wide enough
// to cover the area as people walk it, tight enough not to spill into the next one.
const POINT_AREAS = [
    { aliases: ['hongdae', '홍대'],                    lat: 37.5568, lon: 126.9237, km: 1.2 },
    { aliases: ['yeonnam', '연남'],                    lat: 37.5602, lon: 126.9250, km: 0.8 },
    { aliases: ['hapjeong', '합정'],                   lat: 37.5495, lon: 126.9137, km: 0.8 },
    { aliases: ['mangwon', '망원'],                    lat: 37.5556, lon: 126.9026, km: 0.8 },
    { aliases: ['sinchon', '신촌'],                    lat: 37.5551, lon: 126.9368, km: 1.0 },
    { aliases: ['itaewon', '이태원'],                  lat: 37.5345, lon: 126.9946, km: 1.0 },
    { aliases: ['insadong', '인사동'],                 lat: 37.5730, lon: 126.9856, km: 0.7 },
    { aliases: ['myeongdong', '명동'],                 lat: 37.5636, lon: 126.9827, km: 0.8 },
    { aliases: ['euljiro', '을지로'],                  lat: 37.5660, lon: 126.9910, km: 1.0 },
    { aliases: ['seongsu', '성수'],                    lat: 37.5445, lon: 127.0557, km: 1.2 },
    { aliases: ['gwanghwamun', '광화문'],              lat: 37.5720, lon: 126.9769, km: 0.9 },
    { aliases: ['dongdaemun', '동대문'],               lat: 37.5714, lon: 127.0094, km: 1.0 },
    { aliases: ['noryangjin', '노량진'],               lat: 37.5133, lon: 126.9424, km: 0.8 },
    { aliases: ['yeouido', '여의도'],                  lat: 37.5216, lon: 126.9243, km: 1.3 },
    { aliases: ['apgujeong', '압구정'],                lat: 37.5273, lon: 127.0286, km: 1.0 },
    { aliases: ['cheongdam', '청담'],                  lat: 37.5197, lon: 127.0530, km: 1.0 },
    { aliases: ['banpo', '반포'],                      lat: 37.5045, lon: 127.0110, km: 1.2 },
    { aliases: ['bukchon', '북촌'],                    lat: 37.5826, lon: 126.9836, km: 0.7 },
    { aliases: ['seochon', '서촌'],                    lat: 37.5789, lon: 126.9707, km: 0.7 },
    { aliases: ['mullae', '문래'],                     lat: 37.5175, lon: 126.8956, km: 0.9 },
    { aliases: ['jamsil', '잠실', 'sincheon', '신천'],  lat: 37.5133, lon: 127.1000, km: 1.5 },
    { aliases: ['sadang', '사당'],                     lat: 37.4765, lon: 126.9816, km: 1.0 },
    { aliases: ['konkuk', '건대'],                     lat: 37.5403, lon: 127.0695, km: 1.0 },
    { aliases: ['daehangno', '대학로', '혜화'],         lat: 37.5820, lon: 127.0018, km: 0.8 },
    { aliases: ['sangwang', '상왕십리'],                lat: 37.5644, lon: 127.0290, km: 0.9 },
];

const ALL_AREAS = [...DISTRICT_AREAS, ...POINT_AREAS];

function haversineKm(aLat, aLon, bLat, bLon) {
    const R = 6371, rad = Math.PI / 180;
    const dLat = (bLat - aLat) * rad, dLon = (bLon - aLon) * rad;
    const h = Math.sin(dLat / 2) ** 2 +
              Math.cos(aLat * rad) * Math.cos(bLat * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
}

// Membership never changes between requests — compute each area's members once.
const areaMembers = new Map();
function membersOf(area) {
    if (areaMembers.has(area)) return areaMembers.get(area);
    const members = placesData.features.filter(f => {
        if (area.district) return f.properties.district === area.district;
        const [lon, lat] = f.geometry.coordinates;
        return haversineKm(area.lat, area.lon, lat, lon) <= area.km;
    });
    areaMembers.set(area, members);
    return members;
}

// Every area the query names. "gangnam or hongdae" should search both, not pick one.
function matchAreas(queryLower) {
    return ALL_AREAS.filter(a => a.aliases.some(alias => queryLower.includes(alias)));
}

// Below this the filter is doing more harm than good: 사당 has 1 restaurant in the
// dataset and 노량진 has 3, so filtering to them returns a near-empty list for every
// query. Those areas fall back to a global search that says where the results are from.
const MIN_AREA_POOL = 8;

// ---- Link markup repair -----------------------------------------------------
// Wrapping recommended names in [[brackets]] is an instruction in the prompt, and models
// drop instructions: "chicken in Hongdae" came back correctly scoped but with every name
// unbracketed, so the reply rendered with no clickable restaurants at all — the same
// dead-end as a link that opens nothing. We know exactly which restaurants were on the
// table, so put the markup back deterministically rather than hoping for it.
function linkifyNames(text, rows) {
    // Longest first, so "치킨인더키친 경복궁역점" is claimed before bare "치킨인더키친"
    // can match inside it.
    const names = [...new Set(rows.map(r => r.name).filter(Boolean))]
        .sort((a, b) => b.length - a.length);

    let out = text;
    for (const name of names) {
        const trimmed = name.trim();
        const hasHangul = /[가-힣]/.test(trimmed);
        // A two-letter Latin name like "ON" would match the word "on" everywhere.
        // Korean names are distinctive enough at that length.
        if (trimmed.length < (hasHangul ? 2 : 3)) continue;
        if (out.includes(`[[${trimmed}]]`)) continue;  // model already linked this one

        const escaped = trimmed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        // The lookarounds keep us out of markup the model (or an earlier, longer name)
        // already produced. Non-global on purpose: link the first mention, leave the
        // rest of the prose alone.
        const boundary = hasHangul ? '' : '\\w';
        const re = new RegExp(`(?<![\\[${boundary}])${escaped}(?![\\]${boundary}])`);
        if (re.test(out)) out = out.replace(re, `[[${trimmed}]]`);
    }
    return out;
}

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
            // python3 in the Linux container; Windows dev machines only have `python`, and
            // a local .venv should win over either. Hardcoding python3 meant the server ran
            // locally but every search silently returned zero results.
            const pythonProcess = spawn(PYTHON_BIN, [pythonScript, faissIndexPath]);

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
        // Sent back to the browser so a [[name]] link resolves to the exact restaurant
        // this row describes. Four restaurants in the dataset share a name outright, so
        // the frontend matching a name against all 2268 features is a coin flip between
        // branches — it landed on 치킨인더키친 경복궁역점 for a Hongdae recommendation.
        id: f.properties.vector_id,
        name: f.properties.name,
        // Both languages: queries are often Korean while `cuisine` is English since 5.5.4,
        // so a 국밥 query was being compared against "Gukbap".
        cuisine: [...new Set([f.properties.cuisine_ko, f.properties.cuisine].filter(Boolean))].join(' / '),
        area: [f.properties.neighborhood, f.properties.district].filter(Boolean).join(', ') || null,
        award: f.properties.category,
        desc: f.properties.description ? f.properties.description.substring(0, 300) : "",
        address: f.properties.address_ko || f.properties.address || null,
        lat: f.geometry.coordinates[1],
        lon: f.geometry.coordinates[0]
    });

    // Join FAISS candidates back to features, re-rank by similarity blended with award
    // standing, and trim to what we actually send the model.
    const byVectorId = new Map();
    for (const f of placesData.features) {
        if (f.properties.vector_id != null) byVectorId.set(Number(f.properties.vector_id), f);
    }

    // Fuse the semantic ranking with the lexical one via RRF, then let award standing
    // nudge the result. Award influence is scaled to RRF's range (a rank-1 document
    // contributes 1/61 ≈ 0.0164) so it stays the thumb on the scale it was in 4.4b.
    const RRF_TIER_INFLUENCE = 0.004;

    const joinIds = async (candidates, allowedPool = null) => {
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
        // Hand a wider pool to the reranker than we ultimately keep — that band is where
        // the measured headroom lives.
        return rerankCandidates(userQuery, out.slice(0, RERANK_POOL).map(e => e.f));
    };

    // 3. LOCATION FILTER — three plans:
    //   Plan B: user named a neighbourhood → FAISS scoped to that area
    //   Plan A: map window provided → FAISS scoped to in-view restaurants (correct order: location first, then semantic rank)
    //   Plan C: map window provided but no in-view results → global FAISS, tell user results are from elsewhere
    const queryLower = userQuery.toLowerCase();
    const namedAreas = matchAreas(queryLower);

    let bestMatches, locationNote, vectorIds;

    if (namedAreas.length) {
        // Plan B: named neighbourhood — scope the candidate pool to that area so an
        // out-of-area restaurant can't be recommended at all, rather than asking the
        // model not to pick one.
        const areaFeatures = [...new Set(namedAreas.flatMap(membersOf))];
        const areaIds = areaFeatures
            .map(f => f.properties.vector_id)
            .filter(id => id != null)
            .map(Number);
        const areaLabel = namedAreas.map(a => a.district || a.aliases[0]).join(' + ');

        if (areaIds.length >= MIN_AREA_POOL) {
            console.log(`[Step 1] Plan B: named area ${areaLabel} — ${areaIds.length} restaurants in scope`);
            vectorIds = await searchFAISS(userQuery, areaIds);
            console.log(`[Step 2] Area-scoped FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
            // Scope the lexical half to the same pool, or it re-imports the very
            // restaurants the area filter just excluded.
            bestMatches  = (await joinIds(vectorIds, areaIds)).map(toRow);
            locationNote = `Every restaurant below is inside ${areaLabel}, so you do not need to check whether they are in the requested area — they are. Do not claim any of them is somewhere else.`;
        }

        if (!bestMatches || bestMatches.length === 0) {
            // Too little data in that area to filter on — search globally and be
            // upfront that the results aren't where they asked.
            console.log(`[Step 1B] Plan B fallback: ${areaLabel} has only ${areaIds.length} restaurants — global FAISS`);
            vectorIds = await searchFAISS(userQuery);
            console.log(`[Step 2B] Global FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
            bestMatches  = (await joinIds(vectorIds)).map(toRow);
            locationNote = "NOTE: Our database has very few restaurants in the area the user named. The options below are from elsewhere in Seoul. Say so briefly and name the area each one is actually in — never imply they are in the requested neighbourhood.";
        }

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
            bestMatches = (await joinIds(vectorIds, inViewIds)).map(toRow);
        }

        if (!bestMatches || bestMatches.length === 0) {
            // Plan C: nothing matched in view — fall back to global and say so
            console.log(`[Step 1C] Plan C: no in-view matches — falling back to global FAISS`);
            vectorIds = await searchFAISS(userQuery);
            console.log(`[Step 2C] Global FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
            bestMatches  = (await joinIds(vectorIds)).map(toRow);
            locationNote = "NOTE: There were no strong matches in the user's current map view. The results below are from elsewhere in Seoul. Start your response by briefly letting the user know you couldn't find anything on their current screen, but found some good options in other neighbourhoods — and suggest they pan the map.";
        } else {
            locationNote = "All options below are within the user's current map view.";
        }

    } else {
        // No location context at all — global search
        console.log(`[Step 1] No location context — global FAISS search`);
        vectorIds = await searchFAISS(userQuery);
        console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds.map(c => c.id));
        bestMatches  = (await joinIds(vectorIds)).map(toRow);
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
      - MATCH THE DISH FIRST. If the user named a dish or cuisine, only recommend restaurants
        whose cuisine actually matches it. A highly-awarded restaurant serving something else
        is NOT a good match — do not recommend it just because it has a high award. If nothing
        in the list serves what they asked for, say so plainly and then offer the closest
        alternatives, naming what they serve.
      - Award level breaks ties between restaurants that already match the dish. It never
        overrides the dish.
      - Location has already been filtered for you where possible — follow the note above it rather than second-guessing it. Never claim a restaurant is in a neighbourhood its address contradicts; if you are unsure where one is, describe it by the area in its own address.
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

    const responseData = {
      reply: linkifyNames(response.text(), bestMatches),
      // The rows the model was given, so the client can map its [[name]] links back to
      // specific restaurants instead of re-guessing from the name alone.
      matches: bestMatches.map(m => ({ id: m.id, name: m.name, lat: m.lat, lon: m.lon })),
    };
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