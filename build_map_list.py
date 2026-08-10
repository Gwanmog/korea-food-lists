from __future__ import annotations
import sys
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import math
import argparse
import csv
import json
import os
import re
import time
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from slugify import slugify

# Same convention as master_agent.py / critic_agent.py: keys live in soul-food-api/.env.
# Without this, kakao_rest_key() returns None and ledger enrichment silently no-ops,
# which leaves every place without a kakao_id and defeats deduplication.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / "soul-food-api" / ".env")

# -------------------------
# Constants & Config
# -------------------------
MICHELIN_BASE = "https://guide.michelin.com"
MICHELIN_SEOUL_LIST = "https://guide.michelin.com/us/en/seoul-capital-area/kr-seoul/restaurants"

BLUER_BASE = "https://bluer.co.kr"
BLUER_API = f"{BLUER_BASE}/api/v1"

DIR_RAW = Path("data/raw")
DIR_CACHE = Path("data/cache")
DIR_SITE = Path("site")


# -------------------------
# Data Model
# -------------------------
@dataclass
class Place:
    source: str
    name: str
    address: str | None
    city: str | None
    country: str | None
    category: str | None
    cuisine: str | None
    price: str | None
    phone: str | None
    url: str | None
    year: str | None
    description: str | None
    latitude: float | None
    longitude: float | None
    captured_at: str
    kakao_id: str | None = None
    kakao_url: str | None = None
    korean_query: str | None = None
    name_ko: str | None = None  # NEW: Official Korean Name (e.g. 정육면체)
    address_ko: str | None = None  # NEW: Official Korean Address
    description_ko: str | None = None  # Customer-facing Korean description
    # Every guide that recognises this restaurant, e.g.
    # [{"guide": "michelin", "tier": "1 Star"}, {"guide": "blueribbon", "tier": "RIBBON_TWO"}]
    # `category` stays the primary guide's tier so existing frontend logic keeps working.
    awards: list | None = None
    # Machine-readable tier code (NEON_3 / NEON_2 / NEON_1 / NEON_VETTED). Language- and
    # emoji-independent, so the frontend can filter on it instead of sniffing the
    # description text for "✨" — which broke as soon as the description changed.
    tier: str | None = None
    # Cuisine in the reader's language. Sources disagree: Michelin ships English,
    # Blue Ribbon and Neon ship Korean, so the label was mixed-language for everyone
    # regardless of the UI toggle. Populated from data/cuisine_map.json.
    cuisine_ko: str | None = None
    # Location tokens, extracted once at build time so the embeddings can carry them.
    # `district` is the 구 (25 across Seoul), `neighborhood` the 동 (~183 seen).
    # Road-name addresses rarely contain the 동, so it comes from Kakao's lot address.
    district: str | None = None
    neighborhood: str | None = None


PLACE_FIELDS = {f.name for f in fields(Place)}

# Which guide wins when one restaurant is recognised by several.
# Also the order awards are listed in, and the order sources appear in the
# space-joined `source` string that the frontend matches with .includes().
GUIDE_PRIORITY = ["michelin", "blueribbon", "neon"]


# -------------------------
# Caching / Ledger Logic
# -------------------------
class KakaoLedger:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}
        self.loaded = False

    def load(self):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                print(f"[ledger] loaded {len(self.data)} entries from {self.path}")
            except Exception as e:
                print(f"[ledger] failed to load cache: {e}")
                self.data = {}
        self.loaded = True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        print(f"[ledger] saved {len(self.data)} entries to {self.path}")

    def get_key(self, name: str, address: str | None) -> str:
        n = slugify(name or "unknown", lowercase=True)
        a = slugify(address or "", lowercase=True)
        return f"{n}__{a}"

    def get(self, name: str, address: str | None) -> dict | None:
        if not self.loaded: self.load()
        return self.data.get(self.get_key(name, address))

    def update(self, name: str, address: str | None, result: dict | None):
        if not self.loaded: self.load()
        key = self.get_key(name, address)
        self.data[key] = result


