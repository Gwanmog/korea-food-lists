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
# ⚙️ THE SEOUL MASTER QUEUE (ALL 25 DISTRICTS)
# ==========================================
NEIGHBORHOODS = [
    "서교동", "창천동", "대현동", "연남동", "망원동", "합정동", "상수동", "공덕동", "상암동",
    "이태원동", "용산동2가",
    "권농동", "익선동", "삼청동", "을지로", "명동", "신당동", "창신동",
    "종로3가", "돈의동", "낙원동", "충무로", "필동", "광희동", "을지로6가",
    "역삼동", "서초동", "압구정동", "신사동", "성수동", "마장동",
    "문래동", "정릉동"
]
# 🎯 THE TARGET DICTIONARY
# Format: "Kakao Search Bait": ("Gemini Master Target", Strict_Mode_Boolean)
KEYWORDS = {
    # The Fried Chicken Essentials
    "치킨": ("치킨", False),
    "닭강정": ("치킨", False),
    "양념치킨": ("치킨", False),

    # The Casual Street Food & Market Snacking
    "떡볶이": ("분식", False),
    "김밥": ("분식", False),
    "튀김": ("분식", False),
    "호떡": ("디저트", False),
    "빈대떡": ("전", False),
    "오뎅": ("분식", False),

    # Hangover & Soul Food (The Working-Class Heroes)
    "국밥": ("국밥", False),
    "감자탕": ("감자탕", False),
    "제육볶음": ("백반", False),

    # The Spring Seasonal Exclusive
    "쭈꾸미": ("해산물", False),

    # Soju Tents & Late Night
    "곱창": ("곱창", False),
    "육회": ("육회", False),

    # Trendy Desserts
    "두바이 초콜릿": ("디저트", False)
}

MAX_PLACES_PER_SEARCH = 45
CSV_FILENAME = os.path.join(script_dir, 'neon_guide_review_queue.csv')

# ==========================================

def discover_restaurants(keyword, location, max_results):
    """
    Paginated Discovery Agent: Sweeps up to 3 pages (45 results)
    to prevent true hotspots from being buried by Kakao's SEO keyword ranking.
    """
    print(f"\n🗺️ Discovery Agent: Deep-sweeping Kakao for '{location} {keyword}'...")
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}

    all_places = []

    # Sweep Page 1, Page 2, and Page 3 (15 items per page)
    for page in range(1, 4):
        params = {
            "query": f"{location} {keyword}",
            "size": 15, # Kakao's strict limit per page
            "page": page
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                documents = data.get('documents', [])
                all_places.extend(documents)

                # If we hit the end of Kakao's database before page 3, break early
                if data.get('meta', {}).get('is_end', True):
                    break
            else:
                print(f"❌ Kakao API Error on page {page}: {response.status_code}")
                break
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Network error connecting to Kakao: {e}")
            break

    print(f"✅ Recovered {len(all_places)} spots from the Kakao depths.")
    return all_places[:max_results]


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


def is_strong_hit(place, keyword, valid_categories, expected_neighborhood):
    """
    Agile pre-filter powered ONLY by Geographic bounds.
    Trusts the Naver Fast-Pass to filter generic bars,
    and the AI Lie Detector to penalize corporate franchises.
    """
    address = place.get('address_name', '')

    # 🚨 THE GEOGRAPHIC BOUNCER (The only rule we need here!)
    if expected_neighborhood not in address:
        return False

    # If it is physically inside the neighborhood, let the AI pipeline judge it!
    return True


def run_massive_pipeline():
    seen_places = load_existing_restaurants()

    # 🔄 Unpack all three variables!
    for search_bait, (master_target, is_strict) in KEYWORDS.items():
        print(f"\n" + "*" * 50)
        print(f"🎯 BAIT: {search_bait} | TARGET: {master_target} | STRICT: {is_strict}")
        print(f"*" * 50)

        # Pass the strict flag to the Coordinator
        valid_categories = get_kakao_categories(search_bait, strict_mode=is_strict)

        for neighborhood in NEIGHBORHOODS:
            print(f"\n📍 INITIATING SECTOR SCAN: {neighborhood} ({search_bait})")

            # Use SEARCH BAIT for Kakao
            places_to_investigate = discover_restaurants(search_bait, neighborhood, MAX_PLACES_PER_SEARCH)

            for place in places_to_investigate:
                restaurant_name = place['place_name']

                # 🚀 Pass 'neighborhood' to the Bouncer and print the result!
                if not is_strong_hit(place, search_bait, valid_categories, neighborhood):
                    print(f"⏭️ Bouncing {restaurant_name} (Category or Geography mismatch).")
                    continue

                if restaurant_name in seen_places:
                    print(f"⏭️ Skipping {restaurant_name} (Already in CSV).")
                    continue

                seen_places.add(restaurant_name)
                # FIX 1: Use search_bait instead of keyword in the print statement
                print(f"\n🕵️ Investigating: {restaurant_name} ({neighborhood} / {search_bait})")

                # --- A. Get Naver Blogs ---
                blog_results = search_naver_blogs(restaurant_name, neighborhood)
                if not blog_results:
                    continue

                # 🚀 THE FAST-PASS FILTER 🚀
                # Check if the target vibe is even mentioned in the blog titles/snippets
                # If we are looking for craft beer, we look for key terms.
                fast_pass_terms = ["수제맥주", "크래프트", "브루어리", "양조장", "에일", "IPA"]

                passed_fast_pass = False
                for blog in blog_results:
                    title = blog.get('title', '')
                    snippet = blog.get('description', '')

                    if any(term in title or term in snippet for term in fast_pass_terms):
                        passed_fast_pass = True
                        break  # We found proof! Stop checking snippets.

                if not passed_fast_pass:
                    print(
                        f"⏭️ Fast-Pass Failed: {restaurant_name}. No mention of target keywords in top 10 blog titles. Skipping AI.")
                    continue
                # 🚀 ------------------------ 🚀

                print(f"✅ Fast-Pass Passed! Scraping full blogs for {restaurant_name}...")

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
                evaluation = evaluate_restaurant(restaurant_name, scraped_texts, master_target)

                # --- C. Live Save to Staging Queue ---
                if evaluation:
                    score = evaluation.get('score', 0)
                    print(f"🎯 AI Scored {restaurant_name}: {score}/100")

                    row = {
                        "Neighborhood": neighborhood,
                        # FIX 2: Save it under the master_target so your lists stay clean!
                        "Keyword": master_target,
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