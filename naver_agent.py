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


def search_naver_blogs(restaurant_name, neighborhood):
    url = "https://openapi.naver.com/v1/search/blog.json"
    query = f"{neighborhood} {restaurant_name}"
    params = {
        "query": query,
        "display": 10,
        "sort": "sim"
    }

    # 2. Pull the actual keys from the loaded environment
    headers = {
        "X-Naver-Client-Id": os.getenv("NAVER_CLIENT_ID"),
        "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET")
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get('items', [])

    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        print(f"⚠️  TIMEOUT: Naver ignored '{restaurant_name}'. Skipping...")
        return []
    except requests.exceptions.RequestException as e:
        print(f"❌  ERROR: Naver API failed for '{restaurant_name}': {e}")
        return []


def scrape_naver_blog_text(blog_url):
    """
    Scrapes the text from a Naver blog post and captures the bottom images
    to detect sponsorship banners (e.g., '소정의 원고료', '협찬').
    """
    try:
        # 1. First request to the provided blog URL
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(blog_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 2. Naver blogs hide content inside an iframe. Find the iframe src.
        iframe = soup.find('iframe', id='mainFrame')
        if not iframe:
            return None

        iframe_src = iframe.get('src')
        if not iframe_src:
            return None

        # 3. Construct the real URL and fetch the actual content
        real_url = f"https://blog.naver.com{iframe_src}"
        real_response = requests.get(real_url, headers=headers, timeout=10)
        if real_response.status_code != 200:
            return None

        real_soup = BeautifulSoup(real_response.text, 'html.parser')

        # 4. Target ONLY the main content area (avoids sidebars/footers)
        content_area = real_soup.find('div', class_='se-main-container')
        if not content_area:
            # Fallback for older Naver blog formats
            content_area = real_soup.find('div', id='postViewArea')
            if not content_area:
                return None

        # 5. Extract Text
        # Get all text blocks and join them cleanly
        text_blocks = content_area.find_all(['p', 'span', 'div'], class_=lambda c: c and 'se-text' in c)
        if text_blocks:
            final_text = "\n".join([block.get_text(strip=True) for block in text_blocks if block.get_text(strip=True)])
        else:
            # Fallback text extraction
            final_text = content_area.get_text('\n', True)

        # --- 6. THE BULLETPROOF IMAGE SNIFFER ---
        last_images = []

        # Find all legitimate post images within the content container
        images = content_area.find_all('img', class_=lambda c: c and ('se-image' in c or 'se-sticker' in c))

        # Grab the last 2 images to ensure we don't miss a banner followed by a map
        for img in images[-2:]:
            if 'src' in img.attrs:
                last_images.append(img['src'])

        # 7. Return both the text and the image array
        return {"text": final_text, "bottom_images": last_images}

    except Exception as e:
        print(f"⚠️ Error scraping Naver blog {blog_url}: {e}")
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
            print(blog_text["text"][:500] + "...\n[CONTINUED]")
            print("-" * 50)
        else:
            print("\n❌ Failed to extract text. The blog layout might be unsupported.")