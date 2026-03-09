import os
import json
import csv
import time
import pandas as pd
from typing import Any
from google import genai
from google.genai import types
from dotenv import load_dotenv
from naver_agent import search_naver_blogs, scrape_naver_blog_text
from critic_agent import get_image_bytes

# 1. Setup and Auth
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

QUARANTINE_FILE = 'needs_human_attention.csv'
CLEAN_FILE = 'neon_guide_audited_final.csv'


def deep_data_sweep(restaurant_name, neighborhood, target_count=12):
    """Scrapes a massive amount of blogs to get a definitive consensus.
    Returns (combined_text, image_bytes) — image_bytes is the first bottom image found, or None."""
    print(f"   🕵️ Initiating Deep Sweep for {restaurant_name}...")
    blog_results = search_naver_blogs(restaurant_name, neighborhood)

    if not blog_results:
        return "", None

    safe_texts = []
    image_bytes = None

    for blog in blog_results[:target_count]:
        url = blog['link']
        blog_data = scrape_naver_blog_text(url)
        if blog_data and "text" in blog_data:
            clean_text = blog_data["text"].replace('"', "'").replace('\n', ' ').replace('\t', ' ')
            safe_texts.append(clean_text[:5000])

            # Grab the first usable bottom image for visual sponsorship detection
            if image_bytes is None:
                for img_url in blog_data.get("bottom_images", []):
                    image_bytes = get_image_bytes(img_url)
                    if image_bytes:
                        break

        time.sleep(1)  # Be polite to Naver

    return " --- NEXT REVIEW --- ".join(safe_texts), image_bytes


