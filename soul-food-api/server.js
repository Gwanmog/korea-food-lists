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
const embeddingModel = genAI.getGenerativeModel({ model: "gemini-embedding-001" });
// UPDATED: Use the current standard model (Gemini 2.5 Flash)
const model = genAI.getGenerativeModel({
  model: "gemini-2.5-flash"
});

async function searchFAISS(query) {
    try {
        // 1. Let Node.js handle the network request to Gemini!
        // This is much faster than waiting for Python to boot up and do it.
        const result = await embeddingModel.embedContent(query);
        const vector = result.embedding.values;

        return new Promise((resolve) => {
            const pythonScript = path.join(__dirname, '../search_vectors.py');

            // Spawn the trimmed Python script (No arguments needed anymore)
            const pythonProcess = spawn('python', [pythonScript]);

            // 2. Stream the vector data securely into Python's stdin
            pythonProcess.stdin.write(JSON.stringify(vector));
            pythonProcess.stdin.end();

            let outputData = '';
            pythonProcess.stdout.on('data', (data) => outputData += data.toString());

            // Print out any Python errors to the Node console for easy debugging
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
        return []; // Fail gracefully so the chat still works
    }
}
app.post('/chat', async (req, res) => {
  try {
    const { userQuery, language } = req.body;
    const targetLang = language === 'ko' ? 'Korean' : 'English';

    console.log(`[AI Request] Lang: ${targetLang} | User asked: "${userQuery}"`);

    // 1. THE RAG RETRIEVAL: Ask FAISS for the best matches!
    const vectorIds = await searchFAISS(userQuery);

    // 2. THE JOIN: Grab the rich metadata for those exact IDs
    const bestMatches = placesData.features
        .filter(feature => vectorIds.includes(feature.properties.vector_id))
        .map(f => ({
            name: f.properties.name,
            cuisine: f.properties.cuisine,
            award: f.properties.category,
            desc: f.properties.description ? f.properties.description.substring(0, 150) : ""
        }));

    // 3. THE GENERATION: Hand the perfect data to Gemini
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
  console.log(`Server is running at http://localhost:${port}`);
});

const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

// 🚀 ROBUST PATHING: Use process.cwd() for cleaner root-relative paths
const geojsonPath = path.resolve(process.cwd(), 'site/places.geojson');

if (!fs.existsSync(geojsonPath)) {
    console.error(`🚨 CRITICAL ERROR: Could not find GeoJSON at ${geojsonPath}`);
    // This will log the EXACT path Render is trying to use so you can debug in the logs
    process.exit(1);
}

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