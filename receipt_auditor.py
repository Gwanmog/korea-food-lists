import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import time
import random
import os
import re
import requests
import json
from google import genai
from dotenv import load_dotenv
from google.genai import types

LOCAL_MODEL = "qwen2.5:3b"
# Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

# Initialize Gemini for the fallback
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

CSV_FILENAME = os.path.join(script_dir, 'neon_guide_review_queue.csv')


def analyze_receipts_with_fallback(restaurant_name, original_score, receipts_text):
    """Tries Qwen locally via Ollama. Falls back to Gemini if Ollama fails."""

    prompt = f"""
        You are a restaurant auditor comparing the AI's expected Score against the Customer Receipts.

        Restaurant Name: {restaurant_name}
        AI Score: {original_score}/100
        Customer Receipts: {receipts_text}

        INSTRUCTION:
        Read the reviews and pick EXACTLY ONE of the three JSON templates below.
        
        Copy the template exactly and only replace the text fields.
        You may ONLY replace the text inside:
        "comments"
        "reason"

        All other JSON values must remain identical to the template.
        Do NOT change any boolean values.
        Do NOT invent new combinations.
        
        If your output does not exactly match one of the templates, the result is INVALID.

        =========================================
        TEMPLATE 1: THE PASS (DEFAULT)
        Use this if the reviews are positive, neutral, or contain short generic compliments like "good meat" or "nice interior." Minor nitpicks are fine.
        {{
            "justified": "Yes",
            "comments": "[Your summary here]",
            "reason": "[Explain why it passes]",
            "manual_flag": false,
            "upgrade_recommended": false
        }}

        =========================================
        TEMPLATE 2: THE FAIL / FLAG
        Use this ONLY if there is a severe contradiction (e.g., Score is 90+ but reviews explicitly complain about bad food, rude staff, or scams).
        {{
            "justified": "No",
            "comments": "[Your summary here]",
            "reason": "[Explain the severe contradiction]",
            "manual_flag": true,
            "upgrade_recommended": false
        }}

        =========================================
        TEMPLATE 3: THE UPGRADE
        Use this ONLY if the score is under 85 but every single review is an enthusiastic, flawless, 5-star rave.
        {{
            "justified": "Yes",
            "comments": "[Your summary here]",
            "reason": "[Explain why it deserves a higher score]",
            "manual_flag": false,
            "upgrade_recommended": true
        }}

        OUTPUT: Provide ONLY the chosen JSON template with the text fields filled in.
        """

    # 1. Try Local Ollama First
    try:
        model_name = "qwen2.5:3b"
        print(f"   🧠 Asking Local Qwen ({model_name})...")

        response = requests.post(
            'http://localhost:11434/api/generate',
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False
                # 🚨 REMOVED "format": "json" - Let Qwen speak freely!
            },
            timeout=180
        )
        response.raise_for_status()

        raw_text = response.json().get('response', '')
        print(f"   [DEBUG] Raw Qwen output: {repr(raw_text)}")

        # 1. Strip markdown
        clean_text = raw_text.replace('```json', '').replace('```', '').strip()

        # 2. 🚨 Extract ONLY the JSON dictionary, ignoring the "thinking"
        json_match = re.search(r'(\{.*\})', clean_text, re.DOTALL)

        if json_match:
            clean_text = json_match.group(1)
        else:
            raise ValueError("Qwen rambled too much and never output a valid JSON dictionary!")

        # Parse the text into a Python dictionary
        result = json.loads(clean_text)

        # 3. 🛡️ THE PYTHON SAFETY NET
        # Force the manual_flag to align perfectly with the justified verdict
        if result.get("justified") == "No":
            if not result.get("manual_flag"):
                print("   🛡️ [Python Override] AI failed the restaurant but forgot to flag. Forcing flag to True.")
            result["manual_flag"] = True

        elif result.get("justified") == "Yes":
            if result.get("manual_flag"):
                print("   🛡️ [Python Override] AI passed the restaurant but left the flag on. Forcing flag to False.")
            result["manual_flag"] = False

        # Finally, return the corrected dictionary
        return result

    except Exception as e:
        print(f"   ⚠️ Local Qwen failed ({e}). Gracefully degrading to Gemini...")

        # 2. Fallback to Gemini
        try:
            gemini_response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            # Clean formatting to ensure pure JSON
            clean_text = gemini_response.text.replace('```json', '').replace('```', '').strip()
            return json.loads(clean_text)
        except Exception as gemini_e:
            print(f"   ❌ Both Local AI and Gemini failed: {gemini_e}")
            return None


