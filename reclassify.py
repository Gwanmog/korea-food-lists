"""
reclassify.py

Scans needs_human_attention.csv for entries flagged Reclassify Eligible = True.
For each, asks Gemini what the restaurant's TRUE primary food identity is based
on its AI-generated description — no re-scraping needed for this step.

If the true category differs from the original keyword, re-scrapes Naver blogs
and re-scores under the corrected category. Restaurants that hit 85+ are injected
back into neon_guide_review_queue.csv for a clean second pass through the full
receipt audit + final verdict pipeline.

Reclassify Eligible status is updated in-place:
  True      → still pending
  Promoted  → re-scored 85+, injected into queue
  No Match  → same category, unknown, or re-score still below 85
"""

import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import os
import csv
import json
import time
import random
import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import types

from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import evaluate_restaurant
from master_agent import write_skip_cache, QUEUE_FILE as MASTER_QUEUE_FILE

script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

QUARANTINE_FILE = os.path.join(script_dir, 'needs_human_attention.csv')
QUEUE_FILE = MASTER_QUEUE_FILE
PROMOTION_THRESHOLD = 85


def get_true_category(restaurant_name, original_keyword, description_en, justification):
    """
    Ask Gemini what this restaurant is TRULY known for, using only its
    AI-generated description and justification — not its discovery keyword.
    """
    prompt = f"""You are a Seoul food expert.

Restaurant: {restaurant_name}
Originally discovered under keyword: {original_keyword}
AI Description: {description_en}
AI Justification: {justification}

Based on the name and description ONLY — ignoring the discovery keyword —
what is the single PRIMARY Korean food category this restaurant is truly known for?

Rules:
- Return ONLY a valid JSON object with one key: "true_category"
- The value must be a specific Korean food category (e.g. "돈까스", "냉면", "갈비탕")
- If the true category is clearly the same as the original keyword, return the original keyword exactly
- If you genuinely cannot determine it, return "unknown"

Example: {{"true_category": "돈까스"}}"""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        result = json.loads(response.text)
        return result.get('true_category', 'unknown').strip()
    except Exception as e:
        print(f"   ❌ Gemini classification failed for {restaurant_name}: {e}")
        return 'unknown'


def rescrape_and_score(restaurant_name, neighborhood, new_category):
    """Re-scrape Naver blogs and re-score the restaurant under its corrected category."""
    blog_results = search_naver_blogs(restaurant_name, neighborhood)
    if not blog_results:
        print(f"   ⚠️ No Naver blogs found for {restaurant_name}.")
        return None

    scraped_texts = []
    for blog in blog_results:
        text = scrape_naver_blog_text(blog['link'])
        if text:
            scraped_texts.append(text)
        time.sleep(random.uniform(1.5, 3.0))

    if len(scraped_texts) < 4:
        print(f"   ⚠️ Only {len(scraped_texts)} blogs scraped — not enough to avoid sponsored bias.")
        return None

    return evaluate_restaurant(restaurant_name, scraped_texts, new_category)


def append_to_queue(row_dict):
    """Append a reclassified restaurant to the master review queue for re-auditing."""
    headers = [
        "Neighborhood", "Keyword", "Restaurant Name", "Score", "Award Level",
        "AI Justification", "English Desc", "Korean Desc", "Kakao URL", "Lat", "Lon",
        "Sponsored Ratio", "Auditor Comments", "Rating Justified", "Auditor Reason",
        "Needs Manual Review", "Upgrade Recommended"
    ]
    file_exists = os.path.isfile(QUEUE_FILE)
    with open(QUEUE_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row_dict)


