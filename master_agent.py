import os
import requests
import time
import csv
from dotenv import load_dotenv

# Import our agent tools
from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import evaluate_restaurant

load_dotenv()
KAKAO_API_KEY = os.getenv("KAKAO_REST_API_KEY")

# ==========================================
# ⚙️ BATCH CONFIGURATION QUEUE
# ==========================================
NEIGHBORHOODS = ["홍대", "연남동"]
KEYWORDS = ["치킨", "치맥"]
MAX_PLACES_PER_SEARCH = 5


# ==========================================

def discover_restaurants(keyword, location, max_results):
    """The Discovery Agent: Uses Kakao Maps to find raw restaurant data."""
    print(f"\n🗺️ Discovery Agent: Searching Kakao for '{location} {keyword}'...")

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_API_KEY}"}
    params = {
        "query": f"{location} {keyword}",
        "size": max_results
    }

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 200:
        places = response.json().get('documents', [])
        print(f"✅ Kakao returned {len(places)} results.")
        return places
    else:
        print(f"❌ Kakao API Error: {response.status_code}")
        return []


def run_staging_pipeline():
    """Runs the AI pipeline and saves results to a CSV for human review."""

    # We will store our rows of data here before saving
    staging_data = []
    seen_place_ids = set()

    for neighborhood in NEIGHBORHOODS:
        print(f"\n" + "=" * 50)
        print(f"📍 INITIATING SCAN: {neighborhood}")
        print(f"=" * 50)

        for keyword in KEYWORDS:
            places_to_investigate = discover_restaurants(keyword, neighborhood, MAX_PLACES_PER_SEARCH)

            for place in places_to_investigate:
                place_id = place['id']
                restaurant_name = place['place_name']

                if place_id in seen_place_ids:
                    print(f"⏭️ Skipping {restaurant_name} (Duplicate).")
                    continue

                seen_place_ids.add(place_id)
                print(f"\n🕵️ Investigating: {restaurant_name} ({neighborhood} / {keyword})")

                # A. Get Naver Blogs
                blog_results = search_naver_blogs(restaurant_name, neighborhood)
                if not blog_results:
                    continue

                scraped_texts = []
                for blog in blog_results:
                    url = blog['link']
                    text = scrape_naver_blog_text(url)
                    if text:
                        scraped_texts.append(text)
                    time.sleep(1)

                if not scraped_texts:
                    print("⚠️ Not enough readable data. Skipping.")
                    continue

                # B. Send to Gemini for Scoring
                evaluation = evaluate_restaurant(restaurant_name, scraped_texts)

                # C. Save EVERYTHING to the Staging Queue (Even low scores, so you can see why it failed)
                if evaluation:
                    score = evaluation.get('score', 0)
                    print(f"🎯 AI Scored {restaurant_name}: {score}/100")

                    # Create a dictionary for our CSV row
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
                    staging_data.append(row)
                else:
                    print(f"❌ AI failed to evaluate {restaurant_name}.")

                time.sleep(3)

    # 4. Save to CSV for Excel/Google Sheets Review
    if staging_data:
        csv_filename = 'neon_guide_review_queue.csv'
        headers = ["Neighborhood", "Keyword", "Restaurant Name", "Score", "Award Level", "AI Justification",
                   "English Desc", "Korean Desc", "Kakao URL", "Lat", "Lon"]

        # We use 'utf-8-sig' so Excel reads the Korean characters perfectly without scrambling them
        with open(csv_filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(staging_data)

        print(
            f"\n🏁 Complete! Open '{csv_filename}' in Excel or Google Sheets to review the {len(staging_data)} processed spots.")
    else:
        print("\n🏁 Complete, but no data was successfully processed.")


if __name__ == "__main__":
    run_staging_pipeline()