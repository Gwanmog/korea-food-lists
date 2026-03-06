import pandas as pd
import math
import json
import requests
import os

LOCAL_MODEL = "qwen2.5:3b"

def get_distance(lat1, lon1, lat2, lon2):
    """Calculates distance in meters between two GPS coordinates."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def check_name_match(name1, name2):
    """Uses Local Qwen to check if the English/Korean names match."""
    prompt = f"""
    You are a bilingual Korean-English data entity deduplicator.
    Determine if these two restaurant names refer to the EXACT SAME establishment.

    Name 1: {name1}
    Name 2: {name2}

    TEMPLATE 1: MATCH
    {{
        "is_match": true,
        "reason": "Explain why they match"
    }}
    TEMPLATE 2: NO MATCH
    {{
        "is_match": false,
        "reason": "Explain that they are different names"
    }}

    Respond ONLY with the chosen JSON template.
    """
    try:
        response = requests.post(
            'http://localhost:11434/api/generate',
            json={"model": LOCAL_MODEL, "prompt": prompt, "stream": False, "format": "json"},
            timeout=15
        )
        result = json.loads(response.json()['response'])
        return bool(result.get("is_match", False)), str(result.get("reason", ""))
    except Exception as e:
        print(f"   ⚠️ Local Qwen failed: {e}")
        return False, "Error"


def load_existing_map_data():
    """
    Loads your existing map data.
    UPDATE THIS to point to wherever your seoul-food-api stores its locations!
    """
    # Assuming your Node API reads from a JSON file. Change path as needed.
    map_data_path = os.path.join(os.path.dirname(__file__), 'seoul-food-api', 'data', 'restaurants.json')

    if not os.path.exists(map_data_path):
        print(f"⚠️ Could not find existing map data at {map_data_path}.")
        print("Using a dummy Michelin entry for testing.")
        return [
            {"name": "Mingles", "lat": 37.5226, "lng": 127.0421},
            {"name": "Ggupdang", "lat": 37.5144, "lng": 127.0195}
        ]

    with open(map_data_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def run_merge():
    print("🚀 Starting Geo-Semantic Deduplication Pipeline...")

    # 1. Load the Audited CSV
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, 'neon_guide_review_queue.csv')
    df = pd.read_csv(csv_path)

    # 2. Filter for ONLY the winning restaurants
    # Strip whitespace to ensure safe matching
    df['Rating Justified'] = df['Rating Justified'].astype(str).str.strip()
    df_winners = df[df['Rating Justified'] == 'Yes'].copy()
    print(f"✅ Found {len(df_winners)} approved restaurants ready for merging.")

    # 3. Load Master Map Data
    map_data = load_existing_map_data()
    print(f"🗺️ Loaded {len(map_data)} existing restaurants from the master map.")

    clean_new_restaurants = []

    # 4. The Deduplication Loop
    for idx, new_row in df_winners.iterrows():
        new_name = new_row['Restaurant Name']
        new_lat = float(new_row['Latitude'])
        new_lng = float(new_row['Longitude'])

        is_duplicate = False

        for existing in map_data:
            existing_name = existing.get('name', '')
            existing_lat = float(existing.get('lat', 0))
            existing_lng = float(existing.get('lng', 0))

            # Spatial Check (within 30 meters)
            dist = get_distance(new_lat, new_lng, existing_lat, existing_lng)

            if dist < 30:
                print(f"\n📍 PROXIMITY ALERT: '{new_name}' is {int(dist)}m away from '{existing_name}'")

                # Semantic Check (LLM)
                match, reason = check_name_match(new_name, existing_name)

                if match:
                    print(f"   🚨 DUPLICATE DROPPED: {reason}")
                    is_duplicate = True
                    break  # Stop checking this restaurant against others
                else:
                    print(f"   🟢 SAFE (Different entities): {reason}")

        if not is_duplicate:
            clean_new_restaurants.append(new_row)

    # 5. Export the Final Clean Data
    if clean_new_restaurants:
        clean_df = pd.DataFrame(clean_new_restaurants)
        output_path = os.path.join(script_dir, 'ready_for_map_import.csv')
        clean_df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"\n🎉 Success! {len(clean_df)} pristine restaurants saved to 'ready_for_map_import.csv'.")
    else:
        print("\n🤷 All new restaurants were duplicates of existing map data.")

if __name__ == "__main__":
    run_merge()