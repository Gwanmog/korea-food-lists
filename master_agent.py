import os
import requests
import time
import random
import csv
import sys
import subprocess
from datetime import datetime, timedelta
from dotenv import load_dotenv

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

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
    "청담동",
    "삼성동"
]
# 🎯 THE TARGET DICTIONARY
# Format: "Kakao Search Bait": ("Gemini Master Target", Strict_Mode_Boolean)
KEYWORDS = {
    "치맥": ("치맥", False),
    "삼겹살": ("삼겹살", False),
    "한우": ("한우구이", False),
    "비빔밥": ("비빔밥", False),
    "짜장면": ("짜장면", False),
    "곱창": ("곱창", False),
    "냉면": ("냉면", False),
    "카페": ("카페", False),
    "갈비": ("갈비", False),
    "순대국밥": ("순대국밥", False),
}

MAX_PLACES_PER_SEARCH = 45
CSV_FILENAME = os.path.join(script_dir, 'neon_guide_review_queue.csv')
QUEUE_FILE = CSV_FILENAME  # alias for import by reclassify.py
SKIP_CACHE_FILE = os.path.join(script_dir, 'skip_cache.csv')
SKIP_CACHE_TTL_DAYS = 365

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
        "Sponsored Ratio", "Auditor Comments", "Rating Justified", "Auditor Reason",
        "Needs Manual Review", "Upgrade Recommended"
    ]

    file_exists = os.path.isfile(CSV_FILENAME)

    with open(CSV_FILENAME, 'a', newline='', encoding='utf-8-sig') as f:
        # 🚨 THE FIX: added extrasaction='ignore' just in case of dict mismatches
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        if not file_exists:
            writer.writeheader()

        # Write the row. The dictionary won't have the 4 new keys, so DictWriter will automatically leave those CSV cells blank!
        writer.writerow(row_dict)


def load_skip_cache():
    """Load (restaurant_name, keyword) pairs blocked within the TTL window."""
    blocked = set()
    if not os.path.isfile(SKIP_CACHE_FILE):
        return blocked
    cutoff = datetime.now() - timedelta(days=SKIP_CACHE_TTL_DAYS)
    with open(SKIP_CACHE_FILE, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                last_attempted = datetime.strptime(row['Last Attempted'], '%Y-%m-%d')
                if last_attempted >= cutoff:
                    blocked.add((row['Restaurant Name'], row['Keyword']))
            except (ValueError, KeyError):
                pass
    print(f"🚫 Skip Cache: {len(blocked)} (restaurant, keyword) pairs blocked within TTL.")
    return blocked


def write_skip_cache(restaurant_name, keyword, reason):
    """Record a failed (restaurant, keyword) pair so it won't be re-scraped within the TTL."""
    file_exists = os.path.isfile(SKIP_CACHE_FILE)
    with open(SKIP_CACHE_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=['Restaurant Name', 'Keyword', 'Last Attempted', 'Reason'])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            'Restaurant Name': restaurant_name,
            'Keyword': keyword,
            'Last Attempted': datetime.now().strftime('%Y-%m-%d'),
            'Reason': reason,
        })