def run_appellate_court():
    if not os.path.exists(QUARANTINE_FILE):
        print("🎉 No quarantined restaurants found. The Appellate Court is adjourned.")
        return

    df_quarantine = pd.read_csv(QUARANTINE_FILE)
    if df_quarantine.empty:
        print("🎉 Quarantine file is empty. Adjourned.")
        return

    print(f"⚖️ The Appellate Court is now in session. Reviewing {len(df_quarantine)} cases...")

    # Trackers
    pardoned_rows = []
    remaining_quarantine = []

    for idx, row in df_quarantine.iterrows():
        restaurant_name = str(row.get('Restaurant Name', 'Unknown'))
        neighborhood = str(row.get('Neighborhood', 'Seoul'))
        original_score = row.get('Score', 0)
        auditor_reason = str(row.get('Auditor Reason', 'Unknown Anomaly'))
        original_justification = str(row.get('AI Justification', ''))

        print(f"\n" + "-" * 50)
        print(f"🏛️ CASE: {restaurant_name}")
        print(f"📌 LOWER COURT SCORE: {original_score}")
        print(f"⚠️ AUDITOR REASON: {auditor_reason}")

        # 1. Skip things that are broken beyond repair (like scraper failures)
        if "rescrape" in str(row.get('Needs Manual Review', '')).lower():
            print("⏭️ Status is 'Rescrape'. Needs code fixes, not AI logic. Leaving in quarantine.")
            remaining_quarantine.append(row)
            continue

        # 2. Gather the Deep Evidence
        massive_text, image_bytes = deep_data_sweep(restaurant_name, neighborhood, target_count=10)
        if len(massive_text) < 1000:
            print("⚠️ Deep sweep failed to find enough data. Leaving in quarantine.")
            remaining_quarantine.append(row)
            continue

        # 3. The Chief Justice Prompt
        instruction = f"""
        You are the Chief Justice of the Neon Guide rating system. You are a skeptical, rigorous critic — not a cheerleader.

        A restaurant named '{restaurant_name}' was flagged for appeal.
        - Original Score Given: {original_score}/100
        - Original Justification: {original_justification}
        - Reason for Appeal: {auditor_reason}

        Your job is to read a large sample of customer blog posts and produce a fair, evidence-based score.

        =========================================
        STEP 1: SPONSORSHIP AUDIT (DO THIS FIRST)
        =========================================
        Check for Korean sponsored content disclosures using TWO methods:

        - Text Check: Scan every blog post for disclosure phrases:
          소정의 원고료, 제품을 제공받아, 협찬, 지원받아, 무료로 제공, 서포터즈, 체험단
        - Image Check: If an image is attached, examine it visually for the same disclosure
          phrases (협찬, 제공받아, etc.) — many blogs use image-based disclosures instead of text.

        Count how many posts are sponsored (by text OR image).
        Calculate: sponsored_ratio = sponsored posts / total posts reviewed.

        SPONSORSHIP RULE: If sponsored_ratio exceeds 50%, the final score MUST NOT exceed 84.
        Sponsored blogs are marketing, not genuine customer feedback. Do not let them inflate the score.

        =========================================
        STEP 2: SCORE ON 5 CRITERIA (20 points each, 100 total)
        =========================================
        1. ingredients — Quality and sourcing of raw materials
        2. technique — Mastery of cooking, flavor, texture, temperature control
        3. personality — Uniqueness of the chef or concept vs. generic
        4. value — Price relative to quality and portion
        5. consistency — Evidence of long-term reputation and repeat customers

        Be strict. A score of 80+ means genuinely good. A score of 90+ means exceptional.
        Do not award high scores just because reviews are positive — evaluate the SUBSTANCE of what they say.

        =========================================
        STEP 3: AWARD LEVEL
        =========================================
        - 95-100: 3 Neon Hearts (destination-worthy, near-flawless)
        - 88-94: 2 Neon Hearts (exceptional neighborhood staple)
        - 80-87: 1 Neon Heart (genuinely good, minor flaws)
        - 70-79: None (decent, worth visiting but not award-worthy)
        - <70: None (average or worse — do not pardon)

        Output ONLY valid JSON:
        {{
            "sponsored_ratio": "X/Y sponsored",
            "score_breakdown": {{
                "ingredients": (int 0-20),
                "technique": (int 0-20),
                "personality": (int 0-20),
                "value": (int 0-20),
                "consistency": (int 0-20)
            }},
            "final_score": (int, MUST equal exact sum of the 5 categories, and MUST NOT exceed 84 if sponsored_ratio > 50%),
            "award_level": "string",
            "appellate_justification": "1-2 sentences citing specific evidence from the reviews."
        }}
        """

        try:
            print("   🧠 Asking Chief Justice Gemini for a final verdict...")
            payload_contents: list[Any] = [f"Deep Data for {restaurant_name}:\n\n{massive_text}"]
            if image_bytes:
                payload_contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
                print("   🖼️ Bottom image attached for visual sponsorship check.")

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=payload_contents,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )

            verdict = json.loads(response.text)
            new_score = verdict.get('final_score', 0)

            sponsored = verdict.get('sponsored_ratio', 'unknown')
            print(f"   ✅ VERDICT REACHED. New Score: {new_score}/100 | Sponsorship: {sponsored}")
            print(f"   📝 Justification: {verdict.get('appellate_justification')}")

            # 4. Process the Verdict
            row['Score'] = new_score
            row['Award Level'] = verdict.get('award_level', 'None')
            row['AI Justification'] = verdict.get('appellate_justification', '')

            # Only pardon it if the new score is decent. If it's garbage, leave it in quarantine.
            if new_score >= 70:
                row['Needs Manual Review'] = 'False'
                pardoned_rows.append(row)
                print("   🕊️ PARDONED: Moving back to the clean deployment list.")
            else:
                print("   🛑 REJECTED: Final score is too low. Keeping in quarantine.")
                remaining_quarantine.append(row)

        except Exception as e:
            print(f"   ❌ Chief Justice failed: {e}. Leaving in quarantine.")
            remaining_quarantine.append(row)

    # ==========================================
    # FILE MANAGEMENT
    # ==========================================
    # 1. Update the Quarantine File
    if remaining_quarantine:
        pd.DataFrame(remaining_quarantine).to_csv(QUARANTINE_FILE, index=False, encoding='utf-8-sig')
        print(f"\n⚠️ {len(remaining_quarantine)} places remain in {QUARANTINE_FILE}.")
    else:
        # If empty, delete the file or clear it
        os.remove(QUARANTINE_FILE)
        print(f"\n🎉 Quarantine file is completely clear!")

    # 2. Merge Pardoned Rows back into the Clean File
    if pardoned_rows:
        df_clean = pd.read_csv(CLEAN_FILE)
        df_pardoned = pd.DataFrame(pardoned_rows)

        # Concat and save
        df_final = pd.concat([df_clean, df_pardoned], ignore_index=True)
        df_final.to_csv(CLEAN_FILE, index=False, encoding='utf-8-sig')
        print(f"✅ Successfully appended {len(pardoned_rows)} pardoned spots to {CLEAN_FILE}.")


if __name__ == "__main__":
    run_appellate_court()