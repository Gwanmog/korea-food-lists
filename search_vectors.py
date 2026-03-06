import sys
import json
import os
import faiss
import numpy as np

# Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
FAISS_INDEX_PATH = os.path.join(script_dir, 'data', 'restaurant_vectors.index')


def search():
    try:
        # 1. Read the raw vector array piped from Node.js
        input_data = sys.stdin.read()
        if not input_data:
            return

        vector = json.loads(input_data)
        vector_np = np.array([vector], dtype=np.float32)

        # 2. Load FAISS and search
        index = faiss.read_index(FAISS_INDEX_PATH)
        distances, indices = index.search(vector_np, 5)

        # 3. Print ONLY the IDs as a JSON string so Node.js can read it
        result_ids = [int(id) for id in indices[0] if id != -1]
        print(json.dumps(result_ids))

    except Exception as e:
        # Print errors to stderr so they don't corrupt the JSON payload Node is expecting
        print(f"FAISS Error: {e}", file=sys.stderr)
        print(json.dumps([]))


if __name__ == "__main__":
    search()