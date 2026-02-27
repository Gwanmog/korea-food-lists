import os
import requests
import urllib.parse
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load the keys from your .env file
# --- FOOLPROOF ENV LOADING ---
# 1. Find the exact folder where this Python script lives
script_dir = os.path.dirname(os.path.abspath(__file__))

# 2. Point directly to the soul-food-api folder inside it
env_path = os.path.join(script_dir, 'soul-food-api', '.env')

# 3. Load the keys
load_dotenv(dotenv_path=env_path)

CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

# Fake browser headers so Naver doesn't block us for being a bot
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


def search_naver_blogs(restaurant_name, location="서울"):
    """Finds the top Naver Blog URLs for the restaurant."""
    print(f"\n🤖 Agent Task 1: Searching Naver for '{location} {restaurant_name}'...")

    query = f"{location} {restaurant_name}"
    encoded_query = urllib.parse.quote(query)
    url = f"https://openapi.naver.com/v1/search/blog.json?query={encoded_query}&display=10&sort=sim"

    auth_headers = {
        "X-Naver-Client-Id": CLIENT_ID,
        "X-Naver-Client-Secret": CLIENT_SECRET
    }

    response = requests.get(url, headers=auth_headers)

    if response.status_code == 200:
        return response.json()['items']
    else:
        print(f"❌ API Error: {response.status_code}")
        return None


def scrape_naver_blog_text(blog_url):
    """Bypasses Naver's iframe trap to extract the actual Korean text."""
    try:
        # 1. Fetch the main blog page
        response = requests.get(blog_url, headers=HEADERS)
        soup = BeautifulSoup(response.text, 'html.parser')

        # 2. Find the hidden iframe that holds the real content
        iframe = soup.find('iframe', id='mainFrame')
        if not iframe:
            return None

        # 3. Construct the true URL of the post
        real_url = "https://blog.naver.com" + iframe['src']

        # 4. Fetch the TRUE page
        real_response = requests.get(real_url, headers=HEADERS)
        real_soup = BeautifulSoup(real_response.text, 'html.parser')

        # 5. Extract the text. Naver's modern editor puts content in 'se-main-container'
        content_div = real_soup.find('div', class_='se-main-container')

        if content_div:
            # Extract all text, strip out weird spacing, and return it
            raw_text = content_div.get_text(separator=' ', strip=True)
            return raw_text
        else:
            return "Could not find the main text container."

    except Exception as e:
        print(f"❌ Scraping failed for {blog_url}: {e}")
        return None


# --- TEST THE FULL PIPELINE ---
if __name__ == "__main__":
    target_restaurant = "교촌치킨 강남역점"

    # 1. Get the URLs
    blog_results = search_naver_blogs(target_restaurant)

    if blog_results:
        # We will just test the very first blog post to keep it quick
        first_blog = blog_results[0]
        blog_url = first_blog['link']
        clean_title = first_blog['title'].replace('<b>', '').replace('</b>', '')

        print(f"✅ Found top post: {clean_title}")
        print(f"🤖 Agent Task 2: Attempting to scrape text from URL...")

        # 2. Scrape the actual text
        blog_text = scrape_naver_blog_text(blog_url)

        if blog_text:
            print("\n🎉 SUCCESS! Here is a preview of the extracted text:\n")
            print("-" * 50)
            # Print just the first 500 characters so we don't flood the terminal
            print(blog_text[:500] + "...\n[CONTINUED]")
            print("-" * 50)
        else:
            print("\n❌ Failed to extract text. The blog layout might be unsupported.")