# -------------------------
# Address Translator
# -------------------------
def generate_korean_query(address: str) -> str | None:
    if not address: return None
    clean = re.sub(r'South Korea|Seoul|,|\b\d{5}\b', ' ', address).strip()
    gu_match = re.search(r'([a-zA-Z]+-gu)', clean, re.IGNORECASE)
    if not gu_match: return f"Seoul {clean}"
    gu = gu_match.group(1)
    rest = clean.replace(gu, "").strip()
    if re.match(r'^\d', rest):
        road_match = re.search(r'([a-zA-Z]+(-[a-zA-Z0-9]+)*)', rest)
        if road_match:
            road_start = road_match.start()
            number = rest[:road_start].strip()
            road_part = rest[road_start:].strip()
            return f"Seoul {gu} {road_part} {number}"
    return f"Seoul {gu} {rest}"


# -------------------------
# Kakao Logic
# -------------------------
def kakao_rest_key() -> str | None:
    return os.getenv("KAKAO_REST_API_KEY")


def kakao_address_search(s: requests.Session, api_key: str, address: str) -> dict | None:
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}
    q = generate_korean_query(address)
    if not q: return None
    try:
        params = {"query": q, "analyze_type": "similar"}
        r = s.get(url, params=params, headers=headers, timeout=5)
        r.raise_for_status()
        docs = r.json().get("documents", [])
        if docs:
            return {"x": docs[0]["x"], "y": docs[0]["y"]}
    except:
        pass
    return None


def kakao_local_keyword_search(s: requests.Session, api_key: str, query: str, x: float | None, y: float | None,
                               radius: int = 2000) -> list[dict]:
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params = {"query": query[:80], "size": 3, "category_group_code": "FD6"}
    if x and y:
        params.update({"x": str(x), "y": str(y), "radius": str(radius), "sort": "distance"})
    headers = {"Authorization": f"KakaoAK {api_key}"}
    try:
        r = s.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 400: return []
        r.raise_for_status()
        return r.json().get("documents", [])
    except requests.RequestException:
        return []


def _backfill_address_from_kakao(p: Place, doc: dict | None):
    """
    Copy Kakao's official Korean address onto the Place when we don't already have one.

    Neon Guide entries are built with address=None/address_ko=None (load_neon_guide), so
    ~60% of the map carried no address in either language — invisible in the popup, and
    invisible to the AI, which reads `address_ko || address` when ranking by location.
    Kakao already returns road_address_name for these; we just never stored it.

    Only fills blanks — never overwrites an address the guide itself supplied.
    """
    if not doc or not isinstance(doc, dict):
        return
    road = (doc.get("road_address_name") or "").strip()
    lot = (doc.get("address_name") or "").strip()
    korean = road or lot
    if korean:
        if not (p.address_ko or "").strip():
            p.address_ko = korean
        if not (p.address or "").strip():
            p.address = korean
    if not (p.name_ko or "").strip() and (doc.get("place_name") or "").strip():
        p.name_ko = doc["place_name"].strip()

    # Location tokens for the embeddings (Overhaul 4.1). Always set, even when the guide
    # already supplied an address — the 동 in particular is only reliably available from
    # Kakao's lot-based address, since road-name addresses omit it.
    for source_addr in (lot, road, p.address_ko or "", p.address or ""):
        if not p.district:
            m = re.search(r"([가-힣]+구)(?:\s|$|,)", source_addr)
            if m:
                p.district = m.group(1)
        if not p.neighborhood:
            m = re.search(r"([가-힣]+[0-9]*가?동)(?:\s|$|,)", source_addr)
            if m:
                p.neighborhood = m.group(1)


