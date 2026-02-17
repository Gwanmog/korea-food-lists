from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from slugify import slugify

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
# Kakao Logic
# -------------------------
def kakao_rest_key() -> str | None:
    return os.getenv("KAKAO_REST_API_KEY")


def kakao_address_search(s: requests.Session, api_key: str, address: str) -> dict | None:
    """
    Smart Address Search that handles English-ordered addresses.
    """
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {api_key}"}

    # Clean Address
    clean_addr = re.sub(r'\b[B]?\d+F\b,?', '', address, flags=re.IGNORECASE)
    clean_addr = re.sub(r'\b\d{5}\b', '', clean_addr)
    clean_addr = clean_addr.replace("South Korea", "").replace("Seoul", "").replace(",", " ").strip()

    queries = [clean_addr]

    # Flip Logic (Gu Road Number)
    gu_match = re.search(r'([a-zA-Z]+-gu)', clean_addr, re.IGNORECASE)
    if gu_match:
        gu = gu_match.group(1)
        rest = clean_addr.replace(gu, "").strip()
        queries.insert(0, f"Seoul {gu} {rest}")

    for q in queries:
        q = re.sub(r'\s+', ' ', q).strip()
        if not q: continue
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
        # 0. TRUST THE SOURCE: Do we have valid coords from Scraper?
        has_coords = isinstance(p.latitude, float) and isinstance(p.longitude, float)

        # 1. Check Ledger
        cached = ledger.get(p.name, p.address)
        if cached:
            if cached.get("found") is not False:
                hits += 1
                p.kakao_id = cached.get("id")
                p.kakao_url = cached.get("place_url")

                # CRITICAL: Only overwrite coords if we STARTED with nothing.
                # If we have coords (from Michelin), ignore the cached location.
                if not has_coords:
                    if cached.get("y"): p.latitude = float(cached["y"])
                    if cached.get("x"): p.longitude = float(cached["x"])
            out.append(p)
            continue

        api_calls += 1
        misses += 1

        found_doc = None

        # 2. Strategy A: We have Coords -> Search NEARBY for Metadata
        if has_coords:
            # We trust the location, just looking for the Kakao ID
            docs = kakao_local_keyword_search(s, api_key, p.name, p.longitude, p.latitude, radius=200)
            if docs:
                found_doc = docs[0]
            else:
                # No ID found? Fine. Keep our coords and create a dummy record.
                found_doc = {"id": None, "place_url": None, "x": str(p.longitude), "y": str(p.latitude)}

        # 3. Strategy B: Address Search (Only if we didn't have coords)
        if not found_doc and p.address and not has_coords:
            coords = kakao_address_search(s, api_key, p.address)
            if coords:
                lat, lon = float(coords["y"]), float(coords["x"])
                # Temporarily set coords for the radius search
                docs = kakao_local_keyword_search(s, api_key, p.name, lon, lat, radius=100)
                if docs:
                    found_doc = docs[0]
                else:
                    found_doc = {"id": None, "place_url": None, "x": coords["x"], "y": coords["y"]}

        # 4. Strategy C: Keyword Fallback (Last Resort)
        if not found_doc and not has_coords:
            # Safety: Don't do global search for English names (too risky)
            if p.source == "michelin":
                print(f"[safety] Skipping keyword search for {p.name}")
                ledger.update(p.name, p.address, {"found": False})
                out.append(p)
                continue

            candidates = [p.name]
            if p.address:
                short_addr = " ".join(p.address.split()[:3])
                candidates.append(f"{p.name} {short_addr}")

            for q in candidates:
                docs = kakao_local_keyword_search(s, api_key, q, None, None)
                if docs:
                    found_doc = docs[0]
                    break
                time.sleep(0.1)

        # 5. Save result
        if found_doc:
            ledger.update(p.name, p.address, found_doc)
            p.kakao_id = found_doc.get("id")
            p.kakao_url = found_doc.get("place_url")

            # FINAL GUARD: Only update coords if we were missing them.
            if not has_coords:
                if found_doc.get("y"): p.latitude = float(found_doc["y"])
                if found_doc.get("x"): p.longitude = float(found_doc["x"])
        else:
            if has_coords:
                # We have coords but no ID. Save as "found" so we don't retry.
                ledger.update(p.name, p.address,
                              {"found": True, "x": str(p.longitude), "y": str(p.latitude), "id": None,
                               "place_url": None})
            else:
                ledger.update(p.name, p.address, {"found": False})

        out.append(p)
        if api_calls % 10 == 0:
            print(f"[kakao] progress: {api_calls} API calls made...")
            time.sleep(0.5)

    ledger.save()
    print(f"[kakao] Finished. Cache Hits: {hits}, API Calls: {api_calls}")
    return out


# -------------------------
# Scrapers
# -------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_session_michelin() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def make_session_bluer() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"{BLUER_BASE}/search",
        "Origin": BLUER_BASE,
    })
    return s


