import os
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

# 1. Pathing setup
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, 'soul-food-api', '.env')
load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("🚨 ERROR: Could not find GEMINI_API_KEY. Check your .env file!")

client = genai.Client(api_key=API_KEY)


# NOTICE: We added search_keyword here!
def evaluate_restaurant(restaurant_name, scraped_blog_texts, search_keyword):
    """
    A rigorous, two-step pipeline.
    Junior Analyst verifies the menu and extracts facts.
    Head Critic scores harshly based on the Neon Heart scale.
    """
    print(f"\n🧠 Junior Analyst: Verifying '{search_keyword}' at '{restaurant_name}'...")

    combined_text = "\n\n--- NEXT REVIEW ---\n\n".join(scraped_blog_texts)

    # ==========================================
    # PHASE 1: The Junior Analyst (Gatekeeper)
    # ==========================================
    analyst_instruction = f"""
    You are a meticulous data analyst reviewing Korean blog posts for a restaurant.
    The user is specifically looking for excellent: {search_keyword}.

    TASK 1: Verify if the restaurant actually specializes in or famously serves {search_keyword}. 
    If they just happen to have it on a massive menu, or don't serve it at all, flag 'serves_target_food' as false.

    TASK 2: If true, extract objective facts regarding:
    - Textural integrity (e.g., does the batter stay crispy, is the meat dry?)
    - Vibe and demographic (Is it packed with locals, tourists, or empty?)
    - 안주 (Anju) synergy with alcohol.

    Output strictly in JSON format matching this structure:
    {{
        "serves_target_food": (boolean),
        "extracted_facts_ko": (A detailed summary of the facts in Korean)
    }}
    """

    analyst_config = types.GenerateContentConfig(
        system_instruction=analyst_instruction,
        response_mime_type="application/json",
        temperature=0.2  # Lower temperature makes the AI more factual and less creative
    )

    analyst_prompt = f"Analyze these reviews for {restaurant_name}:\n\n{combined_text}"

    try:
        analyst_response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=analyst_prompt,
            config=analyst_config
        )
        analyst_data = json.loads(analyst_response.text)

        # The Menu Gatekeeper
        if not analyst_data.get("serves_target_food", False):
            print(f"🛑 REJECTED: The Analyst confirmed this place does not focus on {search_keyword}.")
            return {"score": 0, "award_level": "None", "justification": f"Does not specialize in {search_keyword}."}

        extracted_facts = analyst_data.get("extracted_facts_ko", "")
        print("✅ Menu verified. Facts extracted. Handing to Head Critic.")

    except Exception as e:
        print(f"❌ Junior Analyst Error: {e}")
        return None

    # ==========================================
    # PHASE 2: The Head Critic (The Harsh Judge)
    # ==========================================
    print(f"👑 Head Critic: Scoring rigorously...")

    critic_instruction = f"""
    You are the Head Critic for the 'Neon Guide', an elite guide evaluating Korean comfort food and 안주.
    You are notoriously strict. You are evaluating this restaurant for its quality regarding: {search_keyword}.

    Score the restaurant out of 100 using this severe rubric:
    - 95-100: Legendary. Reviewers mention traveling specifically for this. Flawless execution.
    - 88-94: Exceptional. A neighborhood staple with undeniably superior technique.
    - 80-87: Great. Solid choice if you are in the area, but not worth a cross-town trip.
    - Under 80: Average or tourist trap. Do not award any hearts.

    Award Levels:
    - 95+: "3 Neon Hearts"
    - 88-94: "2 Neon Hearts"
    - 80-87: "1 Neon Heart"
    - <80: "None"

    Return ONLY a valid JSON object.
    Structure:
    {{
        "score": (integer 0-100),
        "award_level": (string),
        "description_en": (A punchy, honest 2-sentence English description. Be critical if needed.),
        "description_ko": (A natural, 2-sentence Korean description),
        "justification": (1 sentence explaining EXACTLY why it earned this specific score and why it lost points.)
    }}
    """

    critic_config = types.GenerateContentConfig(
        system_instruction=critic_instruction,
        response_mime_type="application/json",
        temperature=0.4
    )

    critic_prompt = f"Critique this factual summary for {restaurant_name}:\n\n{extracted_facts}"

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