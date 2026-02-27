import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# Load your API key from .env
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

# Extract the key explicitly
API_KEY = os.getenv("GEMINI_API_KEY")

# Force-feed the key to the new client
if not API_KEY:
    raise ValueError("🚨 ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)

def evaluate_restaurant(restaurant_name, scraped_blog_texts):
    """
    A two-step Agentic pipeline using the new google-genai SDK.
    """
    print(f"\n🧠 Junior Analyst (Flash-Lite): Reading raw blogs for '{restaurant_name}'...")

    combined_text = "\n\n--- NEXT REVIEW ---\n\n".join(scraped_blog_texts)

    # ==========================================
    # PHASE 1: The Junior Analyst (Data Extraction)
    # ==========================================
    analyst_instruction = """
    You are a data analyst. Read the provided Korean blog reviews for the restaurant.
    Extract the objective facts regarding:
    1. Food quality (crispiness, meat quality, sauce).
    2. Atmosphere and vibe (tourist trap vs. local legend).
    3. How well the food pairs with alcohol (안주 synergy).

    Output strictly in JSON format with a single key "extracted_facts_ko" containing a comprehensive summary in Korean.
    """

    # The new SDK uses a config object for instructions and JSON formatting
    analyst_config = types.GenerateContentConfig(
        system_instruction=analyst_instruction,
        response_mime_type="application/json"
    )

    analyst_prompt = f"Analyze these reviews for {restaurant_name}:\n\n{combined_text}"

    try:
        analyst_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=analyst_prompt,
            config=analyst_config
        )
        analyst_data = json.loads(analyst_response.text)
        extracted_facts = analyst_data.get("extracted_facts_ko", "")
        print("✅ Junior Analyst has successfully summarized the data.")

    except Exception as e:
        print(f"❌ Junior Analyst Error: {e}")
        return None

    # ==========================================
    # PHASE 2: The Head Critic (Scoring & Writing)
    # ==========================================
    print(f"👑 Head Critic (Flash): Evaluating the summary and writing the final review...")

    critic_instruction = """
    You are the Head Critic for the 'Neon Guide', an elite restaurant guide dedicated exclusively to South Korean comfort food, 치킨, and drinking culture at 포차 or 술집.

    Based on the provided Korean summary from your analyst, score the restaurant out of 100 based on this rubric:
    1. Food Quality & Technique (40 points)
    2. Local Vibe & Authenticity (30 points)
    3. 안주 Synergy (30 points)

    You MUST return ONLY a valid JSON object. Do not include markdown formatting like ```json.
    Structure:
    {
        "score": (integer 0-100),
        "award_level": (string: "3 Neon Crosses" for 90+, "2 Neon Crosses" for 80-89, "1 Neon Cross" for 70-79, "None" for <70),
        "description_en": (A punchy, 2-sentence English description tailored for a tourist map),
        "description_ko": (A natural, 2-sentence Korean description for the map),
        "justification": (1 sentence explaining why it earned this score)
    }
    """

    critic_config = types.GenerateContentConfig(
        system_instruction=critic_instruction,
        response_mime_type="application/json"
    )

    critic_prompt = f"Evaluate this restaurant summary for {restaurant_name}:\n\n{extracted_facts}"

    try:
        critic_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=critic_prompt,
            config=critic_config
        )
        final_result = json.loads(critic_response.text)
        return final_result

    except Exception as e:
        print(f"❌ Head Critic Error: {e}")
        return None


# --- TEST THE PIPELINE ---
if __name__ == "__main__":
    test_restaurant = "교촌치킨 강남역점"
    fake_blog_data = [
        "이곳은 정말 최고입니다! 튀김옷이 3시간이 지나도 바삭바삭해요. 마늘 간장 소스는 맥주랑 완벽하게 어울립니다. 분위기는 조금 시끄럽지만 진짜 퇴근 후 직장인들의 성지 같은 느낌이에요.",
        "맛은 있는데 웨이팅이 너무 깁니다. 외국인 관광객도 많아져서 예전 같은 로컬 분위기는 아니네요. 그래도 치킨 퀄리티는 인정합니다."
    ]

    evaluation = evaluate_restaurant(test_restaurant, fake_blog_data)

    if evaluation:
        print("\n🏆 THE NEON GUIDE VERDICT 🏆")
        print(f"Score: {evaluation['score']}/100")
        print(f"Award: {evaluation['award_level']}")
        print(f"EN Desc: {evaluation['description_en']}")
        print(f"KO Desc: {evaluation['description_ko']}")
        print(f"AI Notes: {evaluation['justification']}\n")