import os
import requests
import time
import random  # NEW: For human camouflage
import csv
from dotenv import load_dotenv

from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import evaluate_restaurant

# Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

KAKAO_API_KEY = os.getenv("KAKAO_REST_API_KEY")

# ==========================================
# ⚙️ THE SEOUL MASTER QUEUE
# Add your curated neighborhoods here!
# ==========================================
NEIGHBORHOODS = ["홍대"]
KEYWORDS = ["치킨", "치맥"]
MAX_PLACES_PER_SEARCH = 5  # "The Dial"
CSV_FILENAME = os.path.join(script_dir, 'neon_guide_review_queue.csv')


# ==========================================

def discover_restaurants(keyword, location, max_results):
    print(f"\n🗺️ Discovery Agent: Searching Kakao for '{location} {keyword}'...")
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {"query": f"{location} {keyword}", "size": max_results}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('documents', [])
        else:
            print(f"❌ Kakao API Error: {response.status_code}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Network error connecting to Kakao: {e}")
        return []


def append_to_csv(row_dict):
    """LIVE CHECKPOINTING: Saves one row to the CSV immediately."""
    headers = ["Neighborhood", "Keyword", "Restaurant Name", "Score", "Award Level", "AI Justification", "English Desc",
               "Korean Desc", "Kakao URL", "Lat", "Lon"]

    # Check if file exists so we know whether to write the header row
    file_exists = os.path.isfile(CSV_FILENAME)

    with open(CSV_FILENAME, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_dict)

def load_existing_restaurants():
    """Reads the CSV to memorize places we've already scored across multiple runs."""
    seen_names = set()
    if os.path.isfile(CSV_FILENAME):
        with open(CSV_FILENAME, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Memorize the restaurant name so we don't scrape it again
                seen_names.add(row.get("Restaurant Name"))
    return seen_names


def run_massive_pipeline():
    # Load permanent memory from the CSV!
    seen_places = load_existing_restaurants()
    print(f"🧠 Memory loaded: Skippping {len(seen_places)} previously scored spots.")

    for neighborhood in NEIGHBORHOODS:
        print(f"\n" + "=" * 50)
        print(f"📍 INITIATING SECTOR SCAN: {neighborhood}")
        print(f"=" * 50)

        for keyword in KEYWORDS:
            places_to_investigate = discover_restaurants(keyword, neighborhood, MAX_PLACES_PER_SEARCH)

            for place in places_to_investigate:
                restaurant_name = place['place_name']

                # Check permanent memory instead of just ID
                if restaurant_name in seen_places:
                    print(f"⏭️ Skipping {restaurant_name} (Already in CSV).")
                    continue

                # Add to memory so we don't hit it again on the next keyword loop
                seen_places.add(restaurant_name)

                # --- A. Get Naver Blogs ---
                blog_results = search_naver_blogs(restaurant_name, neighborhood)
                if not blog_results:
                    continue

                scraped_texts = []
                for blog in blog_results:
                    url = blog['link']
                    text = scrape_naver_blog_text(url)
                    if text:
                        scraped_texts.append(text)

                    # RANDOMIZED JITTER: Sleep between 1.5 and 3.2 seconds
                    sleep_time = random.uniform(1.5, 3.2)
                    time.sleep(sleep_time)

                if not scraped_texts:
                    print("⚠️ Not enough readable data. Skipping.")
                    continue

                # --- B. Send to Gemini for Scoring ---
                evaluation = evaluate_restaurant(restaurant_name, scraped_texts, keyword)

                # --- C. Live Save to Staging Queue ---
                if evaluation:
                    score = evaluation.get('score', 0)
                    print(f"🎯 AI Scored {restaurant_name}: {score}/100")

                    row = {
                        "Neighborhood": neighborhood,
                        "Keyword": keyword,
                        "Restaurant Name": restaurant_name,
                        "Score": score,
                        "Award Level": evaluation.get('award_level', 'None'),
                        "AI Justification": evaluation.get('justification', ''),
                        "English Desc": evaluation.get('description_en', ''),
                        "Korean Desc": evaluation.get('description_ko', ''),
                        "Kakao URL": place.get('place_url', ''),
                        "Lat": place.get('y', ''),
                        "Lon": place.get('x', '')
                    }

                    # Save instantly! If the script crashes on the next loop, this data is safe.
                    append_to_csv(row)
                else:
                    print(f"❌ AI failed to evaluate {restaurant_name}.")

                # Big rest between restaurants to let APIs cool down
                time.sleep(random.uniform(4.0, 7.0))

    print(f"\n🏁 Massive Sweep Complete! Data safely secured in {CSV_FILENAME}.")


if __name__ == "__main__":
    run_massive_pipeline()