def load_existing_restaurants():
    """Reads ALL CSVs to memorize places we've already scored across multiple runs."""
    seen_names = set()

    # Check all three stages of the pipeline so we never re-scrape anything
    files_to_check = [
        CSV_FILENAME,  # 1. The live staging queue
        os.path.join(script_dir, 'neon_guide_audited_final.csv'),  # 2. The clean production list
        os.path.join(script_dir, 'needs_human_attention.csv')  # 3. The quarantine pile
    ]

    for file_path in files_to_check:
        if os.path.isfile(file_path):
            with open(file_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row.get("Restaurant Name")
                    if name:
                        seen_names.add(name)

    print(f"🧠 Master Agent Memory: {len(seen_names)} previously seen places will be skipped.")
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
    skip_cache = load_skip_cache()

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

                # Check skip cache for this specific (restaurant, keyword) pair.
                # A restaurant blocked on one keyword can still be picked up by another.
                if (restaurant_name, search_bait) in skip_cache:
                    print(f"⏭️ Skip Cache: {restaurant_name} blocked for '{search_bait}' (tried within last year).")
                    continue

                print(f"\n🕵️ Investigating: {restaurant_name} ({neighborhood} / {search_bait})")

                # --- A. Get Naver Blogs ---
                blog_results = search_naver_blogs(restaurant_name, neighborhood)
                if not blog_results:
                    continue

                # 🚀 THE FAST-PASS FILTER 🚀
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
                    # Cache this (name, keyword) pair — but don't add to seen_places so
                    # other keywords can still discover this restaurant in the same run.
                    write_skip_cache(restaurant_name, search_bait, 'fast_pass_fail')
                    skip_cache.add((restaurant_name, search_bait))
                    continue
                # 🚀 ------------------------ 🚀

                # Claim the restaurant now to prevent double-scoring under other keywords this run.
                seen_places.add(restaurant_name)

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
                        "Keyword": master_target,
                        "Restaurant Name": restaurant_name,
                        "Score": score,
                        "Award Level": evaluation.get('award_level', 'None'),
                        "AI Justification": evaluation.get('justification', ''),
                        "English Desc": evaluation.get('description_en', ''),
                        "Korean Desc": evaluation.get('description_ko', ''),
                        "Kakao URL": place.get('place_url', ''),
                        "Lat": place.get('y', ''),
                        "Lon": place.get('x', ''),
                        "Sponsored Ratio": evaluation.get('sponsored_ratio', ''),
                    }

                    append_to_csv(row)

                    # Cache below-threshold scores so we don't re-scrape them next run
                    # under the same keyword. They can still surface via other keywords.
                    if score < 70:
                        write_skip_cache(restaurant_name, search_bait, 'below_threshold')
                        skip_cache.add((restaurant_name, search_bait))
                else:
                    # Gemini failed transiently — don't cache, allow a clean retry next run.
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

    # 3.2. Trigger the Reclassification Engine
    print("\n" + "=" * 50)
    print("🔄 PHASE 3.2: Running Reclassification Engine...")
    print("=" * 50 + "\n")
    try:
        reclassify_result = subprocess.run([sys.executable, "reclassify.py"], check=False)
        if reclassify_result.returncode == 1:
            # Promotions occurred — re-run receipt_auditor + final_verdict to process them
            print("\n>>> Promotions detected. Re-running receipt auditor on promoted entries...")
            subprocess.run([sys.executable, "receipt_auditor.py"], check=False)
            print("\n>>> Re-running final_verdict to incorporate promoted entries...")
            subprocess.run([sys.executable, "final_verdict.py"], check=False)
    except FileNotFoundError:
        print("\n❌ Could not find 'reclassify.py'.")

    # 3.5. Trigger the Appellate Court
    print("\n" + "=" * 50)
    print("⚖️ PHASE 3.5: Activating the Appellate Court for Quarantined Data...")
    print("=" * 50 + "\n")
    try:
        subprocess.run([sys.executable, "appellate_court.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Appellate Court failed: {e}")
    except FileNotFoundError:
        print("\n❌ Could not find 'appellate_court.py'.")

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
        import re as _re, csv as _csv

        def _next_patch_ma():
            r = subprocess.run(['git', 'log', '--oneline', '-1'],
                               capture_output=True, text=True, encoding='utf-8', cwd=script_dir)
            if not r.stdout:
                return 'next'
            m = _re.search(r'Patch ([\d]+)\.([\d]+)', r.stdout)
            if m:
                return f"{m.group(1)}.{int(m.group(2)) + 1}"
            return 'next'

        def _count_csv_ma(filename):
            p = os.path.join(script_dir, filename)
            if not os.path.exists(p):
                return 0
            with open(p, encoding='utf-8-sig') as f:
                return sum(1 for _ in _csv.DictReader(f))

        patch = _next_patch_ma()
        total_clean = _count_csv_ma('neon_guide_audited_final.csv')
        total_quar = _count_csv_ma('needs_human_attention.csv')
        hoods_swept = ', '.join(NEIGHBORHOODS)

        commit_msg = (
            f"Patch {patch}: Web sweep — {hoods_swept}\n\n"
            f"{total_clean} restaurants in guide | {total_quar} in quarantine\n\n"
            f"Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
        )

        subprocess.run(["git", "add",
            "site/places.geojson",
            "data/restaurant_vectors.index",
            "neon_guide_audited_final.csv",
            "needs_human_attention.csv",
        ], check=True, cwd=script_dir)

        # Git commit throws a non-zero exit code if there are no changes.
        commit_process = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True, text=True, cwd=script_dir
        )

        if commit_process.returncode == 0:
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