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
        input_data = sys.stdin.read()
        if not input_data:
            return

        parsed = json.loads(input_data)

        # Accept either a raw vector array (legacy) or {vector, allowed_ids}
        if isinstance(parsed, list):
            vector = parsed
            allowed_ids = None
        else:
            vector = parsed['vector']
            allowed_ids = parsed.get('allowed_ids')  # list of int vector IDs, or None

        vector_np = np.array([vector], dtype=np.float32)

        if not os.path.exists(FAISS_INDEX_PATH):
            sys.__stdout__.write(json.dumps([]))
            return

        index = faiss.read_index(FAISS_INDEX_PATH)

        if allowed_ids:
            # Score only the in-view subset by reconstructing their vectors
            valid_ids = [i for i in allowed_ids if 0 <= i < index.ntotal]
            if not valid_ids:
                sys.__stdout__.write(json.dumps([]))
                return

            subset_vectors = np.array([index.reconstruct(i) for i in valid_ids], dtype=np.float32)
            diffs = subset_vectors - vector_np
            distances = np.sum(diffs ** 2, axis=1)

            k = min(20, len(valid_ids))
            sorted_indices = np.argsort(distances)[:k]
            result_ids = [valid_ids[i] for i in sorted_indices]
        else:
            # Global search across all restaurants
            distances, indices = index.search(vector_np, 20)
            result_ids = [int(idx) for idx in indices[0] if idx != -1]

        sys.__stdout__.write(json.dumps(result_ids))

    except Exception:
        sys.__stdout__.write(json.dumps([]))


if __name__ == "__main__":
    search()
