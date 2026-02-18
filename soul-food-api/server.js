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
const model = genAI.getGenerativeModel({
  model: "gemini-1.5-flash-flash"
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