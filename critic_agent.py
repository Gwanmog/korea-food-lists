import os
import json
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

def evaluate_restaurant(restaurant_name, scraped_blog_texts, search_keyword):
    print(f"\n🧠 Junior Analyst: Verifying '{search_keyword}' and extracting Michelin criteria...")

    combined_text = "\n\n--- NEXT REVIEW ---\n\n".join(scraped_blog_texts)

    # ==========================================
    # PHASE 1: The Junior Analyst (Fact Extractor)
    # ==========================================
    analyst_instruction = f"""
        You are a meticulous data analyst reviewing Korean blog posts. 
        The target vibe/food is: {search_keyword}.

        TASK 1: Verify if the restaurant genuinely focuses on {search_keyword}. 
        - If the target is '수제맥주' (Craft Beer), act as a strict beer critic. Check the tap list mentioned in the blogs.
        - If they primarily serve mass-market domestic beer (카스/Cass, 테라/Terra, 켈리/Kelly, 생맥주) and only have 1 or 2 generic craft beers, REJECT THEM. 
        - They must have a dedicated craft beer lineup, brew their own beer (양조장), or have a highly curated guest tap list.
        - If they fail this standard, flag 'serves_target_food' as false.

        TASK 2: Extract objective facts based strictly on these 5 criteria:
        1. Quality of ingredients (식재료의 품질 - e.g., fresh meat, clean oil, freshness of the beer kegs).
        2. Mastery of technique (맛과 조리 기술 - e.g., brewing techniques, food pairings, batter crispiness).
        3. Personality of the chef/brewer (사장의 개성 - e.g., unique recipes, experimental brews vs. generic).
        4. Value for money (가성비 - price vs. quality/portion).
        5. Consistency (일관성 - e.g., mentions of being a long-time favorite, returning customers).

        Output strictly in JSON format:
        {{
            "serves_target_food": (boolean),
            "extracted_facts_ko": (A detailed summary of the facts categorized by the 5 criteria in Korean)
        }}
        """

    analyst_config = types.GenerateContentConfig(
        system_instruction=analyst_instruction,
        response_mime_type="application/json",
        temperature=0.2
    )

    analyst_prompt = f"Analyze these reviews for {restaurant_name}:\n\n{combined_text}"

    try:
        analyst_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=analyst_prompt,
            config=analyst_config
        )
        analyst_data = json.loads(analyst_response.text)

        if not analyst_data.get("serves_target_food", False):
            print(f"🛑 REJECTED: Does not focus on {search_keyword}.")
            return {"score": 0, "award_level": "None", "justification": f"Does not specialize in {search_keyword}."}

        extracted_facts = analyst_data.get("extracted_facts_ko", "")
        print("✅ Facts extracted based on the 5 criteria. Handing to Head Critic.")

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

    Score the restaurant out of 100, awarding up to 20 points for each of the following:
    1. Quality of the ingredients (20 pts)
    2. Mastery of flavor and cooking techniques (20 pts)
    3. Personality of the chef / Uniqueness (20 pts)
    4. Value for money (20 pts)
    5. Consistency over time (20 pts)

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
        "justification": (1 sentence explaining the score breakdown, specifically noting where points were lost)
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
        return json.loads(critic_response.text)

    except Exception as e:
        print(f"❌ Head Critic Error: {e}")
        return None