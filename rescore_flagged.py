"""
rescore_flagged.py

Re-scores all restaurants in neon_guide_audited_final.csv and needs_human_attention.csv
where Rating Justified == 'No' or Needs Manual Review == True.

Uses the updated critic_agent.py (Gemini-only, with red_flags and experience category).
Re-scrapes fresh Naver blogs for each restaurant before re-scoring.

Output: <source>_rescored.csv (safe copy), then prompts to overwrite.
"""

import os
import sys
import time
import pandas as pd
from dotenv import load_dotenv

# Force UTF-8 output on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(script_dir, 'soul-food-api', '.env'))

from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import evaluate_restaurant

AWARD_ORDER = {"3 Neon Hearts": 3, "2 Neon Hearts": 2, "1 Neon Heart": 1, "None": 0}

CSV_FILES = [
    os.path.join(script_dir, 'neon_guide_audited_final.csv'),
    os.path.join(script_dir, 'needs_human_attention.csv'),
]

MIN_BLOGS = 3  # Minimum blog posts required to attempt a re-score


def get_blogs_for_restaurant(restaurant_name, neighborhood):
    """Scrape Naver blogs and return list of {text, bottom_images} dicts."""
    blog_results = search_naver_blogs(restaurant_name, neighborhood)
    if not blog_results:
        return []

    scraped = []
    for blog in blog_results:
        url = blog.get('link', '')
        if not url:
            continue
        result = scrape_naver_blog_text(url)
        if result and result.get('text'):
            scraped.append(result)
        if len(scraped) >= 10:
            break

    return scraped


def tier_from_award(award_str):
    return AWARD_ORDER.get(str(award_str).strip(), 0)


def process_csv(csv_path):
    if not os.path.exists(csv_path):
        print(f"⚠️  File not found, skipping: {csv_path}")
        return

    df = pd.read_csv(csv_path, encoding='utf-8-sig')

    flagged_mask = (df['Rating Justified'] == 'No') | (df['Needs Manual Review'] == True)
    flagged_indices = df[flagged_mask].index.tolist()

    print(f"\n{'='*60}")
    print(f"📂 {os.path.basename(csv_path)}")
    print(f"   Total rows: {len(df)} | Flagged for re-score: {len(flagged_indices)}")
    print(f"{'='*60}")

    if not flagged_indices:
        print("   ✅ Nothing to re-score.")
        return

    changed = 0
    tier_drops = 0
    skipped_no_blogs = 0

    for i, idx in enumerate(flagged_indices):
        row = df.loc[idx]
        restaurant_name = str(row['Restaurant Name'])
        neighborhood = str(row['Neighborhood'])
        category = str(row['Category'])
        old_score = row['Score']
        old_award = str(row.get('Award Level', 'None')).strip()

        print(f"\n[{i+1}/{len(flagged_indices)}] {restaurant_name} ({neighborhood} / {category})")
        print(f"   Old score: {old_score} | Old award: {old_award}")

        blogs = get_blogs_for_restaurant(restaurant_name, neighborhood)

        if len(blogs) < MIN_BLOGS:
            print(f"   ⚠️  Only {len(blogs)} blogs found (need {MIN_BLOGS}). Skipping re-score.")
            skipped_no_blogs += 1
            continue

        print(f"   📝 {len(blogs)} blogs found. Sending to critic...")
        result = evaluate_restaurant(restaurant_name, blogs, category)

        if result is None:
            print(f"   ❌ Gemini failed. Keeping original score.")
            continue

        new_score = result.get('score', old_score)
        new_award = result.get('award_level', old_award)
        red_flags = result.get('red_flags', [])
        delta = new_score - old_score

        old_tier = tier_from_award(old_award)
        new_tier = tier_from_award(new_award)
        tier_changed = new_tier != old_tier

        print(f"   New score: {new_score} (delta: {delta:+d}) | New award: {new_award}")
        if red_flags:
            print(f"   🚩 Red flags: {red_flags}")
        if tier_changed:
            direction = "⬆️ UPGRADE" if new_tier > old_tier else "⬇️ DOWNGRADE"
            print(f"   {direction}: {old_award} → {new_award}")
            tier_drops += 1

        # Update the row
        df.at[idx, 'Score'] = new_score
        df.at[idx, 'Award Level'] = new_award
        df.at[idx, 'Description EN'] = result.get('description_en', row.get('Description EN', ''))
        df.at[idx, 'Description KO'] = result.get('description_ko', row.get('Description KO', ''))
        df.at[idx, 'Justification'] = result.get('justification', row.get('Justification', ''))
        df.at[idx, 'AI Justification'] = result.get('justification', row.get('AI Justification', ''))
        # Clear the old audit fields so the auditor can re-evaluate
        df.at[idx, 'Rating Justified'] = ''
        df.at[idx, 'Auditor Comments'] = f'[Re-scored] Red flags: {red_flags}' if red_flags else '[Re-scored]'
        df.at[idx, 'Auditor Reason'] = ''
        df.at[idx, 'Needs Manual Review'] = False
        df.at[idx, 'Upgrade Recommended'] = False

        changed += 1
        time.sleep(1)  # Be kind to the Gemini API

    # Write rescored output
    base, ext = os.path.splitext(csv_path)
    out_path = f"{base}_rescored{ext}"
    df.to_csv(out_path, index=False, encoding='utf-8-sig')

    print(f"\n{'='*60}")
    print(f"✅ Done. {changed} restaurants re-scored | {tier_drops} tier changes | {skipped_no_blogs} skipped (no blogs)")
    print(f"📄 Safe copy saved to: {out_path}")
    print(f"{'='*60}")

    confirm = input(f"\nOverwrite original {os.path.basename(csv_path)} with rescored version? (y/N): ").strip().lower()
    if confirm == 'y':
        df.to_csv(csv_path, index=False, encoding='utf-8-sig')
        print(f"   ✅ Original overwritten.")
    else:
        print(f"   ℹ️  Original unchanged. Rescored version is at: {out_path}")


if __name__ == '__main__':
    print("🔄 Rescore Flagged Restaurants")
    print("   Re-scrapes Naver blogs + re-runs Gemini critic on all flagged entries.\n")

    for csv_file in CSV_FILES:
        process_csv(csv_file)

    print("\n🎯 All files processed.")