def enrich_places_with_ledger(places: list[Place], ledger: KakaoLedger) -> list[Place]:
    api_key = kakao_rest_key()
    if not api_key:
        print("[kakao] NO API KEY FOUND. Skipping enrichment.")
        return places

    s = requests.Session()
    ledger.load()

    hits, misses, api_calls = 0, 0, 0
    out: list[Place] = []

    for p in places:
        has_coords = isinstance(p.latitude, float) and isinstance(p.longitude, float)
        cached = ledger.get(p.name, p.address)

        if cached and cached.get("found") is not False:
            hits += 1
            if has_coords and cached.get("y") and cached.get("x"):
                dist = haversine_distance(p.latitude, p.longitude, float(cached["y"]), float(cached["x"]))
                if dist > 2000:
                    print(f"[sanity] Rejecting cached ID for {p.name} (Distance {dist:.0f}m)")
                    p.kakao_id = None
                    p.kakao_url = None
                else:
                    p.kakao_id = cached.get("id")
                    p.kakao_url = cached.get("place_url")
            else:
                p.kakao_id = cached.get("id")
                p.kakao_url = cached.get("place_url")
                if not has_coords and cached.get("y"): p.latitude = float(cached["y"])
                if not has_coords and cached.get("x"): p.longitude = float(cached["x"])
            _backfill_address_from_kakao(p, cached)
            out.append(p)
            continue

        api_calls += 1
        misses += 1
        found_doc = None

        # PRIORITY: Try searching with the Official Korean Name if we found it
        search_terms = []
        if p.name_ko: search_terms.append(p.name_ko)
        search_terms.append(p.name)

        if has_coords:
            for term in search_terms:
                docs = kakao_local_keyword_search(s, api_key, term, p.longitude, p.latitude, radius=500)
                if docs: found_doc = docs[0]; break
            if not found_doc: found_doc = {"id": None, "place_url": None, "x": str(p.longitude), "y": str(p.latitude)}

        elif p.address:
            # Try searching address using Korean Address if available
            addr_to_use = p.address_ko if p.address_ko else p.address
            # If it's Korean, address search works better without my English-flipper logic
            # But the existing logic handles English well. Let's try English address first.
            coords = kakao_address_search(s, api_key, p.address)
            if coords:
                lat, lon = float(coords["y"]), float(coords["x"])
                for term in search_terms:
                    docs = kakao_local_keyword_search(s, api_key, term, lon, lat, radius=100)
                    if docs: found_doc = docs[0]; break
                if not found_doc: found_doc = {"id": None, "place_url": None, "x": coords["x"], "y": coords["y"]}

        if not found_doc and not has_coords:
            if p.source == "michelin":
                ledger.update(p.name, p.address, {"found": False})
                out.append(p)
                continue

            # Fallback for Blue Ribbon
            candidates = [p.name]
            if p.address: candidates.append(generate_korean_query(p.address))
            for q in candidates:
                if not q: continue
                docs = kakao_local_keyword_search(s, api_key, q, None, None)
                if docs: found_doc = docs[0]; break
                time.sleep(0.1)

        if found_doc:
            if has_coords:
                dist = haversine_distance(p.latitude, p.longitude, float(found_doc["y"]), float(found_doc["x"]))
                if dist > 2000:
                    print(f"[sanity] Rejecting API result for {p.name} (Distance {dist:.0f}m)")
                    ledger.update(p.name, p.address,
                                  {"found": True, "x": str(p.longitude), "y": str(p.latitude), "id": None,
                                   "place_url": None})
                    out.append(p)
                    continue

            ledger.update(p.name, p.address, found_doc)
            p.kakao_id = found_doc.get("id")
            p.kakao_url = found_doc.get("place_url")
            if not has_coords:
                if found_doc.get("y"): p.latitude = float(found_doc["y"])
                if found_doc.get("x"): p.longitude = float(found_doc["x"])
            _backfill_address_from_kakao(p, found_doc)
        else:
            ledger.update(p.name, p.address,
                          {"found": False} if not has_coords else {"found": True, "x": str(p.longitude),
                                                                   "y": str(p.latitude), "id": None, "place_url": None})

        out.append(p)
        if api_calls % 10 == 0: time.sleep(0.5)

    ledger.save()
    return out


# -------------------------
# Scrapers & IO
# -------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session_michelin() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"})
    return s


def make_session_bluer() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": f"{BLUER_BASE}/search", "Origin": BLUER_BASE})
    return s


