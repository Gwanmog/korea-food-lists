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



def get_kakao_categories(keyword, strict_mode=False):
    """
    Acts as a Pre-Flight Coordinator.
    Uses Gemini to translate a food keyword into Kakao Map category labels.
    """
    if strict_mode:
        print(f"🔒 STRICT MODE ON: Bypassing AI. Locking target strictly to '{keyword}'.")
        return [keyword]

    print(f"🧠 Coordinator: Translating '{keyword}' into Kakao categories...")

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
        print(f"   [Coordinator] Asking Gemini...")
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
            categories = [keyword]

        print(f"   ✅ Categories locked in by Gemini: {categories}")
        return categories
    except Exception as e:
        print(f"   ❌ Gemini failed: {e}. Defaulting to keyword only.")
        return [keyword]

# ==========================================
# 📋 DETERMINISTIC SPONSORSHIP DETECTION
# ==========================================
# Korean law requires bloggers to disclose paid/comped posts, and the disclosures use a
# small, stable set of stock phrases. That makes this a string-matching problem, not a
# judgement call — so we do it in Python instead of asking the LLM to count.
#
# Why this matters: 51% of the existing guide has NO sponsored ratio at all, which means
# the sponsorship caps in the critic prompt (score <= 70 at >=75% sponsored, <= 80 at
# >=50%) had nothing to act on for half the restaurants. Asking a model to count
# occurrences across a 10k-char concatenation is exactly the kind of task it's least
# reliable at, and it costs a token round-trip per restaurant.
#
# NOTE: this fixes FUTURE sweeps only. Scraped blog text is never persisted (see
# naver_agent.scrape_naver_blog_text), so existing rows cannot be backfilled without
# re-scraping — that happens in Overhaul Phase 3.
SPONSORSHIP_MARKERS = [
    # Direct payment / provision disclosures
    "소정의 원고료", "원고료를 지원", "원고료를 제공", "원고료 지원",
    "제품을 제공받아", "제공받아 작성", "제공 받아 작성", "무상으로 제공",
    "무료로 제공", "지원받아 작성", "지원 받아 작성",
    # Explicit sponsorship vocabulary
    "협찬받", "협찬 받", "협찬을", "협찬으로", "협찬사", "협찬 제품",
    # Paid-advertising labels
    "유료광고", "유료 광고", "광고성 게시물", "대가성",
    # Campaign / tester programs
    "체험단", "서포터즈", "리뷰어단", "시식권", "초대받아", "초대 받아",
    # Affiliate disclosures
    "파트너스 활동", "수수료를 제공받", "일정액의 수수료",
]


