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

            // 🚨 MUST be python3 for the Linux Docker container!
            const pythonProcess = spawn('python3', [pythonScript]);

            pythonProcess.stdin.write(JSON.stringify(vector));
            pythonProcess.stdin.end();

            let outputData = '';
            pythonProcess.stdout.on('data', (data) => outputData += data.toString());
            pythonProcess.stderr.on('data', (data) => console.error(`[Python stderr]: ${data}`));

            pythonProcess.on('close', () => {
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
    const { userQuery, language } = req.body;
    const targetLang = language === 'ko' ? 'Korean' : 'English';

    console.log(`[AI Request] Lang: ${targetLang} | User asked: "${userQuery}"`);

    const vectorIds = await searchFAISS(userQuery);

    const bestMatches = placesData.features
        .filter(feature => vectorIds.includes(feature.properties.vector_id))
        .map(f => ({
            name: f.properties.name,
            cuisine: f.properties.cuisine,
            award: f.properties.category,
            desc: f.properties.description ? f.properties.description.substring(0, 150) : ""
        }));

    const prompt = `
      You are a local foodie expert in Seoul.
      The user is looking for a recommendation.

      **IMPORTANT:** You must reply in **${targetLang}**.
      **CRITICAL INSTRUCTION:** When you recommend a restaurant from the list, you MUST wrap its exact name in double brackets like this: [[Restaurant Name]].

      User's Request: "${userQuery}"

      Here are the absolute best mathematical matches from our database:
      ${JSON.stringify(bestMatches)}

      Based ONLY on the list above, recommend the top 1-3 best matches.
      Explain WHY it fits their request based on the description provided.
      Keep it brief, highly accurate, and friendly.
    `;

    const result = await model.generateContent(prompt);
    const response = await result.response;

    res.json({ reply: response.text() });

  } catch (error) {
    console.error("Error generating AI response:", error);
    res.status(500).json({ error: "Something went wrong with the AI." });
  }
});

app.listen(port, () => {
  console.log(`🚀 Server is running on port ${port}`);
});