def scrape_michelin_run(limit: int = 0) -> list[Place]:
    print("[michelin] Starting scrape...")
    s = make_session_michelin()
    captured_at = utc_now_iso()
    places = []
    page = 1
    detail_urls = set()

    # 1. Gather URLs
    while True:
        url = f"{MICHELIN_SEOUL_LIST}/page/{page}" if page > 1 else MICHELIN_SEOUL_LIST
        print(f"[michelin] listing page {page}...")
        try:
            r = s.get(url, timeout=20)
            if r.status_code == 404: break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.select("a[href*='/restaurant/']")
            if not links: break
            new_found = 0
            for link in links:
                full = urljoin(MICHELIN_BASE, link['href'])
                if full not in detail_urls:
                    detail_urls.add(full)
                    new_found += 1
            if new_found == 0: break
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"[michelin] list error: {e}")
            break

    sorted_urls = sorted(list(detail_urls))
    if limit: sorted_urls = sorted_urls[:limit]
    print(f"[michelin] found {len(sorted_urls)} details. Fetching...")

    # 2. Fetch Details (English + Korean)
    for i, u in enumerate(sorted_urls):
        try:
            time.sleep(0.5)
            # A. Fetch English
            r = s.get(u, timeout=20)
            soup = BeautifulSoup(r.text, "lxml")
            name = soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown"

            # Description etc
            desc_div = soup.select_one(".data-sheet__description")
            description = desc_div.get_text(strip=True) if desc_div else None

            price_cuisine_text = soup.select_one(".data-sheet__block--text")
            pc_text = price_cuisine_text.get_text(strip=True) if price_cuisine_text else ""
            price, cuisine = None, None
            if "·" in pc_text:
                parts = pc_text.split("·")
                price = parts[0].strip()
                cuisine = parts[1].strip()
            else:
                cuisine = pc_text

            # Geo
            lat, lon, address = None, None, None
            scripts = soup.find_all("script", {"type": "application/ld+json"})
            for sc in scripts:
                try:
                    data = json.loads(sc.string)
                    if not isinstance(data, list): data = [data]
                    for item in data:
                        if not address and item.get("address"):
                            addr_obj = item.get("address")
                            if isinstance(addr_obj, dict):
                                address = f"{addr_obj.get('streetAddress', '')}, {addr_obj.get('addressLocality', '')}"
                        if item.get("@type") in ("Restaurant", "FoodEstablishment"):
                            geo = item.get("geo", {})
                            if geo.get("latitude"): lat = float(geo.get("latitude"))
                            if geo.get("longitude"): lon = float(geo.get("longitude"))
                except:
                    pass

            if lat is None:
                lat_match = re.search(r'["\']?latitude["\']?\s*[:=]\s*["\']?([0-9.]+)["\']?', str(soup))
                if lat_match: lat = float(lat_match.group(1))
            if lon is None:
                lon_match = re.search(r'["\']?longitude["\']?\s*[:=]\s*["\']?([0-9.]+)["\']?', str(soup))
                if lon_match: lon = float(lon_match.group(1))

            if not address:
                m = re.search(r"([^\n]+,\s*Seoul)", soup.body.get_text())
                if m: address = m.group(1).strip()

            # B. THE DOUBLE DIP: Fetch Korean Version
            name_ko, address_ko = None, None
            try:
                # Replace /us/en/ with /kr/ko/
                url_ko = u.replace("/us/en/", "/kr/ko/")
                r_ko = s.get(url_ko, timeout=10)
                if r_ko.status_code == 200:
                    soup_ko = BeautifulSoup(r_ko.text, "lxml")
                    # Scrape Korean Name
                    h1_ko = soup_ko.find("h1")
                    if h1_ko: name_ko = h1_ko.get_text(strip=True)

                    # Scrape Korean Address (from LD-JSON or body)
                    scripts_ko = soup_ko.find_all("script", {"type": "application/ld+json"})
                    for sc in scripts_ko:
                        try:
                            d_ko = json.loads(sc.string)
                            if not isinstance(d_ko, list): d_ko = [d_ko]
                            for it in d_ko:
                                if it.get("address"):
                                    ao = it.get("address")
                                    if isinstance(ao, dict):
                                        address_ko = f"{ao.get('streetAddress', '')}, {ao.get('addressLocality', '')}"
                        except:
                            pass
            except Exception as e:
                print(f"    [ko-fetch] failed: {e}")

            # Categories
            category = "Selected"
            text_lower = soup.body.get_text().lower()
            if "3 stars" in text_lower:
                category = "3 Stars"
            elif "2 stars" in text_lower:
                category = "2 Stars"
            elif "1 star" in text_lower:
                category = "1 Star"
            elif "bib gourmand" in text_lower:
                category = "Bib Gourmand"

            p = Place(
                source="michelin", name=name, address=address, city="Seoul", country="South Korea",
                category=category, cuisine=cuisine, price=price, phone=None, url=u, year=None,
                description=description, latitude=lat, longitude=lon, captured_at=captured_at,
                name_ko=name_ko, address_ko=address_ko  # Save Korean info
            )
            places.append(p)
            print(f"  [{i + 1}/{len(sorted_urls)}] {name} -> {name_ko if name_ko else 'No Korean Name'}")

        except Exception as e:
            print(f"  [{i + 1}] Failed {u}: {e}")

    return places


