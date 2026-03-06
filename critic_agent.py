import os
import json
import requests
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv
from typing import Any

script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("🚨 ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)

# Define your local model here so it's easy to change later
LOCAL_MODEL = "qwen2.5:3b"


def get_kakao_categories(keyword, strict_mode=False):
    """
    Acts as a Pre-Flight Coordinator.
    Tries Local Qwen first, falls back to Gemini.
    """
    if strict_mode:
        print(f"🔒 STRICT MODE ON: Bypassing AI. Locking target strictly to '{keyword}'.")
        return [keyword]

    print(f"🧠 Coordinator: Translating '{keyword}' into Kakao categories...")

    # 🚨 THE FIX: Explicitly ask for a JSON Object (Dictionary) to satisfy Ollama's format requirement
    instruction = f"""
    You understand common category labels used in Kakao Map restaurant listings.

    The user will provide a food or restaurant keyword.

    Your task:
    Return a MAXIMUM of 3 specific Kakao Map-style category labels 
    that restaurants serving this keyword would most likely be registered under.

    Rules:
    - All output must be in Korean (Hangul only).
    - NEVER return the broad top-level category "음식점".
    - Be specific (e.g., "전,부침개", "요리주점", "감자탕").
    - Do NOT return menu items unless they are commonly used as a Kakao listing category.
    - If the keyword is broad, return the most likely establishment types.

    OUTPUT FORMAT:
    Return ONLY a valid JSON object with a single key "categories" containing an array of strings.

    EXAMPLE:
    {{
        "categories": ["요리주점", "맥주,호프", "펍"]
    }}

    Keyword: {keyword}
    """

    try:
        print(f"   [Coordinator] Asking Local {LOCAL_MODEL}...")
        response = requests.post(
            'http://localhost:11434/api/generate',
            json={"model": LOCAL_MODEL, "prompt": instruction, "stream": False, "format": "json"},
            timeout=30
        )
        response.raise_for_status()

        # 🚨 THE FIX: Extract the list using the dictionary key
        result_dict = json.loads(response.json()['response'])
        categories = result_dict.get("categories", [])

        if isinstance(categories, list) and len(categories) > 0:
            categories = categories[:3]
            print(f"   ✅ Categories locked in by Local AI: {categories}")
            return categories
        else:
            raise ValueError("Local AI returned JSON, but the 'categories' list was missing or empty.")

    except Exception as e:
        print(f"   ⚠️ Local AI failed ({e}). Gracefully degrading to Gemini...")
        try:
            gemini_response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=f"Keyword: {keyword}",
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    response_mime_type="application/json",
                    temperature=0.1
                )
            )

            result_dict = json.loads(gemini_response.text)
            categories = result_dict.get("categories", [])[:3]

            if not categories:
                categories = [keyword]  # Ultimate fallback

            print(f"   ✅ Categories locked in by Gemini: {categories}")
            return categories
        except Exception as gemini_e:
            print(f"   ❌ Both AI systems failed: {gemini_e}. Defaulting to keyword only.")
            return [keyword]

def get_image_bytes(image_url):
    """Fetches the raw bytes of an image to feed to the AIs."""
    if not image_url:
        return None
    try:
        headers = {"Referer": "https://blog.naver.com"}
        response = requests.get(image_url, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"⚠️ Failed to fetch image for lie detector: {e}")
    return None


