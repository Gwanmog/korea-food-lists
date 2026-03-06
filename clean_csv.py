import pandas as pd
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
csv_path = os.path.join(script_dir, 'neon_guide_review_queue.csv')

df = pd.read_csv(csv_path)

# Ensure data types are strings for the auditor cleanup
df['Rating Justified'] = df['Rating Justified'].astype(str).str.strip()
df['Needs Manual Review'] = df['Needs Manual Review'].astype(str).str.strip().str.title()
df['Auditor Reason'] = df['Auditor Reason'].astype(str)

cleared_count = 0

# 1. Fix the Auditor's broken logic (Illogical Combos & Pedantry)
for idx, row in df.iterrows():
    justified = row['Rating Justified']
    flagged = row['Needs Manual Review']
    reason = row['Auditor Reason']

    if justified == 'No' and flagged == 'False':
        df.at[idx, 'Rating Justified'] = ''
        cleared_count += 1

    elif flagged == 'True' and "UI buttons" not in reason:
        df.at[idx, 'Rating Justified'] = ''
        cleared_count += 1

print(f"🧹 Successfully cleared {cleared_count} Auditor fields for a re-run!")

# 2. 🚨 THE 0/100 PURGE 🚨
# Convert Score to numeric safely so we can filter it
initial_row_count = len(df)
df['Score'] = pd.to_numeric(df['Score'], errors='coerce').fillna(-1)

# Keep only rows where the score is strictly greater than 0
df_cleaned = df[df['Score'] > 0]
dropped_zeros_count = initial_row_count - len(df_cleaned)

# Save it back to the CSV
df_cleaned.to_csv(csv_path, index=False, encoding='utf-8-sig')

print(f"🗑️ Completely DELETED {dropped_zeros_count} rows with a 0/100 score.")
print("   (The Master Agent will automatically re-scrape and re-score these on its next run!)")