def scrape_bluer_run() -> list[Place]:
    print("[bluer] Starting scrape...")
    s = make_session_bluer()
    captured_at = utc_now_iso()
    places = []
    zones = ["서울 강북", "서울 강남"]
    for zone in zones:
        print(f"[bluer] probing zone: {zone}")
        params = {"zone1": zone, "page": 1, "size": 30}
        consecutive_empty = 0
        while True:
            try:
                url = f"{BLUER_API}/restaurants?{urlencode(params)}"
                r = s.get(url, timeout=20)
                if r.status_code == 429: time.sleep(5); continue
                r.raise_for_status()
                data = r.json()
                embedded = data.get("_embedded", {})
                items = []
                for k, v in embedded.items():
                    if isinstance(v, list): items.extend(v)
                if not items: break
                found_on_page = 0
                for item in items:
                    header = item.get("headerInfo") or {}
                    ribbon = (header.get("ribbonType") or "").upper()
                    if ribbon in ["RIBBON_ONE", "RIBBON_TWO", "RIBBON_THREE"]:
                        found_on_page += 1
                        juso = item.get("juso") or {}
                        gps = item.get("gps") or {}
                        description = item.get("comment") or header.get("nameEN")

                        # Blue Ribbon has native Korean name already
                        name_kr = header.get("nameKR")
                        name_en = header.get("nameEN") or name_kr

                        p = Place(
                            source="blueribbon", name=name_en,
                            address=juso.get("roadAddrPart1"), city="Seoul", country="South Korea",
                            category=ribbon, cuisine=None, price=None, phone=item.get("defaultInfo", {}).get("phone"),
                            url=None, year=header.get("bookYear"), description=description,
                            latitude=float(gps["latitude"]) if gps.get("latitude") else None,
                            longitude=float(gps["longitude"]) if gps.get("longitude") else None,
                            captured_at=captured_at,
                            name_ko=name_kr,  # Map nameKR -> name_ko
                            address_ko=juso.get("roadAddrPart1")
                        )
                        places.append(p)
                if found_on_page == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0
                if consecutive_empty >= 5: print(f"  [bluer] 5 empty pages. Next zone."); break
                if "next" not in data.get("_links", {}): break
                params["page"] += 1
                time.sleep(0.5)
            except Exception as e:
                print(f"[bluer] error: {e}"); break
        save_raw(places, "blueribbon.csv")
    return places


def save_raw(places: list[Place], filename: str):
    path = DIR_RAW / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if not places: return
    keys = asdict(places[0]).keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for p in places: w.writerow(asdict(p))
    print(f"[io] saved {len(places)} to {path}")


def save_raw_guarded(places: list[Place], source: str, canonical_name: str) -> bool:
    """
    Validate a fresh scrape, and only then let it replace the file the build reads.

    Overhaul 1.5. `save_raw()` overwrote unconditionally, which is how a scrape that had
    lost every Michelin star replaced good data and shipped to the map for months. A
    degraded scrape now aborts and leaves the previous capture in place.

    Returns True if the new data was promoted.
    """
    from scrape_validation import validate_scrape, promote_capture

    if not places:
        print(f"[guard] {source}: scrape returned nothing — keeping previous capture.")
        return False

    rows = [asdict(p) for p in places]
    fieldnames = list(rows[0].keys())
    canonical = DIR_RAW / canonical_name

    report = validate_scrape(rows, source,
                             previous_path=canonical if canonical.exists() else None)
    print(report.render())

    if not report.ok:
        print(f"[guard] {source}: NOT promoted. '{canonical_name}' is untouched.\n"
              f"        Fix the scraper, or if the guide genuinely changed, update\n"
              f"        GUIDE_SPECS in scrape_validation.py deliberately.")
        return False

    promote_capture(rows, source, DIR_RAW, fieldnames, canonical_name=canonical_name)
    return True