def setup_driver():
    """Launches an invisible Chrome browser to bypass Naver's walls."""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless') # Keep visible for now!

    # 🚨 THE STEALTH FLAGS 🚨
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Standard options
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument(
        'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def scrape_receipt_reviews(driver, neighborhood, restaurant_name):
    search_query = f"{neighborhood} {restaurant_name}"
    print(f"\n🕵️‍♂️ AUDITOR: Scraping receipts for '{search_query}'...")

    driver.get(f"https://map.naver.com/p/search/{search_query}")
    time.sleep(random.uniform(3.5, 5.0))

    # STEP 1: Click the search result in the left menu
    try:
        print("   -> [Step 1] Switching to search iframe...")
        driver.switch_to.frame("searchIframe")

        # We grab the first chunk of the restaurant name to use as a fallback text search
        # e.g., "아트몬스터 을지로점" -> "아트몬스터"
        name_snippet = restaurant_name.split()[0]

        # The Ultimate Net: Looks for blue links, standard list links, OR anything containing the name
        search_results = driver.find_elements(By.XPATH,
                                              f"//*[contains(@class, 'place_bluelink')] | "
                                              f"//*[contains(@class, 'YwYLL')] | "
                                              f"//li//a | "
                                              f"//a[contains(., '{name_snippet}')]"
                                              )

        clicked = False
        for result in search_results:
            try:
                # Only try to click it if it's actually visible on the screen
                if result.is_displayed():
                    # Scroll it into view just in case it's at the bottom of the list
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", result)
                    time.sleep(0.5)
                    result.click()
                    print(f"   -> [Step 1] Successfully clicked the search result.")
                    clicked = True
                    break
            except Exception:
                continue  # If this specific element isn't clickable, try the next one

        if not clicked:
            raise Exception("No clickable search results found.")

        time.sleep(random.uniform(2.0, 3.5))
    except Exception as e:
        print(f"   ⚠️ [Step 1] Failed to click a list item. Assuming 'Direct Hit'. Proceeding to Step 2...")

    # STEP 2: Wait for the Detail Panel (entryIframe) and click Review
    try:
        print("   -> [Step 2] Switching to entry iframe...")
        driver.switch_to.default_content()

        # Wait specifically for the iframe to exist so we don't crash if it didn't load!
        WebDriverWait(driver, 5).until(EC.frame_to_be_available_and_switch_to_it("entryIframe"))

        print("   -> [Step 2] Hunting for the Review tab...")
        tabs = driver.find_elements(By.XPATH,
                                    "//a[contains(text(), '리뷰') or contains(., '리뷰')] | //span[contains(text(), '리뷰')]/ancestor::a")

        if not tabs:
            print("   ❌ Failed at Step 2: Could not find the Review tab.")
            return []

            # 🚨 THE JAVASCRIPT BYPASS: Forces the click on the DOM level, ignoring overlays
        driver.execute_script("arguments[0].click();", tabs[0])

        print("   -> [Step 2] Clicked the Review tab (via JS bypass)!")
        time.sleep(random.uniform(2.5, 4.0))

    except Exception as e:
        print(f"   ❌ Failed at Step 2: Detail panel never loaded. ({type(e).__name__})")
        return []

    # STEP 3: Scrape the actual text (The CSS Bypass)
    try:
        print("   -> [Step 3] Extracting review text...")

        # Grab all the list items (individual review blocks)
        review_items = driver.find_elements(By.XPATH, "//li")
        receipt_text = []

        # The Naver Boilerplate Blacklist
        ignore_phrases = [
            "더보기", "방문자 리뷰", "블로그 리뷰", "이전", "다음",
            "리뷰에 반응을", "도움이 돼요", "신고", "번째 방문",
            "이런 점이 좋았어요", "예약", "영수증", "사진"
        ]

        import re

        for item in review_items:
            # Get all text inside this review block, split by newlines
            lines = item.text.strip().split('\n')

            for line in lines:
                line = line.strip()

                # Check 1: Is it just a date? (e.g., "26. 3. 4.", "2026년 3월")
                is_date = re.search(r'\d{2,4}[\.\-년]\s?\d{1,2}[\.\-월]', line)

                # Check 2: Is it longer than 10 characters?
                # Check 3: Is it free of our blacklisted junk words?
                if len(line) > 10 and not any(phrase in line for phrase in ignore_phrases) and not is_date:
                    if line not in receipt_text:
                        receipt_text.append(line)

                if len(receipt_text) >= 15:
                    break
            if len(receipt_text) >= 15:
                break

        if not receipt_text:
            print("   ⚠️ Found the tab, but only found UI junk. No real comments.")
            return []

        print(f"   ✅ Extracted {len(receipt_text)} actual customer comments.")
        return receipt_text

    except Exception as e:
        print(f"   ❌ Failed at Step 3: Could not extract review text. ({type(e).__name__})")
        return []

def run_auditor_pipeline():
    if not os.path.exists(CSV_FILENAME):
        print("No CSV found!")
        return

    # 1. Define the actual column names based on your data structure
    csv_headers = [
        'Neighborhood', 'Category', 'Restaurant Name', 'Score',
        'Award Level', 'Justification', 'Description EN',
        'Description KO', 'Kakao URL', 'Latitude', 'Longitude'
    ]

    # 2. Load the CSV. header=None tells Pandas not to use row 1 as headers!
    df = pd.read_csv(
        CSV_FILENAME,
        on_bad_lines='skip',
        engine='python',
        encoding='utf-8-sig'
    )

    # Strip invisible whitespace from all column headers just to be safe
    df.columns = df.columns.str.strip()
    auditor_cols = ['Auditor Comments', 'Rating Justified', 'Auditor Reason', 'Needs Manual Review',
                    'Upgrade Recommended']
    for col in auditor_cols:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = df[col].astype(object)

    # Target the high-scorers (70+)
    high_scorers = df[pd.to_numeric(df['Score'], errors='coerce') >= 70]
    print(f"⚖️ Launching Supreme Court Auditor for {len(high_scorers)} high-scoring restaurants...")

    if high_scorers.empty:
        return

    driver = setup_driver()

    try:
        # Use enumerate to create a true loop counter (starting at 1)
        for count, (idx, row) in enumerate(high_scorers.iterrows(), start=1):
            justified_val = str(row.get('Rating Justified')).strip()
            # 🚨 THE RETHINK LOOPHOLE: Skip if it has a value AND that value is not "No"
            if pd.notna(row.get('Rating Justified')) and justified_val != '' and justified_val != 'No':
                print(f"⏭️ Skipping '{row['Restaurant Name']}' - Already audited ({justified_val}).")
                continue

            neighborhood = row['Neighborhood']
            name = row['Restaurant Name']
            score = row['Score']

            receipts = scrape_receipt_reviews(driver, neighborhood, name)

            if receipts:
                receipts_joined = " | ".join(receipts)
                verdict = analyze_receipts_with_fallback(name, score, receipts_joined)

                if verdict:
                    df.at[idx, 'Auditor Comments'] = verdict.get('comments', '')
                    df.at[idx, 'Rating Justified'] = verdict.get('justified', '')
                    df.at[idx, 'Auditor Reason'] = verdict.get('reason', '')
                    df.at[idx, 'Needs Manual Review'] = verdict.get('manual_flag', False)
                    df.at[idx, 'Upgrade Recommended'] = verdict.get('upgrade_recommended', False)

                    print(
                        f"   📝 Verdict: {verdict.get('justified')} | Flagged: {verdict.get('manual_flag')} | Upgrade: {verdict.get('upgrade_recommended')}")
                    df.to_csv(CSV_FILENAME, index=False, encoding='utf-8-sig')

            else:
                # 🚨 THE NEW TRAP: If it only found UI junk (or nothing at all), flag it immediately!
                print("   ⚠️ No valid text extracted. Bypassing AI and flagging for manual review.")

                df.at[idx, 'Auditor Comments'] = "No valid reviews found."
                df.at[idx, 'Rating Justified'] = "Unknown"
                df.at[
                    idx, 'Auditor Reason'] = "Scraper reached the review page, but only found dates, UI buttons, or boilerplate text. Needs human verification."
                df.at[idx, 'Needs Manual Review'] = True
                df.at[idx, 'Upgrade Recommended'] = False

                df.to_csv(CSV_FILENAME, index=False, encoding='utf-8-sig')

            # Sleeps for a random amount of time between 3.5 and 6.2 seconds
            time.sleep(random.uniform(3.5, 6.2))

            # The Coffee Break: Use the 'count' variable instead of 'idx'
            if count % 10 == 0:
                pause_time = random.uniform(60, 120)
                print(
                    f"☕ Stealth Mode: Completed {count} searches. Taking a {int(pause_time)} second coffee break to fool Naver...")
                time.sleep(pause_time)

    finally:
        driver.quit()
        print("\n🏁 Audit Complete!")


if __name__ == "__main__":
    run_auditor_pipeline()