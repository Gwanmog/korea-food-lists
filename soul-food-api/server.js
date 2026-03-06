require('dotenv').config();
const express = require('express');
const cors = require('cors');
const { GoogleGenerativeAI } = require('@google/generative-ai');

const app = express();
const port = 3000;

app.use(cors());
app.use(express.json());

// Connect to Gemini
const apiKey = process.env.GEMINI_API_KEY;
if (!apiKey) {
  console.error("ERROR: GEMINI_API_KEY is missing from .env file!");
  process.exit(1);
}

const genAI = new GoogleGenerativeAI(apiKey);

// UPDATED: Use the current standard model (Gemini 2.5 Flash)
const model = genAI.getGenerativeModel({
  model: "gemini-2.5-flash"
});

app.post('/chat', async (req, res) => {
  try {
    // We now expect 'language' in the request body
    const { userQuery, restaurants, language } = req.body;

    // Default to English if not provided
    const targetLang = language === 'ko' ? 'Korean' : 'English';

    console.log(`[AI Request] Lang: ${targetLang} | User asked: "${userQuery}"`);

    const prompt = `
      You are a local foodie expert in Seoul.
      The user is looking for a recommendation.

      **IMPORTANT:** You must reply in **${targetLang}**.

      **CRITICAL INSTRUCTION:**
      When you recommend a restaurant from the list, you MUST wrap its exact name in double brackets like this: [[Restaurant Name]].
      Example: "I recommend [[Oreno Ramen]] because..."

      User's Request: "${userQuery}"

      Here is a list of restaurants currently visible on their map:
      ${JSON.stringify(restaurants)}

      Based ONLY on the list above, recommend the top 1-3 best matches.
      For each recommendation, explain WHY it fits their request.
      If nothing fits well, say "I don't see a perfect match in this area, but..." and pick the closest option.
      Keep it brief and friendly.
    `;

    const result = await model.generateContent(prompt);
    const response = await result.response;
    const text = response.text();

    res.json({ reply: text });

  } catch (error) {
    console.error("Error generating AI response:", error);
    res.status(500).json({ error: "Something went wrong with the AI." });
  }
});

app.listen(port, () => {
  console.log(`Server is running at http://localhost:${port}`);
});

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// 🚀 SCALABILITY TRICK: Load the GeoJSON into memory once when the server starts!
// This prevents reading the file from the hard drive on every single search.
const geojsonPath = path.join(__dirname, '../site/places.geojson');
let placesData = JSON.parse(fs.readFileSync(geojsonPath, 'utf8'));

// 🔍 The Semantic Search Endpoint
app.get('/api/search', (req, res) => {
    const userQuery = req.query.q;

    if (!userQuery) {
        return res.status(400).json({ error: "Please provide a search query (?q=...)" });
    }

    console.log(`🤖 User searching for: "${userQuery}"`);

    // 1. Hand the query to the Python FAISS engine
    const pythonScript = path.join(__dirname, '../search_vectors.py');
    const pythonProcess = spawn('python', [pythonScript, userQuery]);

    let outputData = '';

    // Collect the data Python prints out
    pythonProcess.stdout.on('data', (data) => {
        outputData += data.toString();
    });

    // 2. When Python finishes, do the Metadata Join
    pythonProcess.on('close', (code) => {
        try {
            // Parse the IDs from Python (e.g., [42, 17, 8])
            const vectorIds = JSON.parse(outputData.trim());

            // The Join: Filter the in-memory GeoJSON for these exact IDs
            const results = placesData.features.filter(feature => {
                const id = feature.properties.vector_id;
                return vectorIds.includes(id);
            });

            // Return the rich metadata to the frontend
            res.json({
                query: userQuery,
                match_count: results.length,
                results: results
            });

        } catch (error) {
            console.error("❌ Failed to parse FAISS results:", error);
            res.status(500).json({ error: "Search engine failed." });
        }
    });
});