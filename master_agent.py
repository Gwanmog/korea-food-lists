import os
import requests
import time
import random
import csv
import sys
import subprocess
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
    "잠실동", "방이동","송파동", "석촌동", "삼전동"
]
# 🎯 THE TARGET DICTIONARY
# Format: "Kakao Search Bait": ("Gemini Master Target", Strict_Mode_Boolean)
KEYWORDS = {
    # Late Night & Chicken
    "치맥": ("치맥", False),
    "술집": ("술집", False),

    # Traditional Grill
    "삼겹살": ("삼겹살", False),
    "돼지갈비": ("돼지갈비", False),
    "고기집": ("고기집", False),

    # Noodle Staples
    "칼국수": ("칼국수", False),
    "멸치국수": ("국수", False),
    "냉면": ("냉면", False),
    "국수": ("국수", False),

    # Modern Comfort
    "돈까스": ("돈까스", False)
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

    # 🚨 THE FIX: Added the 4 new Auditor columns to the master list
    headers = [
        "Neighborhood", "Keyword", "Restaurant Name", "Score", "Award Level",
        "AI Justification", "English Desc", "Korean Desc", "Kakao URL", "Lat", "Lon",
        "Auditor Comments", "Rating Justified", "Auditor Reason", "Needs Manual Review", "Upgrade Recommended"
    ]

    file_exists = os.path.isfile(CSV_FILENAME)

    with open(CSV_FILENAME, 'a', newline='', encoding='utf-8-sig') as f:
        # 🚨 THE FIX: added extrasaction='ignore' just in case of dict mismatches
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        if not file_exists:
            writer.writeheader()

        # Write the row. The dictionary won't have the 4 new keys, so DictWriter will automatically leave those CSV cells blank!
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
    Agile pre-filter powered by Geographic bounds and Direct Name Matching.
    """
    restaurant_name = place.get('place_name', '')
    address = place.get('address_name', '')

    # ==========================================
    # 🚨 THE DIRECT HIT LOOPHOLE
    # ==========================================
    # Remove spaces to ensure "미스터리 브루잉" matches "미스터리브루잉컴퍼니"
    search_clean = keyword.replace(" ", "")
    name_clean = restaurant_name.replace(" ", "")

    if search_clean in name_clean:
        # We don't care about the address. The name matches! Let it through.
        return True
    # ==========================================

    # 🚨 THE GEOGRAPHIC BOUNCER
    # If the name wasn't a direct match, it MUST be in the correct neighborhood.
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
                # Dynamically look for the actual food we are searching for!
                # Replace 'search_keyword' and 'target_keyword' with whatever variables your loop uses.
                fast_pass_terms = [search_bait, master_target]

                passed_fast_pass = False
                for blog in blog_results:
                    title = blog.get('title', '')
                    snippet = blog.get('description', '')

                    if any(term in title or term in snippet for term in fast_pass_terms):
                        passed_fast_pass = True
                        break  # We found proof! Stop checking snippets.

                if not passed_fast_pass:
                    print(
                        f"⏭️ Fast-Pass Failed: {restaurant_name}. No mention of '{search_bait}' or '{master_target}'. Skipping AI.")
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

                if len(scraped_texts) < 4:
                    print(
                        f"⚠️ Only {len(scraped_texts)} blogs found. Not enough data to avoid sponsored bias. Skipping.")
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
    # 1. Run the massive Kakao/Naver discovery and Gemini scoring sweep
    run_massive_pipeline()

    # 2. Trigger the Supreme Court Auditor
    print("\n" + "=" * 50)
    print("🚀 PHASE 1 COMPLETE: Handing over to the Supreme Court Auditor...")
    print("=" * 50 + "\n")

    try:
        # Forces the subprocess to stay inside the PyCharm virtual environment
        subprocess.run([sys.executable, "receipt_auditor.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ The Auditor encountered a fatal error and stopped: {e}")
    except FileNotFoundError:
        print("\n❌ Could not find 'receipt_auditor.py'. Make sure it is in the exact same folder as master_agent.py.")

    # 3. Trigger the Final Verdict Cleanup
    print("\n" + "=" * 50)
    print("🧹 PHASE 3: Running Final Verdict Audit...")
    print("=" * 50 + "\n")

    try:
        subprocess.run([sys.executable, "final_verdict.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ The Final Verdict encountered an error: {e}")
    # 4. Trigger the Deduplicator
    print("\n" + "=" * 50)
    print("👯 PHASE 4: Running Dedupe Master...")
    print("=" * 50 + "\n")
    try:
        subprocess.run([sys.executable, "dedupe_master.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Deduplicator failed: {e}")
    except FileNotFoundError:
        print("\n❌ Could not find 'dedupe_master.py'.")

    # 5. Build the Raw Map Data
    print("\n" + "=" * 50)
    print("🗺️ PHASE 5: Building places.geojson...")
    print("=" * 50 + "\n")
    try:
        subprocess.run([sys.executable, "build_map_list.py", "build"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Map Builder failed: {e}")

    # 6. Build the FAISS Index & Inject Vector IDs
    print("\n" + "=" * 50)
    print("🧠 PHASE 6: Generating AI Embeddings & FAISS Index...")
    print("=" * 50 + "\n")
    try:
        subprocess.run([sys.executable, "build_embeddings.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Embeddings Engine failed: {e}")

    # 7. Auto-Deploy to GitHub / Render
    print("\n" + "=" * 50)
    print("🚀 PHASE 7: Deploying to Production...")
    print("=" * 50 + "\n")
    try:
        # Using the exact relative paths based on your project tree
        subprocess.run(["git", "add", "site/places.geojson", "data/restaurant_vectors.index"], check=True)

        # Git commit throws a non-zero exit code if there are no changes.
        # We capture the output instead of checking=True so it doesn't crash the script.
        commit_process = subprocess.run(
            ["git", "commit", "-m", "🤖 Auto-deploy: Master Agent pipeline finished"],
            capture_output=True, text=True
        )

        if commit_process.returncode == 0:
            # Changes were committed, safe to push!
            print("📦 Changes committed. Pushing to GitHub...")
            subprocess.run(["git", "push"], check=True)
            print("\n🏆 ENTIRE PIPELINE DEPLOYED SUCCESSFULLY!")
        else:
            print("🤷 No new restaurants or data changes to commit. Skipping push.")
            print("\n🏆 ENTIRE PIPELINE FINISHED SUCCESSFULLY (No updates needed)!")

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Git Deployment failed: {e}")
    except FileNotFoundError:
        print("\n❌ Git is not installed or not in the system PATH.")