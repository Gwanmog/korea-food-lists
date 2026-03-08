import os
import json
import csv
import time
import pandas as pd
from google import genai
from google.genai import types
from dotenv import load_dotenv
from naver_agent import search_naver_blogs, scrape_naver_blog_text

# 1. Setup and Auth
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

QUARANTINE_FILE = 'needs_human_attention.csv'
CLEAN_FILE = 'neon_guide_audited_final.csv'


def deep_data_sweep(restaurant_name, neighborhood, target_count=12):
    """Scrapes a massive amount of blogs to get a definitive consensus."""
    print(f"   🕵️ Initiating Deep Sweep for {restaurant_name}...")
    blog_results = search_naver_blogs(restaurant_name, neighborhood)

    if not blog_results:
        return ""

    safe_texts = []
    # Grab up to the target_count (way more than the Master Agent)
    for blog in blog_results[:target_count]:
        url = blog['link']
        blog_data = scrape_naver_blog_text(url)
        if blog_data and "text" in blog_data:
            clean_text = blog_data["text"].replace('"', "'").replace('\n', ' ').replace('\t', ' ')
            safe_texts.append(clean_text[:5000])  # 5k chars per blog x 12 blogs = huge context
        time.sleep(1)  # Be polite to Naver

    return " --- NEXT REVIEW --- ".join(safe_texts)


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
        massive_text = deep_data_sweep(restaurant_name, neighborhood, target_count=10)
        if len(massive_text) < 1000:
            print("⚠️ Deep sweep failed to find enough data. Leaving in quarantine.")
            remaining_quarantine.append(row)
            continue

        # 3. The Chief Justice Prompt
        instruction = f"""
        You are the Chief Justice of the Neon Guide rating system.

        A restaurant named '{restaurant_name}' was flagged by an automated auditor.
        - Original Score Given: {original_score}/100
        - Original Justification: {original_justification}
        - Auditor's Reason for Flagging: {auditor_reason}

        Your job is to read a MASSIVE sample of up to 10 customer reviews and determine the absolute truth.

        Rules:
        1. If the auditor says the AI was "too conservative" or "flawless", and the text supports this, you MUST raise the score (e.g., to the high 80s or 90s).
        2. If the text reveals extreme flaws, tank the score.
        3. Assign an award level: 95-100 (3 Neon Hearts), 88-94 (2 Neon Hearts), 80-87 (1 Neon Heart), <80 (None).

        Output ONLY valid JSON:
        {{
            "final_score": (int),
            "award_level": "string",
            "appellate_justification": "1 sentence explaining why you overturned or upheld the original score based on the deep data."
        }}
        """

        try:
            print("   🧠 Asking Chief Justice Gemini for a final verdict...")
            response = client.models.generate_content(
                model='gemini-2.5-flash',  # We can use flash here because the prompt is highly targeted
                contents=f"Deep Data for {restaurant_name}:\n\n{massive_text}",
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )

            verdict = json.loads(response.text)
            new_score = verdict.get('final_score', 0)

            print(f"   ✅ VERDICT REACHED. New Score: {new_score}/100")
            print(f"   📝 Justification: {verdict.get('appellate_justification')}")

            # 4. Process the Verdict
            row['Score'] = new_score
            row['Award Level'] = verdict.get('award_level', 'None')
            row['AI Justification'] = verdict.get('appellate_justification', '')

            # Only pardon it if the new score is decent. If it's garbage, leave it in quarantine.
            if new_score >= 80:
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