def detect_sponsorship(scraped_blog_data):
    """
    Counts how many scraped posts carry a paid-content disclosure.

    Returns (sponsored_count, total_count, ratio_string, ratio_float).
    ratio_string matches the format the critic prompt expects, e.g. "4/10 sponsored".
    """
    total = len(scraped_blog_data)
    if total == 0:
        return 0, 0, "0/0 sponsored", 0.0

    sponsored = 0
    for item in scraped_blog_data:
        text = (item.get("text") or "")
        # Collapse whitespace so "협찬 받아" and "협찬받아" both match reliably.
        squashed = "".join(text.split())
        if any("".join(marker.split()) in squashed for marker in SPONSORSHIP_MARKERS):
            sponsored += 1

    ratio = sponsored / total
    return sponsored, total, f"{sponsored}/{total} sponsored", ratio


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

        # 1. Swap double quotes for single quotes (prevents JSON string breaks)
        # 2. Strip raw newlines and tabs
        clean_text = raw_text.replace('"', "'").replace('\n', ' ').replace('\t', ' ')

        # 3. Cap every single blog at exactly 10,000 characters
        safe_texts.append(clean_text[:10000])

    # Join with a clean delimiter
    combined_text = " --- NEXT REVIEW --- ".join(safe_texts)
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
    3. Experience (서비스, 직원 태도, 식사 분위기)
    4. Value for money (가성비)
    5. Consistency (일관성)

    CRITICAL FILTERING RULE:
    If the sponsored ratio exceeds 50%, aggressively remove hyperbolic adjectives ("환상적인", "최고의", "인생맛집"), emotional language, and marketing tone. Extract ONLY cold, verifiable statements. If something cannot be objectively supported, exclude it.
    </task_3_fact_extraction>

    <task_4_red_flag_detection>
    Regardless of sponsorship ratio, scan ALL reviews for these serious problem patterns and list any that apply in red_flags.
    Apply stricter detection than you would for positive signals — a single credible complaint is enough to flag.

    - "hostile_staff": Any mention of rude, threatening, or coercive staff behavior.
      Signal words: 무서웠어요, 불친절, 강요, 소리질렀어요, 욕설, 갑질
    - "fraud_indicator": A review that contains "사기" or that simultaneously praises the restaurant AND labels it a scam or fraud.
      Also flag: reviews where the same post uses both highly positive and highly negative labels for the same experience.
    - "fake_review": Extreme implausible hyperbole that reads as astroturfing, not genuine emotion.
      Signal phrases: 죽다 살아났어요, 생명을 구해줬어요, 죽기 전에 꼭, or any claim that food had a life-saving or supernatural effect.
    - "quality_decline": Explicit complaints about food quality appearing in MULTIPLE reviews (not just one).
      Signal words: 잡내, 비린내, 맛없어요, 퍽퍽한, 질이 떨어졌어요, 예전만 못해요
    - "price_value_mismatch": Reviews in MULTIPLE posts that explicitly combine price complaints with small portions.
      Signal combination: 비싸요 + 양이 적어요, or 가성비가 나빠요, or 가격 대비 실망

    Leave red_flags as an empty list [] if none of the above are detected.
    </task_4_red_flag_detection>

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
    "red_flags": ["hostile_staff", "fraud_indicator"],
    "extracted_facts_ko": "Korean summary organized by the 5 criteria. The FIRST line must explicitly state the sponsored ratio."
}}
    </output_format>
    """

    analyst_data = None
    max_retries = 3

    for attempt in range(max_retries):
        try:
            print(f"   [Junior Analyst] Asking Gemini (Attempt {attempt + 1}/{max_retries})...")
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
            break

        except Exception as e:
            error_str = str(e)
            if "503" in error_str and attempt < max_retries - 1:
                print(f"   ⏳ Server overloaded (503). Waiting 3 seconds before retry...")
                time.sleep(3)
            else:
                print(f"   ❌ Gemini analyst failed permanently ({e}). Skipping restaurant.")
                return None

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
    red_flags = analyst_data.get("red_flags", [])

    # The Python count is authoritative — it always produces a value (no more blank
    # ratios) and doesn't depend on the model counting correctly across a long
    # concatenation. The analyst's own guess is kept only for comparison.
    spon_n, spon_total, sponsored_ratio, sponsored_pct = detect_sponsorship(scraped_blog_data)
    llm_guess = analyst_data.get('sponsored_ratio', 'unknown')
    if str(llm_guess).strip() and str(llm_guess) != sponsored_ratio:
        print(f"   📋 Sponsorship: counted {sponsored_ratio} (model said '{llm_guess}' — using counted)")
    else:
        print(f"   📋 Sponsorship: {sponsored_ratio}")

    print(f"   ✅ Facts extracted. Red flags: {red_flags if red_flags else 'None'}. "
          f"Handing to Head Critic.")

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
        The Junior Analyst has provided a summary of objective facts, including a "Sponsored Ratio" (협찬 비율) and any detected red_flags.
        </input_data>

        <scoring_rules>
        Score the restaurant out of 100. You MUST build the final score by assigning up to 20 points in each of these 5 categories:
        1. ingredients (Quality and sourcing of raw materials)
        2. technique (Mastery of flavor, cooking execution, temperature control)
        3. experience (Service quality, hospitality, staff conduct, dining atmosphere — including how the chef's personality or lack of care manifests in the guest's experience)
        4. value (Price relative to quality/portion)
        5. consistency (Evidence of long-term reputation and repeat local customers)

        POLARITY RULE: Negative reviews carry MORE weight than positive ones at equal frequency.
        A restaurant where multiple reviewers explicitly say "맛없어요" or "잡내" cannot score above 80 in technique or ingredients,
        regardless of how many positive mentions exist. One clear, specific complaint outweighs three vague compliments.
        </scoring_rules>

        <sponsorship_weighting_rule>
        Read the Sponsored Ratio carefully.
        - If the sponsored ratio is >= 75%: You MUST set "sponsorship_penalty_applied" to true. You must heavily deduct points from the "consistency" and "value" categories. The final calculated total score MUST NOT exceed 70. No exceptions.
        - Else if the sponsored ratio is >= 50%: You MUST set "sponsorship_penalty_applied" to true. You must heavily deduct points from the "consistency" and "value" categories. The final calculated total score MUST NOT exceed 80. No exceptions.
        - If the sponsored ratio is < 50%: Set "sponsorship_penalty_applied" to false. Score normally based purely on the culinary facts.
        </sponsorship_weighting_rule>

        <red_flags_rule>
        The Junior Analyst may have identified red_flags. Apply these mandatory caps BEFORE calculating the final score.
        If multiple red_flags are present, apply ALL applicable caps — do not average them.

        - "hostile_staff": experience MUST NOT exceed 8/20.
        - "fraud_indicator": value MUST NOT exceed 6/20. consistency MUST NOT exceed 6/20. Final score MUST NOT exceed 72.
        - "fake_review": Treat ALL positive facts as unverified. Score as if you had only neutral or negative facts. Final score MUST NOT exceed 75.
        - "quality_decline": technique MUST NOT exceed 10/20. ingredients MUST NOT exceed 12/20.
        - "price_value_mismatch": value MUST NOT exceed 8/20.

        If a red_flag cap conflicts with a sponsorship cap, apply whichever is lower.
        </red_flags_rule>

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
                "experience": (int 0-20),
                "value": (int 0-20),
                "consistency": (int 0-20),
                "sponsorship_penalty_applied": (boolean)
            }},
            "score": (integer, MUST equal the exact sum of the 5 categories above),
            "award_level": "string (e.g., '2 Neon Hearts' or 'None')",
            "description_en": "A punchy, customer-facing 2-3 sentence English description based ONLY on the blog data you just read. Mention specific dishes, techniques, or qualities that appear in the evidence. Write like a Michelin or Eater recommendation. Do NOT mention scores, sponsorship, inspector notes, or internal criteria.",
            "description_ko": "같은 내용을 자연스러운 한국어로 2-3문장. 블로그에서 확인된 내용만 사용하고, 점수나 협찬 관련 내용은 포함하지 마세요.",
            "justification": "1 sentence explaining the score breakdown for internal use only. If a sponsorship penalty or red_flag cap was applied, explicitly state that here."
        }}
        </output_format>
    """

    red_flags_str = json.dumps(red_flags)
    critic_contents = (
        f"Red flags detected by analyst: {red_flags_str}\n\n"
        # Stated explicitly and up front so the critic applies the sponsorship caps to a
        # verified number rather than hunting for the ratio inside the fact summary.
        f"VERIFIED Sponsored Ratio (counted programmatically, authoritative): "
        f"{sponsored_ratio} ({sponsored_pct:.0%})\n\n"
        f"Critique this summary for {restaurant_name}:\n\n{extracted_facts}"
    )

    try:
        print(f"   [Head Critic] Asking Gemini...")
        gemini_response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=critic_contents,
            config=types.GenerateContentConfig(
                system_instruction=critic_instruction,
                response_mime_type="application/json",
                temperature=0.4
            )
        )
        critic_data = json.loads(gemini_response.text)
        critic_data['sponsored_ratio'] = sponsored_ratio  # counted, never blank
        critic_data['red_flags'] = red_flags

        # Enforce the sponsorship caps in Python rather than trusting the model to
        # respect them. These are stated as hard rules in the critic prompt, so a score
        # above the cap is a prompt-compliance failure, not a judgement we should keep.
        try:
            score = int(critic_data.get('score', 0))
            cap = 70 if sponsored_pct >= 0.75 else (80 if sponsored_pct >= 0.50 else 100)
            if score > cap:
                print(f"   🔒 Sponsorship cap enforced: {score} -> {cap} ({sponsored_ratio})")
                critic_data['score'] = cap
                critic_data['justification'] = (
                    f"[Sponsorship cap applied: {sponsored_ratio}] "
                    f"{critic_data.get('justification', '')}"
                )
                if cap < 80:
                    critic_data['award_level'] = 'None'
        except (TypeError, ValueError):
            pass
        print("   ✅ Gemini successfully scored the restaurant!")
        return critic_data

    except Exception as gemini_e:
        print(f"   ❌ Gemini critic failed permanently ({gemini_e}). Skipping restaurant.")
        return None