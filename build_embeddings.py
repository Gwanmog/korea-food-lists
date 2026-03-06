import json
import os
import faiss
import numpy as np
from google import genai
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))

# 1. Exact path to your .env inside soul-food-api
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("🚨 ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)

# 2. Exact paths based on your folder tree
DATA_PATH = os.path.join(script_dir, 'site', 'places.geojson')
FAISS_INDEX_PATH = os.path.join(script_dir, 'data', 'restaurant_vectors.index')


def get_embedding(text):
    """Calls Gemini's specialized embedding model to turn text into a 768-dimensional vector."""
    try:
        response = client.models.embed_content(
            model='text-embedding-004',
            contents=text,
        )
        return response.embeddings[0].values
    except Exception as e:
        print(f"⚠️ Embedding failed: {e}")
        return None


def build_retrieval_system():
    # Load the live GeoJSON map data
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"🚨 Could not find {DATA_PATH}. Check your paths!")

    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    features = geojson_data.get('features', [])

    # Check for an existing FAISS index, or create a new one
    embedding_dimension = 768  # Gemini text-embedding-004 uses 768 dimensions
    if os.path.exists(FAISS_INDEX_PATH):
        print(f"📂 Loading existing FAISS index from {FAISS_INDEX_PATH}...")
        index = faiss.read_index(FAISS_INDEX_PATH)
    else:
        print("✨ Creating brand new FAISS index...")
        index = faiss.IndexFlatL2(embedding_dimension)

    new_embeddings_count = 0

    # The Incremental Loop
    for idx, feature in enumerate(features):
        props = feature.get('properties', {})

        # SKIP if we already embedded this restaurant!
        if 'vector_id' in props:
            continue

        name = props.get('name', props.get('Restaurant Name', 'Unknown'))
        print(f"🧠 Generating embedding for new place: {name}")

        # Feature Engineering: Combine signals for the AI to read
        # Adjust these keys if your GeoJSON property names are slightly different!
        category = props.get('category', props.get('Keyword', ''))
        vibe = props.get('description_en', props.get('English Desc', ''))
        verdict = props.get('justification', props.get('AI Justification', ''))

        rich_text = f"Name: {name}. Category: {category}. Vibe & Food: {vibe}. Verdict: {verdict}"

        vector = get_embedding(rich_text)

        if vector:
            vector_np = np.array([vector], dtype=np.float32)
            index.add(vector_np)

            # Save the ID back to the GeoJSON properties
            props['vector_id'] = index.ntotal - 1
            new_embeddings_count += 1

    # Save everything back to disk
    if new_embeddings_count > 0:
        print(f"\n💾 Saving {new_embeddings_count} new vectors to FAISS...")
        faiss.write_index(index, FAISS_INDEX_PATH)

        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(geojson_data, f, ensure_ascii=False, indent=2)
        print("✅ Live map data updated with vector IDs!")
    else:
        print("\n👍 No new restaurants found. FAISS index is already up to date.")


if __name__ == "__main__":
    build_retrieval_system()