def load_raw(filename: str) -> list[Place]:
    path = DIR_RAW / filename
    if not path.exists(): return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:

            # Map the AI Ghostwriter column onto the standard map column.
            if 'description_en' in row:
                if row['description_en'].strip():  # If the AI actually wrote something
                    row['description'] = row['description_en']
                del row['description_en']

            # Korean descriptions written by the Ghostwriter are kept: they are native
            # Korean, better than re-translating the English in build_embeddings.py,
            # and they save an API call per restaurant.

            # Drop any column that isn't a Place field so the dataclass can't panic.
            row = {k: v for k, v in row.items() if k in PLACE_FIELDS}

            out.append(Place(**row))
    return out


def load_neon_guide(filename: str) -> list[Place]:
    # Check the root folder first, then fallback to data/raw
    path = Path(filename)
    if not path.exists():
        path = DIR_RAW / filename

    if not path.exists():
        print(f"⚠️ Could not find {filename}. Skipping Neon Guide.")
        return []

    out = []
    captured_at = utc_now_iso()

    # ==========================================
    # 🏅 NEON TIERS — broad vetted floor, scarce peak
    # ==========================================
    # Overhaul 2.3. The old mapping awarded a tier to everything (>=90 "Exceptional",
    # >=80 "Highly Recommended"), which is how 44% of the guide ended up holding 2-3
    # hearts. These thresholds are global — a score means the same thing regardless of
    # dish, so a heart means the same thing everywhere on the map.
    #
    # Thresholds are expressed as scores rather than percentages on purpose: scores are
    # coarse integers with large tie groups (125 restaurants share 90), so a percentage
    # cutoff would slice a tie group arbitrarily and put identical scores in different
    # tiers depending on sort order.
    #
    # Measured against the 1,228-restaurant guide of 2026-08-09:
    #   NEON_3       >= 98      14 places   1.1%
    #   NEON_2       95-97      71 places   5.8%
    #   NEON_1       91-94     174 places  14.2%
    #   NEON_VETTED  70-90     969 places  79.0%
    NEON_TIERS = [
        (98, "NEON_3", "3 Neon Hearts"),
        (95, "NEON_2", "2 Neon Hearts"),
        (91, "NEON_1", "1 Neon Heart"),
        (0,  "NEON_VETTED", "Neon Vetted"),
    ]

    def get_neon_tier(score: int) -> tuple[str, str]:
        """Returns (tier_code, human_label) for a score."""
        for threshold, code, label in NEON_TIERS:
            if score >= threshold:
                return code, label
        return "NEON_VETTED", "Neon Vetted"

    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            score_str = str(row.get("Score", "0")).strip()
            try:
                score = int(float(score_str))
            except ValueError:
                score = 0

            # Skip places the Supreme Court rejected
            if score < 70: continue

            lat_str = str(row.get("Latitude", "")).strip()
            lon_str = str(row.get("Longitude", "")).strip()
            lat = float(lat_str) if lat_str and lat_str.lower() != "nan" else None
            lon = float(lon_str) if lon_str and lon_str.lower() != "nan" else None

            kakao_url = row.get("Kakao URL")
            kakao_id = None
            if kakao_url:
                m = re.search(r'kakao\.com/(\d+)', kakao_url)
                if m: kakao_id = m.group(1)

            tier_code, tier_label = get_neon_tier(score)
            guide_desc_en = (
                row.get("Guide Description", "").strip()
                or row.get("Description EN", "").strip()
            )
            guide_desc_ko = (
                row.get("Guide Description KO", "").strip()
                or row.get("Description KO", "").strip()
            )
            # The tier phrase used to be prepended to the description ("✨ Exceptional
            # Gastronomic Experience\n\n..."). That string was then embedded into the
            # search vector, so every Neon vector opened with the same boilerplate and
            # clustered by award tier as much as by food (Overhaul 4.2). The tier now
            # lives in its own field; the description is just the description.
            full_desc = guide_desc_en or tier_label
            full_desc_ko = guide_desc_ko or guide_desc_en or tier_label

            p = Place(
                source="neon",  # Changed to "neon" to match your app.js logic for yellow circles
                name=row.get("Restaurant Name", "Unknown"),
                name_ko=row.get("Restaurant Name"),
                address=None, address_ko=None, city="Seoul", country="South Korea",
                # Recomputed from the score — the CSV's stored "Award Level" came from
                # the old inflated scale and is deliberately ignored.
                category=tier_label,
                cuisine=row.get("Category", "Craft Beer / Dining"),
                price=None, phone=None, url=None, year="2026",
                description=full_desc, latitude=lat, longitude=lon,
                captured_at=captured_at, kakao_id=kakao_id, kakao_url=kakao_url,
                description_ko=full_desc_ko, tier=tier_code
            )
            out.append(p)

    print(f"[neon_guide] Loaded {len(out)} places into quality tiers from {filename}")
    return out

