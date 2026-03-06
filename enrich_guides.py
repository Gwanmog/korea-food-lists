import pandas as pd
import json
import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Import your existing scraper tools!
from naver_agent import search_naver_blogs, scrape_naver_blog_text

# Setup paths and API
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# The Ghostwriter Prompt (Strictly enforcing no romanization!)
ghostwriter_instruction = """
You are a professional food writer for a Seoul travel map. 
Your job is to read user reviews and summarize the vibe, signature dishes, and overall experience of a restaurant in two short, punchy paragraphs.

OUTPUT RULES:
1. Return a JSON object with exactly two keys: "description_en" and "description_ko".
2. In the English description, when mentioning Korean food names or concepts, provide the standard English romanization followed by the native Hangul in parentheses. 
   - Example: Write "The signature samgyeopsal (삼겹살) is incredible."
3. Do not rate or score the restaurant. You are just writing an enticing summary.
"""


def enrich_blue_ribbon():
    # Target the exact raw file you specified
    input_csv = os.path.join(script_dir, 'data', 'raw', 'blueribbon.csv')
    output_csv = os.path.join(script_dir, 'data', 'raw', 'blueribbon_enriched.csv')

    # 🚨 Check if we already have a work-in-progress file!
    if os.path.exists(output_csv):
        print(f"📂 Found existing progress! Loading from: {output_csv}")
        df = pd.read_csv(output_csv)
    else:
        print(f"📂 Starting fresh! Loading raw data from: {input_csv}")
        df = pd.read_csv(input_csv)

    # Create empty columns if they don't exist
    if 'description_en' not in df.columns:
        df['description_en'] = ''
        df['description_ko'] = ''

    for idx, row in df.iterrows():
        # Skip if we already wrote a description for this row
        if pd.notna(row.get('description_en')) and len(str(row.get('description_en'))) > 10:
            continue

        # Adjust 'name' to whatever the exact column header is in your blueribbon.csv
        restaurant_name = row.get('name', row.get('Restaurant Name', 'Unknown'))
        print(f"\n✍️ Ghostwriting description for {restaurant_name}...")

        # 1. Scrape Naver for Context
        blog_results = search_naver_blogs(restaurant_name)
        raw_text = ""

        if blog_results:
            # Grab the text from the top blog post
            top_blog_url = blog_results[0]['link']
            scraped_data = scrape_naver_blog_text(top_blog_url)
            if scraped_data:
                raw_text = scraped_data['text'][:3000]  # Limit to 3000 chars to save AI tokens

        if not raw_text:
            print(f"   ⚠️ Could not find enough Naver data for {restaurant_name}. Skipping.")
            continue

        # 2. Ask Gemini to write the summaries
        prompt = f"Write the descriptions for {restaurant_name} based on these reviews:\n{raw_text}"

        # 1. THE RETRY LOOP
        max_retries = 4
        success = False

        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=ghostwriter_instruction,  # (Or whatever instruction you are using)
                        response_mime_type="application/json",
                        temperature=0.3
                    )
                )
                data = json.loads(response.text)

                # Assign the data
                df.at[idx, 'description_en'] = data.get('description_en', '')
                df.at[idx, 'description_ko'] = data.get('description_ko', '')
                print(f"   ✅ Success!")

                success = True
                break  # Break out of the retry loop if successful

            except Exception as e:
                error_str = str(e)
                if "503" in error_str and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5  # Wait 5s, 10s, 15s...
                    print(
                        f"   ⏳ 503 Server overloaded. Waiting {wait_time}s before retry (Attempt {attempt + 1}/{max_retries})...")
                    time.sleep(wait_time)
                else:
                    print(f"   ❌ Failed after {attempt + 1} attempts: {e}")
                    break  # Give up and move to the next restaurant

        # 2. THE INCREMENTAL SAVE
        # By indenting this so it sits INSIDE the for-loop,
        # it updates the CSV on your hard drive after every single restaurant.
        # If you stop the script, your progress is safely stored!
        if success:
            df.to_csv(output_csv, index=False)
            time.sleep(2)  # Normal rate-limit pause
    print(f"\n🎉 Blue Ribbon data enriched and saved to: {output_csv}")
    print("Next step: Run build_map_list.py to update the GeoJSON!")


if __name__ == "__main__":
    enrich_blue_ribbon()