import os
import requests
from dotenv import load_dotenv

# 1. Foolproof Pathing (Finds the .env exactly where this script lives)
script_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Tell it to go one folder deeper into soul-food-api
env_path = os.path.join(script_dir, 'soul-food-api', '.env')

# 3. Load it!
load_dotenv(dotenv_path=env_path)

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

print("=== 🕵️ NAVER API DIAGNOSTIC ===")

# 2. Check if Python is actually reading the keys
if not CLIENT_ID or not CLIENT_SECRET:
    print("🚨 FATAL: Python cannot find your keys!")
    print(f"I am looking for the .env file exactly here: {env_path}")
    exit()
else:
    # Print just the first 4 characters to prove it loaded without exposing your whole key
    print(f"✅ Keys found in .env!")
    print(f"🔑 Client ID starts with: {CLIENT_ID[:4]}...")
    print(f"🔑 Client Secret starts with: {CLIENT_SECRET[:4]}...")

# 3. Check for the "Invisible Space" trap
if " " in CLIENT_ID or " " in CLIENT_SECRET:
    print("🚨 ERROR: You have invisible spaces in your keys in the .env file! Remove them.")

# 4. Make a direct, isolated test request
print("\n📡 Sending test ping to Naver...")
url = "https://openapi.naver.com/v1/search/blog.json?query=테스트&display=1"
headers = {
    "X-Naver-Client-Id": CLIENT_ID.strip(), # .strip() removes accidental newlines
    "X-Naver-Client-Secret": CLIENT_SECRET.strip()
}

response = requests.get(url, headers=headers)

print(f"\nResponse Status: {response.status_code}")

if response.status_code == 200:
    print("🎉 SUCCESS! The keys work perfectly. The issue is in how naver_agent.py is importing them.")
else:
    print("❌ FAILED. Naver rejected the keys.")
    print("Here is the exact reason Naver gave us:")
    print(f"➡️ {response.text}")