def run_reclassification():
    if not os.path.exists(QUARANTINE_FILE):
        print("No quarantine file found. Nothing to reclassify.")
        return 0

    df = pd.read_csv(QUARANTINE_FILE, encoding='utf-8-sig')

    if 'Reclassify Eligible' not in df.columns:
        print("No Reclassify Eligible column found. Run final_verdict.py first.")
        return 0

    eligible_mask = df['Reclassify Eligible'].astype(str).str.strip() == 'True'
    eligible = df[eligible_mask]
    print(f"🔄 Reclassification Engine: {len(eligible)} candidates found.")

    if eligible.empty:
        print("Nothing to reclassify.")
        return 0

    promoted = 0

    for idx, row in eligible.iterrows():
        name = str(row.get('Restaurant Name', '')).strip()
        neighborhood = str(row.get('Neighborhood', '')).strip()
        # Support both column name variants across pipeline versions
        original_keyword = str(row.get('Keyword', row.get('Category', ''))).strip()
        description_en = str(row.get('English Desc', row.get('Description EN', ''))).strip()
        justification = str(row.get('AI Justification', row.get('Justification', ''))).strip()

        print(f"\n🔍 [{promoted + 1}] {name} — original keyword: '{original_keyword}'")

        # ── Step 1: Ask Gemini for the true category ──────────────────────────
        true_category = get_true_category(name, original_keyword, description_en, justification)
        print(f"   🏷️  Gemini says: '{true_category}'")

        if true_category == 'unknown' or true_category.lower() == original_keyword.lower():
            print(f"   ⏭️  No reclassification. Marking No Match.")
            df.at[idx, 'Reclassify Eligible'] = 'No Match'
            df.to_csv(QUARANTINE_FILE, index=False, encoding='utf-8-sig')
            continue

        # ── Step 2: Re-scrape Naver and re-score ──────────────────────────────
        print(f"   📰 Re-scraping under '{true_category}'...")
        evaluation = rescrape_and_score(name, neighborhood, true_category)

        if not evaluation:
            print(f"   ❌ Re-scrape failed. Leaving in quarantine.")
            df.at[idx, 'Reclassify Eligible'] = 'No Match'
            df.to_csv(QUARANTINE_FILE, index=False, encoding='utf-8-sig')
            continue

        new_score = evaluation.get('score', 0)
        print(f"   🎯 Re-scored as '{true_category}': {new_score}/100")

        # ── Step 3: Promote or cache ───────────────────────────────────────────
        if new_score >= PROMOTION_THRESHOLD:
            queue_row = {
                "Neighborhood": neighborhood,
                "Keyword": true_category,
                "Restaurant Name": name,
                "Score": new_score,
                "Award Level": evaluation.get('award_level', ''),
                "AI Justification": (
                    evaluation.get('justification', '') +
                    f" [Reclassified from '{original_keyword}' → '{true_category}']"
                ),
                "English Desc": evaluation.get('description_en', ''),
                "Korean Desc": evaluation.get('description_ko', ''),
                "Kakao URL": row.get('Kakao URL', ''),
                "Lat": row.get('Lat', row.get('Latitude', '')),
                "Lon": row.get('Lon', row.get('Longitude', '')),
                "Sponsored Ratio": evaluation.get('sponsored_ratio', ''),
            }
            append_to_queue(queue_row)
            df.at[idx, 'Reclassify Eligible'] = 'Promoted'
            df.to_csv(QUARANTINE_FILE, index=False, encoding='utf-8-sig')
            print(f"   ✅ PROMOTED — queued for re-audit under '{true_category}'.")
            promoted += 1
        else:
            # Still below threshold under its true category — cache both keywords
            write_skip_cache(name, original_keyword, 'below_threshold')
            write_skip_cache(name, true_category, 'below_threshold')
            df.at[idx, 'Reclassify Eligible'] = 'No Match'
            df.to_csv(QUARANTINE_FILE, index=False, encoding='utf-8-sig')
            print(f"   ❌ {new_score} still below {PROMOTION_THRESHOLD}. Both keywords cached.")

        time.sleep(random.uniform(4.0, 7.0))

    print(f"\n🏁 Reclassification complete. {promoted}/{len(eligible)} restaurants promoted.")
    return promoted


if __name__ == "__main__":
    run_reclassification()
