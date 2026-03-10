require('dotenv').config();
const express = require('express');
const cors = require('cors');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const app = express();
const port = 3000;

app.use(cors());
app.use(express.json());
app.use(express.static(path.resolve(process.cwd(), 'site')));

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
async function searchFAISS(query) {
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

            pythonProcess.stdin.write(JSON.stringify(vector));
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
app.post('/chat', async (req, res) => {
  try {
    const { userQuery, language, mapWindow } = req.body;
    const targetLang = language === 'ko' ? 'Korean' : 'English';

    console.log(`[AI Request] Lang: ${targetLang} | User asked: "${userQuery}"`);

    // 1. THE RAG RETRIEVAL: Ask FAISS for the best semantic matches (top 20 candidates)
    console.log(`[Step 1] Calling Gemini embeddings...`);
    const vectorIds = await searchFAISS(userQuery);
    console.log(`[Step 2] FAISS returned ${vectorIds.length} IDs:`, vectorIds);
    const safeVectorIds = vectorIds.map(id => String(id));

    // 2. THE JOIN: Grab rich metadata for all 20 candidates
    const toRow = f => ({
        name: f.properties.name,
        cuisine: f.properties.cuisine,
        award: f.properties.category,
        desc: f.properties.description ? f.properties.description.substring(0, 300) : "",
        address: f.properties.address_ko || f.properties.address || null,
        lat: f.geometry.coordinates[1],
        lon: f.geometry.coordinates[0]
    });

    const allCandidates = placesData.features.filter(f =>
        f.properties.vector_id && safeVectorIds.includes(String(f.properties.vector_id))
    );

    // 3. LOCATION FILTER: prefer restaurants inside the user's current map viewport,
    //    UNLESS the user explicitly named a neighbourhood — then search globally.
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

    let bestMatches, locationNote;
    if (userNamedLocation) {
        // User explicitly asked about a place — trust their words, search globally
        bestMatches  = allCandidates.map(toRow);
        locationNote = "The user has named a specific neighbourhood. Recommend only restaurants that match that area per their request.";
    } else if (mapWindow) {
        const inView = allCandidates.filter(f => {
            const [lon, lat] = f.geometry.coordinates;
            return lat >= mapWindow.south && lat <= mapWindow.north &&
                   lon >= mapWindow.west  && lon <= mapWindow.east;
        });

        if (inView.length >= 2) {
            bestMatches  = inView.map(toRow);
            locationNote = "All options below are within the user's current map view.";
        } else {
            bestMatches  = allCandidates.map(toRow);
            locationNote = "NOTE: There were not enough strong matches in the user's current map area, so results may be from elsewhere in Seoul. Mention this briefly and suggest they pan the map.";
        }
    } else {
        bestMatches  = allCandidates.map(toRow);
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
    `;

    console.log(`[Step 3] Sending ${bestMatches.length} matches to Gemini chat...`);
    const result = await model.generateContent(prompt);
    const response = await result.response;
    console.log(`[Step 4] Gemini chat responded OK`);

    res.json({ reply: response.text() });

  } catch (error) {
    console.error("Error generating AI response:", error);
    res.status(500).json({ error: "Something went wrong with the AI." });
  }
});

// 5. Lightweight translation endpoint (used for popup descriptions in KR mode)
app.post('/translate', async (req, res) => {
  try {
    const { text } = req.body;
    if (!text) return res.status(400).json({ error: "Missing text" });
    const result = await model.generateContent(
      `Translate the following restaurant description into natural Korean. Return ONLY the Korean translation, no explanations or extra text:\n\n${text}`
    );
    const translated = result.response.text().trim();
    res.json({ translated });
  } catch (error) {
    console.error("Translation error:", error);
    res.status(500).json({ error: "Translation failed" });
  }
});

app.listen(port, () => {
  console.log(`🚀 Server is running on port ${port}`);
});