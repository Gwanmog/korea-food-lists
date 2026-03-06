# 🥢 Eat My Seoul: AI-Powered Restaurant Recommendation Engine

**Eat My Seoul** is an AI-native, semantic search map for discovering Seoul's finest culinary experiences. It aggregates data from the Michelin Guide, the Blue Ribbon Survey, and our own proprietary AI-driven "Neon Guide."

Instead of traditional keyword filters, this backend utilizes a Retrieval-Augmented Generation (RAG) architecture powered by FAISS and Gemini. Users can search with natural language (e.g., *"spicy comfort food on a rainy day"*), and the engine mathematically computes the best culinary matches.

## 🚀 Tech Stack

* **Backend:** Node.js, Express
* **AI & NLP:** Google Gemini 2.5 Flash Lite API, QWEN2.5:3b
* **Vector Database:** FAISS (Facebook AI Similarity Search)
* **Web Scraping:** Python, BeautifulSoup, Selenium, Requests
* **Geocoding:** Kakao Local REST API
* **Data Flow:** CSV ➔ GeoJSON ➔ FAISS Index

## 🧠 Core Development Principles

1. **Strict Quality Control:** The AI "Neon Guide" relies on authentic customer receipts. If AI sentiment conflicts with receipt-verified negative reviews, the human-in-the-loop "Supreme Court" script revokes the award.
2**Domain Knowledge Overrides:** Specialized venues (like craft breweries) are graded on their core competencies (beer and ambiance), overruling generic AI penalties for average food.

---

## 🗺️ The Master Data Pipeline

Updating the map requires running the Python pipeline in this exact sequence to ensure data is scraped, cleaned, deduplicated, and mathematically indexed.

### Phase 1: The AI Critics (Generating the Neon Guide)

Our agents scour Naver blogs, read receipts, and score restaurants to build our proprietary dataset.

1. `python master_agent.py` — Scrapes blogs, evaluates food quality, and writes the initial bilingual descriptions.
2. `python receipt_auditor.py` — The "Bouncer." Reads authentic customer receipts to catch AI hallucinations or hidden negative reviews.
3. `python final_verdict.py` — The "Supreme Court." Cleans the data, applies manual domain-knowledge overrides (like brewery adjustments), and outputs `neon_guide_audited_final.csv`.

### Phase 2: The Ghostwriter (Enriching Established Guides)

Fleshing out sparse data for locations that already hold Michelin or Blue Ribbon distinctions.
4. `python enrich_guides.py` — Bypasses the strict scoring logic, scrapes Naver for context, and uses the AI Ghostwriter to generate beautiful bilingual summaries for `blueribbon.csv` so the math engine has rich text to read.

### Phase 3: The Crossroads (Deduplication)

Merging the three guides so they play nicely together.
5. `python dedupe_master.py` — Reads `michelin.csv`, `blueribbon_enriched.csv`, and `neon_guide_audited_final.csv`. Finds overlapping restaurants, merges their awards, and outputs one clean `master_deduped_places.csv`.

### Phase 4: Map Assembly

Translating CSV text into actual GPS map layers.
6. `python build_map_list.py build` — Takes the deduped master list, pings the Kakao API to fill in any missing GPS coordinates or official Korean addresses, and bundles everything into the final `site/places.geojson` file.

### Phase 5: The Brain Sync (Vector Embeddings)

Teaching the AI how to search the map.
7. `python build_embeddings.py` — Reads the finished `places.geojson`. It takes the rich descriptions, uses Gemini to convert them into 768-dimensional math vectors, and saves them to the FAISS index (`data/restaurant_vectors.index`). It also injects a `vector_id` back into the GeoJSON.

### Phase 6: Live Production

8. `npm start` (or `node server.js`) — Boots up the Node.js Express backend. Listens for user semantic queries, triggers `search_vectors.py` to calculate the nearest FAISS neighbors, and returns the exact map pins to the frontend.

---

## 🛠️ Environment Setup

You will need a `.env` file in the root `soul-food-api` directory with the following keys:

```env
GEMINI_API_KEY=your_gemini_key
NAVER_CLIENT_ID=your_naver_id
NAVER_CLIENT_SECRET=your_naver_secret
KAKAO_REST_API_KEY=your_kakao_key

```

### Python Dependencies

Ensure your virtual environment (`.venv`) is active, then install:

```bash
pip install pandas requests beautifulsoup4 selenium google-genai faiss-cpu numpy python-dotenv

```

### Node Dependencies

```bash
npm install express cors dotenv @google/generative-ai

```

---

*Built with ❤️ for the ultimate Seoul food experience.*