def evaluate_restaurant(restaurant_name, scraped_blog_data, search_keyword):
    print(f"\n🧠 Junior Analyst: Verifying '{search_keyword}' and extracting Michelin criteria...")

    # ==========================================
    # 🚨 THE TRUNCATION SAFETY NET
    # Prevent Base64 HTML bloat from blowing up the 1M token window
    # ==========================================
    safe_texts = []
    for item in scraped_blog_data:
        raw_text = item.get("text", "")
        # Cap every single blog at exactly 10,000 characters
        safe_texts.append(raw_text[:10000])

    combined_text = "\n\n--- NEXT REVIEW ---\n\n".join(safe_texts)
    # ==========================================

    image_bytes = None
    for item in scraped_blog_data:
        for img_url in item.get("bottom_images", []):
            image_bytes = get_image_bytes(img_url)
            if image_bytes:
                break
        if image_bytes:
            break

    # ==========================================
    # PHASE 1: The Junior Analyst (Fact Extractor)
    # ==========================================
    analyst_instruction = f"""
    <role>
    You are a rigorous, skeptical data analyst reviewing Korean blog posts.
    Your mission is NOT to summarize praise. Your mission is to extract verifiable signals of quality.
    </role>

    <target>
    The target food/vibe is: {search_keyword}.
    </target>

    <task_1_authenticity_filter>
    Determine whether the restaurant genuinely specializes in {search_keyword} and executes it at a high level. You are hunting for ELEVATED executions of common comfort foods.

    General Rules:
    - Reject generic diners.
    - Reject places praised only for "cheap and large portions" (가성비).
    - Reject convenience-store-tier quality.
    - The target must be a core focus, not a side menu item.

    Dish-Specific Evaluation Rules:
    You MUST evaluate the target food using criteria appropriate to that dish type.
    - Soups/stews → broth depth, long boiling time, absence of off-odors (잡내)
    - Grilled/stir-fried meats → flame control (불맛), caramelization, ingredient sourcing (한돈/한우)
    - Fried foods → oil freshness, crisp texture retention
    - Raw dishes → freshness, trimming precision, temperature control
    - Craft beer → brewing quality, tap freshness, beer-focused identity

    Focus ONLY on signals that indicate technical mastery of the specific dish.
    </task_1_authenticity_filter>

    <task_2_sponsorship_detector>
    Identify sponsored content signals to calculate the true ratio of paid vs. organic reviews.

    - Text Check: Scan for mandatory disclosure phrases ('소정의 원고료', '제품을 제공받아', '협찬', '지원받아').
    - Image Check (if image is attached): If the bottom-of-post image contains '협찬' or '제공받아', count it as sponsored.
    - Output Format: Return as a string (e.g., "X/Y sponsored").
    </task_2_sponsorship_detector>

    <task_3_fact_extraction>
    Extract objective facts strictly under these 5 criteria:
    1. Quality of ingredients (식재료의 품질)
    2. Mastery of technique (맛과 조리 기술)
    3. Personality of the chef (사장의 개성)
    4. Value for money (가성비)
    5. Consistency (일관성)

    CRITICAL FILTERING RULE:
    If the sponsored ratio exceeds 50%, aggressively remove hyperbolic adjectives ("환상적인", "최고의", "인생맛집"), emotional language, and marketing tone. Extract ONLY cold, verifiable statements. If something cannot be objectively supported, exclude it.
    </task_3_fact_extraction>

    <output_format>
Respond ONLY with a valid JSON object. Do not include markdown code blocks. 
Use the exact structure below:

{{
    "validation_checklist": {{
        "is_primary_menu_item": (boolean),
        "has_sponsorship_disclosure": (boolean),
        "is_generic_franchise_or_diner": (boolean)
    }},
    "serves_target_food": (boolean, must be false if is_generic_franchise_or_diner is true),
    "sponsored_ratio": "string (e.g., '4/10 sponsored')",
    "extracted_facts_ko": "Korean summary organized by the 5 criteria. The FIRST line must explicitly state the sponsored ratio."
}}
    </output_format>
    """

    full_analyst_prompt = f"{analyst_instruction}\n\nAnalyze these reviews for {restaurant_name}:\n\n{combined_text}"
    analyst_data = None

    analyst_data = None
    max_retries = 3

    for attempt in range(max_retries):
        try:
            print(f"   [Junior Analyst] Asking Cloud Gemini (Attempt {attempt + 1}/{max_retries})...")
            payload_contents: list[Any] = [f"Analyze these reviews for {restaurant_name}:\n\n{combined_text}"]
            if image_bytes:
                payload_contents.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

            gemini_response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=payload_contents,
                config=types.GenerateContentConfig(
                    system_instruction=analyst_instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            analyst_data = json.loads(gemini_response.text)
            print("   ✅ Gemini successfully extracted facts!")
            break  # Success! Break out of the retry loop

        except Exception as e:
            error_str = str(e)
            if "503" in error_str and attempt < max_retries - 1:
                print(f"   ⏳ Server overloaded (503). Waiting 3 seconds before retry...")
                time.sleep(3)
            else:
                print(f"   ⚠️ Cloud AI failed permanently ({e}). Falling back to Local {LOCAL_MODEL}...")

                # --- LOCAL FALLBACK BLOCK GOES HERE ---
                try:
                    payload = {"model": LOCAL_MODEL, "prompt": full_analyst_prompt, "stream": False, "format": "json"}
                    response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=90)
                    response.raise_for_status()
                    analyst_data = json.loads(response.json()['response'])
                    print("   ✅ Local AI successfully extracted facts!")
                except Exception as local_e:
                    print(f"   ❌ Both AI systems failed Junior Analyst stage: {local_e}")
                    return None
                break  # Break out of the loop after local attempt

    # ==========================================
    # 🛡️ THE BULLETPROOF DATA UNWRAPPER
    # Guaranteed to yield a dictionary no matter how the AI wraps it
    # ==========================================
    if isinstance(analyst_data, str):
        try:
            analyst_data = json.loads(analyst_data)
        except:
            pass

    # Unwrap lists (and nested lists!)
    while isinstance(analyst_data, list):
        if len(analyst_data) > 0:
            analyst_data = analyst_data[0]
        else:
            analyst_data = {}

    # Final Failsafe
    if not isinstance(analyst_data, dict):
        analyst_data = {}
    # ==========================================

    if not analyst_data.get("serves_target_food", False):
        print(f"   🛑 REJECTED: Does not focus on {search_keyword}.")
        return {"score": 0, "award_level": "None", "justification": f"Does not specialize in {search_keyword}."}

    extracted_facts = analyst_data.get("extracted_facts_ko", "")
    print(
        f"   ✅ Facts extracted. Sponsorship: {analyst_data.get('sponsored_ratio', 'Unknown')}. Handing to Head Critic.")

    # ==========================================
    # PHASE 2: The Head Critic (The Michelin Judge)
    # ==========================================
    print(f"👑 Head Critic: Scoring rigorously...")

    critic_instruction = f"""
        <role>
        You are the Head Critic for the 'Neon Guide', evaluating restaurants for: {search_keyword}.
        You apply the rigorous standards of fine dining to everyday comfort food. Be exceptionally strict and mathematically precise.
        </role>

        <input_data>
        The Junior Analyst has provided a summary of objective facts, including a "Sponsored Ratio" (협찬 비율).
        </input_data>

        <scoring_rules>
        Score the restaurant out of 100. You MUST build the final score by assigning up to 20 points in each of these 5 categories:
        1. ingredients (Quality and sourcing of raw materials)
        2. technique (Mastery of flavor, cooking execution, temperature control)
        3. personality (Uniqueness of the chef, signature identity vs. generic)
        4. value (Price relative to quality/portion)
        5. consistency (Evidence of long-term reputation and repeat local customers)
        </scoring_rules>

        <sponsorship_weighting_rule>
        Read the Sponsored Ratio carefully. 
        - If the sponsored ratio is > 50%: You MUST set "sponsorship_penalty_applied" to true. You must heavily deduct points from the "consistency" and "value" categories. The final calculated total score MUST NOT exceed 89. No exceptions.
        - If the sponsored ratio is <= 50%: Set "sponsorship_penalty_applied" to false. Score normally based purely on the culinary facts.
        </sponsorship_weighting_rule>

        <award_levels>
        - 95-100: "3 Neon Hearts" (Flawless execution, destination-worthy)
        - 88-94: "2 Neon Hearts" (Exceptional neighborhood staple)
        - 80-87: "1 Neon Heart" (Great, but has minor flaws in 1 or 2 criteria)
        - <80: "None" (Average, tourist trap, or lacks consistency)
        </award_levels>

        <output_format>
        Respond ONLY with a valid JSON object. Do not include markdown code blocks.
        Use the exact structure below:

        {{
            "score_breakdown": {{
                "ingredients": (int 0-20),
                "technique": (int 0-20),
                "personality": (int 0-20),
                "value": (int 0-20),
                "consistency": (int 0-20),
                "sponsorship_penalty_applied": (boolean)
            }},
            "score": (integer, MUST equal the exact sum of the 5 categories above),
            "award_level": "string (e.g., '2 Neon Hearts' or 'None')",
            "description_en": "A punchy, honest 2-sentence English description reflecting the criteria.",
            "description_ko": "A natural, 2-sentence Korean description.",
            "justification": "1 sentence explaining the score breakdown. If a sponsorship penalty was applied, explicitly state that here."
        }}
        </output_format>
    """

    full_critic_prompt = f"{critic_instruction}\n\nCritique this summary for {restaurant_name}:\n\n{extracted_facts}"

    try:
        print(f"   [Head Critic] Asking Cloud Gemini first...")
        gemini_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"Critique this summary for {restaurant_name}:\n\n{extracted_facts}",
            config=types.GenerateContentConfig(
                system_instruction=critic_instruction,
                response_mime_type="application/json",
                temperature=0.4
            )
        )
        critic_data = json.loads(gemini_response.text)
        print("   ✅ Gemini successfully scored the restaurant!")
        return critic_data

    except Exception as gemini_e:
        print(f"   ⚠️ Gemini failed ({gemini_e}). Gracefully degrading to Local {LOCAL_MODEL}...")
        try:
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={"model": LOCAL_MODEL, "prompt": full_critic_prompt, "stream": False, "format": "json"},
                timeout=45
            )
            response.raise_for_status()
            critic_data = json.loads(response.json()['response'])
            print("   ✅ Local AI successfully scored the restaurant!")
            return critic_data

        except Exception as local_e:
            print(f"   ❌ Both AI systems failed Head Critic stage: {local_e}")
            return None