def scrape_michelin_run(limit: int = 0) -> list[Place]:
    print("[michelin] Starting scrape...")
    s = make_session_michelin()
    captured_at = utc_now_iso()
    places = []

    page = 1
    detail_urls = set()

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

    for i, u in enumerate(sorted_urls):
        try:
            time.sleep(0.5)
            r = s.get(u, timeout=20)
            soup = BeautifulSoup(r.text, "lxml")

            name = soup.find("h1").get_text(strip=True) if soup.find("h1") else "Unknown"

            # --- Description & Metadata ---
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

            # --- ROBUST GEO SCRAPING ---
            lat, lon, address = None, None, None

            # 1. Try ALL JSON-LD blocks
            scripts = soup.find_all("script", {"type": "application/ld+json"})
            for sc in scripts:
                try:
                    data = json.loads(sc.string)
                    # Normalize to list to handle both single dict and list of dicts
                    if not isinstance(data, list): data = [data]

                    for item in data:
                        # Extract Address
                        if not address and item.get("address"):
                            addr_obj = item.get("address")
                            if isinstance(addr_obj, dict):
                                address = f"{addr_obj.get('streetAddress', '')}, {addr_obj.get('addressLocality', '')}"

                        # Extract Geo (Prioritize Restaurant type)
                        if item.get("@type") in ("Restaurant", "FoodEstablishment"):
                            geo = item.get("geo", {})
                            if geo.get("latitude") and geo.get("longitude"):
                                lat = float(geo.get("latitude"))
                                lon = float(geo.get("longitude"))
                except:
                    pass

            # 2. Regex Fallback (If JSON failed)
            if lat is None or lon is None:
                # Look for patterns like "latitude": 37.123 or data-lat="37.123"
                lat_match = re.search(r'["\']?latitude["\']?\s*[:=]\s*["\']?([0-9.]+)["\']?', str(soup))
                lon_match = re.search(r'["\']?longitude["\']?\s*[:=]\s*["\']?([0-9.]+)["\']?', str(soup))
                if lat_match and lon_match:
                    lat = float(lat_match.group(1))
                    lon = float(lon_match.group(1))

            # 3. Address Fallback
            if not address:
                m = re.search(r"([^\n]+,\s*Seoul)", soup.body.get_text())
                if m: address = m.group(1).strip()

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
                description=description,
                latitude=lat, longitude=lon, captured_at=captured_at
            )
            places.append(p)
            print(f"  [{i + 1}/{len(sorted_urls)}] {name} (Coords: {lat}, {lon})")

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
                if r.status_code == 429:
                    time.sleep(5)
                    continue
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

                        p = Place(
                            source="blueribbon",
                            name=header.get("nameKR") or header.get("nameEN"),
                            address=juso.get("roadAddrPart1"),
                            city="Seoul", country="South Korea",
                            category=ribbon,
                            cuisine=None, price=None,
                            phone=item.get("defaultInfo", {}).get("phone"),
                            url=None,
                            year=header.get("bookYear"),
                            description=description,
                            latitude=float(gps["latitude"]) if gps.get("latitude") else None,
                            longitude=float(gps["longitude"]) if gps.get("longitude") else None,
                            captured_at=captured_at
                        )
                        places.append(p)

                if found_on_page == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

                if consecutive_empty >= 5:
                    print(f"  [bluer] 5 empty pages. Next zone.")
                    break

                if "next" not in data.get("_links", {}): break
                params["page"] += 1
                time.sleep(0.5)

            except Exception as e:
                print(f"[bluer] error: {e}")
                break

        save_raw(places, "blueribbon.csv")
    return places


# -------------------------
# IO
# -------------------------
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


def load_raw(filename: str) -> list[Place]:
    path = DIR_RAW / filename
    if not path.exists(): return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # FIX: Handle empty strings properly!
            row["latitude"] = float(row["latitude"]) if row.get("latitude") and str(row["latitude"]).strip() else None
            row["longitude"] = float(row["longitude"]) if row.get("longitude") and str(
                row["longitude"]).strip() else None

            if not row.get("description"): row["description"] = None
            out.append(Place(**row))
    return out


def write_geojson(places: list[Place]):
    DIR_SITE.mkdir(exist_ok=True)
    path = DIR_SITE / "places.geojson"
    features = []
    for p in places:
        if not p.latitude or not p.longitude: continue
        props = asdict(p)
        del props["latitude"];
        del props["longitude"];
        del props["captured_at"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},
            "properties": props
        })
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, ensure_ascii=False, indent=2)
    print(f"[io] wrote {len(features)} features to {path}")


# -------------------------
# Main
# -------------------------
def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    p_fetch = subparsers.add_parser("fetch")
    p_fetch.add_argument("--test-limit", type=int, default=0)
    p_build = subparsers.add_parser("build")

    args = parser.parse_args()

    if args.command == "fetch":
        save_raw(scrape_michelin_run(limit=args.test_limit), "michelin.csv")
        if args.test_limit == 0: save_raw(scrape_bluer_run(), "blueribbon.csv")

    elif args.command == "build":
        m = load_raw("michelin.csv")
        b = load_raw("blueribbon.csv")
        all_places = m + b

        unique = {}
        for p in all_places:
            key = slugify(f"{p.name} {p.address or ''}")
            if key not in unique:
                unique[key] = p
            elif p.source == "michelin":
                unique[key] = p

        merged = list(unique.values())
        enrich_places_with_ledger(merged, KakaoLedger(DIR_CACHE / "kakao_ledger.json"))
        write_geojson(merged)


if __name__ == "__main__":
    main()