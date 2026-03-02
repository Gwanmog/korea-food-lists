import os
import json
import requests
from google import genai
from google.genai import types
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise ValueError("🚨 ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)

def get_kakao_categories(keyword, strict_mode=False):
    """
    Acts as a Pre-Flight Coordinator.
    If strict_mode is True, bypasses the AI and forces an exact keyword match.
    """
    if strict_mode:
        print(f"🔒 STRICT MODE ON: Bypassing AI. Locking target strictly to '{keyword}'.")
        return [keyword]

    print(f"🧠 Coordinator: Translating '{keyword}' into Kakao categories...")

    instruction = """
        You are an expert in South Korean food culture and the Kakao Map API database structure.
        The user is going to provide a food or restaurant keyword.

        Your job is to provide a MAXIMUM of 3 official Kakao Map category tags or highly relevant terms 
        that a restaurant serving this food would be registered under.

        Rules:
        - Keep all categories strictly in Korean (Hangul). Do not romanize anything.
        - NEVER return the top-level broad category "음식점". You must be specific.
        - Return ONLY a valid JSON array of strings. No markdown, no explanations.

        Example for '빈대떡':
        ["전,부침개", "막걸리", "한식"]

        Example for '술집':
        ["요리주점", "호프", "포장마차", "이자카야", "맥주", "전통주"]
        """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=f"Keyword: {keyword}",
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        categories = json.loads(response.text)
        # Final safety net just in case the LLM ignores the prompt
        categories = categories[:3]
        print(f"✅ Categories locked in: {categories}")
        return categories
    except Exception as e:
        print(f"⚠️ Coordinator Error: {e}. Defaulting to keyword only.")
        return [keyword]

def get_image_bytes(image_url):
    """Fetches the raw bytes of an image to feed to Gemini."""
    if not image_url:
        return None
    try:
        # Naver requires a Referer header to download images successfully
        headers = {"Referer": "https://blog.naver.com"}
        response = requests.get(image_url, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        print(f"⚠️ Failed to fetch image for lie detector: {e}")
    return None

def evaluate_restaurant(restaurant_name, scraped_blog_data, search_keyword):
    # NOTE: scraped_blog_data is now a list of dictionaries: [{"text": "...", "bottom_images": [...]}]

    print(f"\n🧠 Junior Analyst: Verifying '{search_keyword}' and extracting Michelin criteria...")

    # 1. Combine all the text from the dictionaries
    combined_text = "\n\n--- NEXT REVIEW ---\n\n".join([item["text"] for item in scraped_blog_data])

    # 2. Grab ONE test image from the bottom of the blogs to test for sponsorship
    image_bytes = None
    for item in scraped_blog_data:
        for img_url in item.get("bottom_images", []):
            image_bytes = get_image_bytes(img_url)
            if image_bytes:
                break  # We just need one working image to catch a corporate banner
        if image_bytes:
            break

    # ==========================================
    # PHASE 1: The Junior Analyst (Fact Extractor)
    # ==========================================
    analyst_instruction = f"""
        You are a meticulous data analyst reviewing Korean blog posts. 
        The target food/vibe is: {search_keyword}.

        TASK 1: Verify if the restaurant genuinely focuses on {search_keyword}. You are hunting for ELEVATED executions of common comfort foods. 
        - If the target is '제육볶음', do not accept generic diners; look for mentions of high-quality domestic pork or beef (한돈 / 한우) and authentic smoky fire flavor (불맛). 
        - If the target is '국밥' or '순대국', verify the broth is boiled in-house for hours and lacks any gamey smell (잡내). 
        - If the target is '곱창' or '육회', prioritize extreme freshness and expert preparation. 
        - Reject places where the only praise is 'cheap and large portions' (가성비).
        - If the target is '치킨', prioritize reviews that mention the crispiness of the batter and the freshness of the oil. 
        - If the target is '국밥' or '감자탕', look for mentions of deep, rich broth boiled in-house. 
        - If the target is a market snack like '떡볶이' or '빈대떡', verify the stall has high turnover and fresh ingredients. 
        - Reject generic convenience store quality.

        TASK 2: THE IMAGE & TEXT LIE DETECTOR (협찬 필터)
        - Text Check: Scan the text for mandatory disclosure phrases ('소정의 원고료', '제품을 제공받아', '협찬', '지원받아').
        - Image Check: I have attached an image found at the very bottom of the blog post. Read the Korean text inside this image. If it says '협찬' (Sponsored) or '제공받아' (Provided), this is a sponsored post.
        - Calculate the total ratio of sponsored posts vs. organic posts (e.g., "7/10 sponsored").

        TASK 3: Extract objective facts based strictly on these 5 criteria:
        1. Quality of ingredients (식재료의 품질 - e.g., fresh meat, clean oil).
        2. Mastery of technique (맛과 조리 기술 - e.g., batter crispiness, sauce balance).
        3. Personality of the chef (사장의 개성 - e.g., unique recipes, signature style vs. generic).
        4. Value for money (가성비 - price vs. quality/portion).
        5. Consistency (일관성 - e.g., mentions of being a long-time favorite, returning customers).

        CRITICAL INSTRUCTION FOR TASK 3: If the sponsored ratio is high (e.g., over 50%), you MUST aggressively filter out hyperbolic marketing adjectives ("환상적인", "최고의"). Extract ONLY verifiable, cold facts (e.g., "They age the dough for 24 hours," "The tap list features 8 local IPAs").

        Output strictly in JSON format:
        {{
            "serves_target_food": (boolean),
            "sponsored_ratio": (string, e.g., "4/10 sponsored"),
            "extracted_facts_ko": (A detailed summary of the facts categorized by the 5 criteria in Korean. Explicitly mention the sponsored ratio at the very beginning of this summary.)
        }}
        """

    analyst_config = types.GenerateContentConfig(
        system_instruction=analyst_instruction,
        response_mime_type="application/json",
        temperature=0.2
    )

    # 3. Build the Multimodal Payload
    payload_contents: list = [f"Analyze these reviews for {restaurant_name}:\n\n{combined_text}"]

    # If we found a bottom image, attach it for the Lie Detector!
    if image_bytes:
        print("📸 Image banner detected. Running multimodal Lie Detector...")
        payload_contents.append(
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        )

    try:
        analyst_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=payload_contents,
            config=analyst_config
        )

        # 1. Load the JSON data
        analyst_data = json.loads(analyst_response.text)

        # 🛡️ 2. THE BULLETPROOF JSON PARSER
        # If Gemini accidentally wraps the dictionary in a list, extract the dictionary!
        if isinstance(analyst_data, list):
            analyst_data = analyst_data[0] if len(analyst_data) > 0 else {}

        # 3. Proceed as normal!
        if not analyst_data.get("serves_target_food", False):
            print(f"🛑 REJECTED: Does not focus on {search_keyword}.")
            return {"score": 0, "award_level": "None", "justification": f"Does not specialize in {search_keyword}."}

        extracted_facts = analyst_data.get("extracted_facts_ko", "")
        print(
            f"✅ Facts extracted. Sponsorship Ratio flagged as: {analyst_data.get('sponsored_ratio', 'Unknown')}. Handing to Head Critic.")
    except Exception as e:
        print(f"❌ Junior Analyst Error: {e}")
        return None

    # ==========================================
    # PHASE 2: The Head Critic (The Michelin Judge)
    # ==========================================
    print(f"👑 Head Critic: Scoring rigorously...")

    critic_instruction = f"""
        You are the Head Critic for the 'Neon Guide', evaluating restaurants for: {search_keyword}.
        You apply the rigorous standards of fine dining to everyday food.

        The Junior Analyst has provided a summary of facts, including a "Sponsored Ratio" (협찬 비율). 

        SCORING RULES:
        Score the restaurant out of 100, awarding up to 20 points for each of the following:
        1. Quality of the ingredients (20 pts)
        2. Mastery of flavor and cooking techniques (20 pts)
        3. Personality of the chef / Uniqueness (20 pts)
        4. Value for money (20 pts)
        5. Consistency over time (20 pts)

        THE SPONSORSHIP WEIGHTING RULE:
        - If the sponsored ratio is low (mostly organic reviews): Score normally. Praise genuine consistency.
        - If the sponsored ratio is high (mostly paid reviews): You must act with extreme culinary skepticism. 
          * Deduct heavily from "Consistency" (paid reviews do not prove long-term consistency).
          * Deduct from "Value for money" (reviewers who ate for free cannot accurately judge value).
          * Cap the maximum possible score at 89 unless there is undeniable, verifiable proof of world-class culinary technique. 

        Award Levels:
        - 95+: "3 Neon Hearts" (Flawless execution, destination-worthy)
        - 88-94: "2 Neon Hearts" (Exceptional neighborhood staple)
        - 80-87: "1 Neon Heart" (Great, but has minor flaws in 1 or 2 criteria)
        - <80: "None" (Average, tourist trap, or lacks consistency)

        Return ONLY a valid JSON object:
        {{
            "score": (integer 0-100),
            "award_level": (string),
            "description_en": (A punchy, honest 2-sentence English description reflecting the criteria),
            "description_ko": (A natural, 2-sentence Korean description),
            "justification": (1 sentence explaining the score breakdown. If the score was penalized due to a high sponsored ratio, explicitly state that here.)
        }}
        """

    critic_config = types.GenerateContentConfig(
        system_instruction=critic_instruction,
        response_mime_type="application/json",
        temperature=0.4
    )

    critic_prompt = f"Critique this summary for {restaurant_name}:\n\n{extracted_facts}"

    try:
        critic_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=critic_prompt,
            config=critic_config
        )

        # 1. Parse the JSON into a variable first
        critic_data = json.loads(critic_response.text)

        # 2. THE BULLETPROOF CHECK: If Gemini wrapped it in a list, unwrap it!
        if isinstance(critic_data, list):
            critic_data = critic_data[0] if len(critic_data) > 0 else {}

        # 3. Now return the safe dictionary
        return critic_data

    except Exception as e:
        print(f"❌ Head Critic Error: {e}")
        return None