import sys
import json
import os
import faiss
import numpy as np
from pathlib import Path

# --- DOCKER-SAFE PATHING ---
# This finds the directory where THIS script lives, then looks for data/
BASE_DIR = Path(__file__).resolve().parent
FAISS_INDEX_PATH = str(BASE_DIR / "data" / "restaurant_vectors.index")

# --- SILENCE WARNINGS ---
# Stop library warnings from leaking into the JSON output Node is reading
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
sys.stderr = open(os.devnull, 'w')


def search():
    try:
        # 1. Read the raw vector array piped from Node.js
        input_data = sys.stdin.read()
        if not input_data:
            return

        vector = json.loads(input_data)
        vector_np = np.array([vector], dtype=np.float32)

        # 2. Load FAISS and search
        # Check if file exists first to avoid a hard crash
        if not os.path.exists(FAISS_INDEX_PATH):
            print(json.dumps([]))
            return

        index = faiss.read_index(FAISS_INDEX_PATH)  # Fixed the variable name!
        distances, indices = index.search(vector_np, 20)

        # 3. Print ONLY the IDs as a JSON string
        # This is the only thing that should go to stdout
        result_ids = [int(idx) for idx in indices[0] if idx != -1]

        # We write directly to the original stdout to ensure it's clean
        sys.__stdout__.write(json.dumps(result_ids))

    except Exception:
        # If anything goes wrong, return empty list so the server doesn't crash
        sys.__stdout__.write(json.dumps([]))


if __name__ == "__main__":
    search()