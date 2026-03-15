"""
audit_exceptional.py
AI-assisted audit of all restaurants scoring 90+ ("Exceptional") in
neon_guide_audited_final.csv using Gemini Flash.

For each entry, sends the Description EN + Justification to Gemini and asks
whether the 90+ rating seems credible. Outputs flagged entries sorted by
AI Verdict = NO first.

Output: out/exceptional_audit.csv
"""

import csv
import os
import sys
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
load_dotenv("soul-food-api/.env")  # fallback — key lives here in this project

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not GEMINI_API_KEY:
    raise EnvironmentError("GEMINI_API_KEY (or GOOGLE_API_KEY) not set in environment / .env")

INPUT_CSV = "neon_guide_audited_final.csv"
OUTPUT_CSV = "out/exceptional_audit.csv"
SCORE_THRESHOLD = 90
CHAR_LIMIT = 300  # chars to send per field

client = genai.Client(api_key=GEMINI_API_KEY)


def ask_gemini(restaurant_name: str, score: int, description_en: str, justification: str) -> tuple[str, str]:
    """Returns (verdict: 'YES'|'NO', reasoning: str)."""
    desc_snippet = description_en[:CHAR_LIMIT].strip()
    just_snippet = justification[:CHAR_LIMIT].strip()

    prompt = (
        f"Restaurant: {restaurant_name}\n"
        f"Score: {score}/100 (Exceptional tier, 90+)\n\n"
        f"Description: {desc_snippet}\n\n"
        f"Justification: {just_snippet}\n\n"
        "Based ONLY on the description and justification above, does this Exceptional (90+/100) "
        "rating seem credible? Answer YES or NO on the first line, then one sentence of reasoning."
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()
        lines = text.splitlines()
        first_line = lines[0].strip().upper() if lines else ""
        verdict = "YES" if first_line.startswith("YES") else "NO"
        reasoning = " ".join(lines[1:]).strip() if len(lines) > 1 else text
        return verdict, reasoning
    except Exception as e:
        return "ERROR", str(e)


def main():
    rows = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                score = int(row["Score"])
            except (ValueError, KeyError):
                continue
            if score >= SCORE_THRESHOLD:
                rows.append(row)

    print(f"Found {len(rows)} entries with score >= {SCORE_THRESHOLD}. Running Gemini audit...")

    os.makedirs("out", exist_ok=True)

    results = []
    for i, row in enumerate(rows, 1):
        name = row.get("Restaurant Name", "")
        score = int(row["Score"])
        desc_en = row.get("Description EN", "")
        justification = row.get("Justification", "")

        print(f"[{i}/{len(rows)}] {name} (score={score})", end=" ... ", flush=True)
        verdict, reasoning = ask_gemini(name, score, desc_en, justification)
        print(verdict)

        results.append({
            "Restaurant Name": name,
            "Score": score,
            "Award Level": row.get("Award Level", ""),
            "AI Verdict (YES/NO)": verdict,
            "AI Reasoning": reasoning,
            "Description EN": desc_en,
            "Justification": justification,
        })

        time.sleep(0.1)  # avoid rate limiting

    # Sort: NO verdicts first, then by score descending
    results.sort(key=lambda r: (0 if r["AI Verdict (YES/NO)"] == "NO" else 1, -r["Score"]))

    fieldnames = ["Restaurant Name", "Score", "Award Level", "AI Verdict (YES/NO)", "AI Reasoning", "Description EN", "Justification"]
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    no_count = sum(1 for r in results if r["AI Verdict (YES/NO)"] == "NO")
    yes_count = sum(1 for r in results if r["AI Verdict (YES/NO)"] == "YES")
    print(f"\n✅ Audit complete.")
    print(f"   Credible (YES): {yes_count}")
    print(f"   Flagged (NO):   {no_count}")
    print(f"   Results saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
