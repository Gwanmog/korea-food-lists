import json
import os
import time
import faiss
import numpy as np
from google import genai
from google.genai import errors as genai_errors
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))

env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)

DATA_PATH = os.path.join(script_dir, 'site', 'places.geojson')
FAISS_INDEX_PATH = os.path.join(script_dir, 'data', 'restaurant_vectors.index')
TRANSLATION_CACHE_PATH = os.path.join(script_dir, 'data', 'translation_cache.json')

TRANSLATE_PROMPT = (
    "You are a Seoul food and nightlife expert. "
    "Translate the following restaurant description into natural Korean, "
    "using the words and phrasing that a Korean food blogger would use. "
    "Preserve all specific details (dish names, ingredients, atmosphere). "
    "Return ONLY the Korean translation, no explanation.\n\n"
    "Text: {text}"
)


def translate_to_korean(text: str, retries: int = 3) -> str | None:
    """Translates English restaurant text to Korean via Gemini. Returns None on failure."""
    if not text or not text.strip():
        return None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=TRANSLATE_PROMPT.format(text=text),
            )
            return response.text.strip()
        except genai_errors.ServerError:
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"  [RETRY] 503 from Gemini, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [ERROR] Translation failed after {retries} attempts.")
                return None
        except Exception as e:
            print(f"  [ERROR] Translation error: {e}")
            return None


def get_embedding(text: str, retries: int = 3):
    """Embeds text using gemini-embedding-001. Returns None on failure."""
    for attempt in range(retries):
        try:
            response = client.models.embed_content(
                model='gemini-embedding-001',
                contents=text,
            )
            return response.embeddings[0].values
        except genai_errors.ServerError:
            if attempt < retries - 1:
                wait = 15 * (attempt + 1)
                print(f"  [RETRY] 503 from Gemini, waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  [ERROR] Embedding failed after {retries} attempts.")
                return None
        except Exception as e:
            print(f"  [ERROR] Embedding error: {e}")
            return None


def _load_translation_cache() -> dict:
    """Load persistent translation cache keyed by kakao_id (or name as fallback)."""
    if os.path.exists(TRANSLATION_CACHE_PATH):
        with open(TRANSLATION_CACHE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_translation_cache(cache: dict):
    """Persist translation cache to disk."""
    os.makedirs(os.path.dirname(TRANSLATION_CACHE_PATH), exist_ok=True)
    with open(TRANSLATION_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def build_retrieval_system():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Could not find {DATA_PATH}. Check your paths!")

    with open(DATA_PATH, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    features = geojson_data.get('features', [])

    # Load persistent translation cache so rebuilt geojson doesn't retranslate
    translation_cache = _load_translation_cache()
    cache_hits = 0

    embedding_dimension = 3072
    print("Creating fresh FAISS index...")
    index = faiss.IndexFlatL2(embedding_dimension)

    new_embeddings_count = 0
    translations_added = 0

    for idx, feature in enumerate(features):
        props = feature.get('properties', {})
        name = props.get('name_ko') or props.get('name', '알 수 없음')

        # Stable cache key: kakao_id preferred, name as fallback
        cache_key = str(props.get('kakao_id') or props.get('name', '')).strip()

        # English source fields
        desc_en = props.get('description') or props.get('description_en') or ""
        verdict_en = props.get('justification') or ""
        category = props.get('category') or ""

        # Data Quality Gate
        if len(desc_en.strip()) < 10 and not category.strip():
            print(f"  Skipping {name}: not enough text to embed.")
            continue

        cached = translation_cache.get(cache_key, {})

        # --- Translate description: props > cache > API ---
        desc_ko = props.get('description_ko') or cached.get('description_ko') or ""
        if not desc_ko and desc_en.strip():
            print(f"  Translating description for: {name}")
            desc_ko = translate_to_korean(desc_en) or ""
            if desc_ko:
                translations_added += 1
        elif cached.get('description_ko') and not props.get('description_ko'):
            cache_hits += 1

        # --- Translate verdict/justification: props > cache > API ---
        verdict_ko = props.get('justification_ko') or cached.get('justification_ko') or ""
        if not verdict_ko and verdict_en.strip():
            verdict_ko = translate_to_korean(verdict_en) or ""
            if verdict_ko:
                translations_added += 1
        elif cached.get('justification_ko') and not props.get('justification_ko'):
            cache_hits += 1

        # Write back to props and update persistent cache
        if desc_ko:
            props['description_ko'] = desc_ko
        if verdict_ko:
            props['justification_ko'] = verdict_ko
        if cache_key and (desc_ko or verdict_ko):
            translation_cache[cache_key] = {
                k: v for k, v in {
                    'description_ko': desc_ko,
                    'justification_ko': verdict_ko,
                }.items() if v
            }

        # --- Build fully Korean rich_text ---
        # Falls back to English desc if translation failed
        desc_final = desc_ko or desc_en
        verdict_final = verdict_ko or verdict_en

        rich_text = (
            f"이름: {name}. "
            f"설명: {desc_final}. "
            f"{'평가: ' + verdict_final + '.' if verdict_final else ''}"
        ).strip()

        print(f"  Embedding: {name}")
        vector = get_embedding(rich_text)

        if vector:
            vector_np = np.array([vector], dtype=np.float32)
            index.add(vector_np)
            props['vector_id'] = index.ntotal - 1
            new_embeddings_count += 1

    # Save everything
    _save_translation_cache(translation_cache)
    print(f"\nTranslation cache: {len(translation_cache)} entries "
          f"({cache_hits} hits, {translations_added} new API calls)")

    if new_embeddings_count > 0:
        print(f"Saving {new_embeddings_count} vectors to FAISS...")
        faiss.write_index(index, FAISS_INDEX_PATH)

        print(f"Saving GeoJSON with updated Korean translations...")
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(geojson_data, f, ensure_ascii=False, indent=2)
        print("Done.")
    else:
        print("No valid restaurants found to embed.")


if __name__ == "__main__":
    build_retrieval_system()
