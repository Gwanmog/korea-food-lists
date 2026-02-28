import os
import requests
import time
import random
import csv
from dotenv import load_dotenv

from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import evaluate_restaurant, get_kakao_categories

# Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

KAKAO_API_KEY = os.getenv("KAKAO_REST_API_KEY")

# ==========================================
# ⚙️ THE SEOUL MASTER QUEUE
# ==========================================
NEIGHBORHOODS = ["홍대"]
KEYWORDS = ["치킨"]  # Try swapping this to 곱창, 비빔밥, 빈대떡, or 육회 later!
MAX_PLACES_PER_SEARCH = 10 # "The Dial"
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
                seen_names.add(row.get("Restaurant Name"))
    return seen_names


def is_strong_hit(place, keyword, valid_categories):
    """
    Agile pre-filter powered by the AI Coordinator's category list.
    """
    name = place.get('place_name', '')
    category = place.get('category_name', '')

    # 1. Direct hit in the restaurant's name
    if keyword in name:
        return True

    # 2. Direct hit in Kakao's default category
    if keyword in category:
        return True

    # 3. Dynamic check against the AI's allowed categories
    if any(valid_cat in category for valid_cat in valid_categories):
        return True

    return False


def run_massive_pipeline():
    seen_places = load_existing_restaurants()
    if seen_places:
        print(f"🧠 Memory loaded: Skipping {len(seen_places)} previously scored spots.")

    for keyword in KEYWORDS:
        print(f"\n" + "*" * 50)
        print(f"🎯 NEW TARGET: {keyword}")
        print(f"*" * 50)

        # 1. Ask Gemini for the rules of engagement for this specific food
        valid_categories = get_kakao_categories(keyword)

        for neighborhood in NEIGHBORHOODS:
            print(f"\n📍 INITIATING SECTOR SCAN: {neighborhood} ({keyword})")

            places_to_investigate = discover_restaurants(keyword, neighborhood, MAX_PLACES_PER_SEARCH)

            for place in places_to_investigate:
                restaurant_name = place['place_name']

                # 2. Hand the AI's valid_categories to the Bouncer
                if not is_strong_hit(place, keyword, valid_categories):
                    print(f"⏭️ Bouncing {restaurant_name} (Category mismatch: {place.get('category_name')}).")
                    continue

                if restaurant_name in seen_places:
                    print(f"⏭️ Skipping {restaurant_name} (Already in CSV).")
                    continue

                seen_places.add(restaurant_name)
                print(f"\n🕵️ Investigating: {restaurant_name} ({neighborhood} / {keyword})")

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

                    # Human Jitter
                    time.sleep(random.uniform(1.5, 3.2))

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

                    append_to_csv(row)
                else:
                    print(f"❌ AI failed to evaluate {restaurant_name}.")

                time.sleep(random.uniform(4.0, 7.0))

    print(f"\n🏁 Massive Sweep Complete! Data safely secured in {CSV_FILENAME}.")


if __name__ == "__main__":
    run_massive_pipeline()