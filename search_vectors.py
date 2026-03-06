import sys
import json
import os
import faiss
import numpy as np
from google import genai
from dotenv import load_dotenv

# Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
FAISS_INDEX_PATH = os.path.join(script_dir, 'data', 'restaurant_vectors.index')


def search(query_text, k=5):  # k=5 means return the top 5 matches
    try:
        # 1. Embed the user's search
        response = client.models.embed_content(
            model='text-embedding-004',
            contents=query_text
        )
        vector = response.embeddings[0].values
        vector_np = np.array([vector], dtype=np.float32)

        # 2. Load FAISS and search
        index = faiss.read_index(FAISS_INDEX_PATH)
        distances, indices = index.search(vector_np, k)

        # 3. Print ONLY the IDs as a JSON string so Node.js can read it
        result_ids = [int(id) for id in indices[0] if id != -1]
        print(json.dumps(result_ids))

    except Exception as e:
        # If it fails, print an empty array so Node doesn't crash
        print(json.dumps([]), file=sys.stderr)


if __name__ == "__main__":
    # Grab the search query passed from Node.js
    if len(sys.argv) > 1:
        user_query = sys.argv[1]
        search(user_query)