def write_geojson(places: list[Place]):
    DIR_SITE.mkdir(exist_ok=True)
    path = DIR_SITE / "places.geojson"
    features = []
    for p in places:
        if not p.latitude or not p.longitude: continue
        props = asdict(p)
        del props["latitude"]
        del props["longitude"]
        del props["captured_at"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},
            "properties": props
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=2)
    print(f"[io] wrote {len(features)} features to {path}")


def apply_cuisine_map(places: list[Place]) -> list[Place]:
    """
    Normalise `cuisine` to English and fill `cuisine_ko`, using the cached bilingual
    lookup built by build_cuisine_map.py.

    Overhaul 5.5.4. Falls back to the original string in both languages when a value
    isn't in the map, so an unmapped cuisine degrades to today's behaviour rather than
    vanishing from the popup.
    """
    path = Path("data/cuisine_map.json")
    if not path.exists():
        print("[cuisine] data/cuisine_map.json missing — run build_cuisine_map.py. Skipping.")
        return places

    with open(path, "r", encoding="utf-8") as f:
        cmap = json.load(f)

    mapped = 0
    unmapped_values: set[str] = set()
    for p in places:
        raw = (p.cuisine or "").strip()
        if not raw:
            continue
        entry = cmap.get(raw)
        if entry:
            p.cuisine = entry.get("en") or raw
            p.cuisine_ko = entry.get("ko") or raw
            mapped += 1
        else:
            p.cuisine_ko = raw
            unmapped_values.add(raw)

    print(f"[cuisine] bilingual labels applied to {mapped} places")
    if unmapped_values:
        # Loud on purpose. Silent fallback is how a new dish keyword would quietly ship
        # Korean text in the English field, which is the bug 5.5.4 exists to prevent.
        print(f"[cuisine] ⚠️ {len(unmapped_values)} UNMAPPED cuisine value(s) — these will "
              f"show in their original language to both audiences:")
        for v in sorted(unmapped_values)[:10]:
            print(f"           {v!r}")
        if len(unmapped_values) > 10:
            print(f"           ... and {len(unmapped_values) - 10} more")
        print("           Fix: run `python build_cuisine_map.py`, then rebuild.")
    return places


