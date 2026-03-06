import pandas as pd


def supreme_court_audit(csv_path, output_path):
    print("⚖️ Convening the Supreme Court for final manual review audit...")
    df = pd.read_csv(csv_path)

    # Ensure columns exist
    if 'Upgrade Recommended' not in df.columns:
        df['Upgrade Recommended'] = ''

    scraper_error_str = "Scraper reached the review page, but only found dates, UI buttons, or boilerplate text"

    for idx, row in df.iterrows():
        if row['Needs Manual Review'] in [True, 'True', 1.0, '1']:
            reason = str(row['Auditor Reason']).lower()

            # 1. Missing scraper data
            if scraper_error_str.lower() in reason:
                df.at[idx, 'Needs Manual Review'] = 'Rescrape'

            # 2. Authentic Negative Receipts vs High AI Score
            elif any(keyword in reason for keyword in
                     ['criticism', 'complaint', 'negative', 'police', 'contradictory', 'discrepancy', 'issue', '불만',
                      '부정적', '불일치']):

                # Catch the hallucination where "too cheap" was flagged as negative
                if '저렴하다는 부정적인 피드백' in reason or '저렴하다는 불만' in reason:
                    df.at[idx, 'Needs Manual Review'] = 'False'
                    df.at[
                        idx, 'Justification'] = "Supreme Court Verified: AI misinterpreted 'too cheap' as a negative. Customer reviews are highly positive."
                else:
                    # Revoke Award
                    df.at[idx, 'Score'] = 0
                    df.at[idx, 'Award Level'] = 'None'
                    df.at[
                        idx, 'Justification'] = "Supreme Court Overruled: Authentic customer receipts revealed major flaws (service/taste/hygiene). Revoked."
                    df.at[idx, 'Needs Manual Review'] = 'False'

            # 3. AI was too conservative (Reviews are flawless but score is low)
            elif any(keyword in reason for keyword in
                     ['conservative', 'enthusiastic', 'flawless', 'higher than', 'praise']):
                df.at[idx, 'Needs Manual Review'] = 'False'
                df.at[idx, 'Justification'] = str(
                    df.at[idx, 'Justification']) + " (Supreme Court Verified: Reviews confirm exceptional quality)."
            else:
                df.at[idx, 'Needs Manual Review'] = 'False'

    # ==========================================
    # 🍺 MANUAL DOMAIN KNOWLEDGE OVERRIDES
    # ==========================================
    # Add any manual audits here. The key just needs to be a unique part of the restaurant's name.

    MANUAL_OVERRIDES = {
        "서울브루어리 합정": {
            "Score": 90,
            "Justification": "Supreme Court Overruled (Manual Intervention): As a specialized brewery, exceptional craft beer quality and ambiance override average food critiques."
        },
        "맥파이": {
            "Score": 85,
            "Justification": "Supreme Court Overruled (Manual Intervention): A foundational pillar of Seoul's craft beer scene. The tap list takes priority over the food menu."
        },
        "서울집시": {
            "Score": 88,
            "Justification": "Supreme Court Overruled (Manual Intervention): Incredible experimental beers and unique fusion food. Protected from generic AI scoring."
        },
        "생활맥주 역삼점": {
            "Score": 75,
            "Justification": "Solid craft beer chain with decent food."
        },
        "서울브루어리 성수": {
            "Score": 85,
            "Justification": "Excellent craft brewery."
        },
        "호맥 서울합정점": {
            "Score": 70,
            "Justification": "Craft beer bar worth a visit."
        },
        "보글하우스": {
            "Score": 80,
            "Justification": "Craft-beer-centric restaurant worth a stop."
        },
        "롱타임노씨 신사점": {
            "Score": 70,
            "Justification": "Craft-beer-centric restaurant worth a stop."
        },
        "메즈나인브루잉컴퍼니": {
            "Score": 75,
            "Justification": "Craft brewery."
        },
        "크래프트리퍼블릭": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "교촌필방": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "생활맥주 합정역3번출구점": {
            "Score": 75,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "브루3.14": {
            "Score": 75,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "꼭그닭": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "생활맥주 남부터미널점": {
            "Score": 75,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "스태거": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "비어포스트바": {
            "Score": 72,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "서울집시 서순라길점": {
            "Score": 82,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "느린마을양조장 강남점": {
            "Score": 81,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "댕크야드 서울": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "춘풍양조장": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "뉴타운": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "에일당": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "붐박스 2호점": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "구스아일랜드 브루하우스": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "나즈드라비 강남대로점": {
            "Score": 80,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "느린마을양조장 연남점": {
            "Score": 79,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "땡기면땡비어": {
            "Score": 78,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "크래프트루 익선": {
            "Score": 77,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "감천양조장": {
            "Score": 76,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "스탑오버": {
            "Score": 75,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "황남주택 삼청점": {
            "Score": 74,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "느린마을양조장 홍대점": {
            "Score": 73,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "케그샵 프리미엄수제맥주&전통주셀렉샵 강남점": {
            "Score": 71,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "서울장수막걸리 체험관": {
            "Score": 71,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "아트몬스터 을지로점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "브루브루": {
            "Score": 71,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "을지맥옥": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "브루웍스 남부버스터미널점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "맥주집": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "블랙서커스": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "브루스카": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "같이양조장 합정점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "행아웃": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "밀회관 강남삼성타운점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "더테이블 마포점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "리퀴드랩": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "더부스 경리단점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "올드문래": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "낙타브루잉": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "브루웍스 역삼점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        },
        "더블린브루어리 강남점": {
            "Score": 70,
            "Justification": "Supreme Court Verified: A craft beer spot definitely worth a stop if you're in the area."
        }
    }
        # Just add a comma and drop your next audited brewery right here!

    # Apply the overrides
    for name_key, override_data in MANUAL_OVERRIDES.items():
        # Find all rows that contain the name_key
        mask = df['Restaurant Name'].str.contains(name_key, na=False)
        for i in df[mask].index:
            df.at[i, 'Score'] = override_data["Score"]
            df.at[i, 'Justification'] = override_data["Justification"]
            df.at[i, 'Needs Manual Review'] = 'False'
            print(f"   🍺 Applied manual brewery override for: {df.at[i, 'Restaurant Name']}")

    # Save the audited file
    df.to_csv(output_path, index=False)
    print(f"✅ Supreme Court Audit Complete! Clean file saved as {output_path}")


if __name__ == "__main__":
    supreme_court_audit('neon_guide_review_queue.csv', 'neon_guide_audited_final.csv')