def merge_duplicate_places(places: list[Place]) -> list[Place]:
    """
    Collapse the same physical restaurant into one pin, carrying every guide's award.

    The guides name the same restaurant differently — Michelin romanises ("Bongsanok"),
    Blue Ribbon uses Hangul ("봉산옥") — so a name+address key can never match them.
    `kakao_id` is the only reliable join, which is why ledger enrichment must run
    BEFORE this function. Places with no resolved kakao_id fall back to a name+address
    slug, which at worst leaves them un-merged (a duplicate pin) rather than wrongly
    merging two different restaurants.

    The surviving Place takes its scalar fields from the highest-priority guide, fills
    any blanks from the others, and lists every award in `awards`. `source` becomes a
    space-joined string ("michelin blueribbon") so the frontend's existing
    `source.includes(...)` checks light up every guide's filter for that pin.
    """
    groups: dict[str, list[Place]] = {}
    order: list[str] = []

    for p in places:
        kid = str(p.kakao_id).strip() if p.kakao_id else ""
        key = f"kakao:{kid}" if kid and kid.lower() not in ("none", "nan") \
            else f"slug:{slugify(f'{p.name} {p.address or 0}')}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)

    def rank(p: Place) -> int:
        s = (p.source or "").lower()
        for i, g in enumerate(GUIDE_PRIORITY):
            if g in s:
                return i
        return len(GUIDE_PRIORITY)

    merged: list[Place] = []
    collapsed = 0
    multi_guide = 0

    for key in order:
        group = sorted(groups[key], key=rank)
        primary = group[0]

        if len(group) > 1:
            collapsed += len(group) - 1

            # Fill any field the primary left blank from the other guides, in priority order.
            for other in group[1:]:
                for f in PLACE_FIELDS:
                    if f in ("source", "category", "awards"):
                        continue
                    if not getattr(primary, f, None) and getattr(other, f, None):
                        setattr(primary, f, getattr(other, f))

        # Record every guide's award, de-duplicated, in priority order.
        awards, seen = [], set()
        for p in group:
            guide = next((g for g in GUIDE_PRIORITY if g in (p.source or "").lower()),
                         (p.source or "unknown").lower())
            tier = (p.category or "").strip()
            if not tier or tier.lower() == "none":
                continue
            if (guide, tier) in seen:
                continue
            seen.add((guide, tier))
            awards.append({"guide": guide, "tier": tier})

        primary.awards = awards

        # Space-joined so app.js's src.includes("michelin") / ("blue") / ("neon") all match.
        guides = []
        for p in group:
            g = next((g for g in GUIDE_PRIORITY if g in (p.source or "").lower()), None)
            if g and g not in guides:
                guides.append(g)
        if len(guides) > 1:
            multi_guide += 1
        primary.source = " ".join(sorted(guides, key=GUIDE_PRIORITY.index)) or primary.source

        merged.append(primary)

    print(f"[merge] {len(places)} records -> {len(merged)} pins "
          f"({collapsed} duplicates collapsed, {multi_guide} pins now hold multiple guides)")
    return merged


def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_fetch = subparsers.add_parser("fetch")
    p_fetch.add_argument("--test-limit", type=int, default=0)
    p_build = subparsers.add_parser("build")
    args = parser.parse_args()

    if args.command == "fetch":
        if args.test_limit:
            # A truncated run can't satisfy the row-count and tier floors, so validating
            # it would fail for the wrong reason. Write it somewhere harmless instead of
            # letting a test overwrite production input.
            print(f"[fetch] TEST MODE (limit={args.test_limit}) — writing to "
                  f"michelin.testrun.csv, NOT promoting.")
            save_raw(scrape_michelin_run(limit=args.test_limit), "michelin.testrun.csv")
            return

        ok_m = save_raw_guarded(scrape_michelin_run(), "michelin", "michelin.csv")
        ok_b = save_raw_guarded(scrape_bluer_run(), "blueribbon", "blueribbon.csv")

        if not (ok_m and ok_b):
            print("\n⚠️ One or more guides failed validation and were NOT promoted.\n"
                  "   The previous captures are still in place, so `build` is safe to run —\n"
                  "   it will just use the older data. Investigate before re-running fetch.")
            sys.exit(1)
        print("\n✅ Both guides scraped, validated, and promoted.")

    elif args.command == "build":
        m = load_raw("michelin.csv")
        enriched_path = DIR_RAW / "blueribbon_enriched.csv"
        if enriched_path.exists():
            print("🌟 Found AI-Enriched Blue Ribbon data! Using that instead.")
            b = load_raw("blueribbon_enriched.csv")
        else:
            b = load_raw("blueribbon.csv")
        n = load_neon_guide("neon_guide_audited_final.csv")
        all_places = m + b + n
        print(f"[build] loaded {len(m)} michelin + {len(b)} blueribbon + {len(n)} neon "
              f"= {len(all_places)} records")

        # Resolve kakao_id FIRST — it is the only key that can match a romanised
        # Michelin name to its Hangul Blue Ribbon counterpart. Deduplicating before
        # this ran is what left 104 restaurants on the map as two separate pins.
        enrich_places_with_ledger(all_places, KakaoLedger(DIR_CACHE / "kakao_ledger.json"))

        merged = merge_duplicate_places(all_places)
        apply_cuisine_map(merged)
        write_geojson(merged)

if __name__ == "__main__":
    main()