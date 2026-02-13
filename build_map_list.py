from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import (
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
    parse_qsl,
)

import pandas as pd
import requests
from bs4 import BeautifulSoup
from slugify import slugify
import random


# -------------------------
# Constants
# -------------------------
MICHELIN_BASE = "https://guide.michelin.com"
MICHELIN_SEOUL_LIST = "https://guide.michelin.com/us/en/seoul-capital-area/kr-seoul/restaurants"

BLUER_BASE = "https://bluer.co.kr"
BLUER_API = f"{BLUER_BASE}/api/v1"


# -------------------------
# Data model
# -------------------------
@dataclass(frozen=True)
class Place:
    source: str  # "michelin" | "blueribbon"
    name: str
    address: str | None
    city: str | None
    country: str | None
    category: str | None
    cuisine: str | None
    price: str | None
    phone: str | None
    url: str | None
    year: str | None  # "2024" | "2025" | "2026" (Blue Ribbon has it)
    latitude: float | None
    longitude: float | None
    captured_at: str  # ISO timestamp


# -------------------------
# Shared helpers
# -------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_places_csv(places: list[Place], path: str) -> None:
    # Determine CSV columns
    if places:
        fieldnames = list(asdict(places[0]).keys())
    else:
        # Fall back to all dataclass fields
        empty = Place(
            source="",
            name="",
            address=None,
            city=None,
            country=None,
            category=None,
            cuisine=None,
            price=None,
            phone=None,
            url=None,
            year=None,
            latitude=None,
            longitude=None,
            captured_at="",
        )
        fieldnames = list(asdict(empty).keys())

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in places:
            w.writerow(asdict(p))


def normalize_key(name: str, address: str | None) -> str:
    n = slugify(name, lowercase=True)
    a = slugify(address or "", lowercase=True)
    return f"{n}__{a}" if a else n


def merge_places(*lists: Iterable[Place]) -> list[Place]:
    merged: dict[str, Place] = {}
    for lst in lists:
        for p in lst:
            key = normalize_key(p.name, p.address)
            if key not in merged:
                merged[key] = p
            else:
                cur = merged[key]
                better = cur

                # Prefer address
                if (not cur.address) and p.address:
                    better = p

                # Prefer coordinates
                if (better.latitude is None or better.longitude is None) and (p.latitude is not None and p.longitude is not None):
                    better = p

                # Prefer URL
                if (not (better.url or "")) and p.url:
                    better = p

                merged[key] = better
    return list(merged.values())


def write_kml_for_mymaps_layered(places: list[Place], path: str) -> None:
    """
    Writes KML with Folders so Google My Maps imports as toggleable layers.
    - Michelin: 1/2/3 Stars + Bib Gourmand + Selected + Other
    - Blue Ribbon: 1/2/3
    """

    def esc(x: str) -> str:
        return (x or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def category_bucket(p: Place) -> str:
        if p.source == "michelin":
            c = (p.category or "").strip()
            if c == "3 Stars":
                return "⭐⭐⭐ Michelin 3 Star"
            if c == "2 Stars":
                return "⭐⭐ Michelin 2 Star"
            if c == "1 Star":
                return "⭐ Michelin 1 Star"
            if c == "Bib Gourmand":
                return "🟢 Michelin Bib Gourmand"
            if c == "Selected":
                return "⚪ Michelin Selected"
            return "⚫ Michelin Other"

        if p.source == "blueribbon":
            c = (p.category or "").strip().upper()
            if c == "RIBBON_THREE":
                return "🔵🔵🔵 Blue Ribbon 3"
            if c == "RIBBON_TWO":
                return "🔵🔵 Blue Ribbon 2"
            if c == "RIBBON_ONE":
                return "🔵 Blue Ribbon 1"
            return "🔘 Blue Ribbon Other"

        return "Other"

    # Group places into folders
    folders: dict[str, list[Place]] = {}
    for p in places:
        folders.setdefault(category_bucket(p), []).append(p)

    # Folder ordering (nice UX)
    folder_order = [
        "⭐⭐⭐ Michelin 3 Star",
        "⭐⭐ Michelin 2 Star",
        "⭐ Michelin 1 Star",
        "🟢 Michelin Bib Gourmand",
        "⚪ Michelin Selected",
        "⚫ Michelin Other",
        "🔵🔵🔵 Blue Ribbon 3",
        "🔵🔵 Blue Ribbon 2",
        "🔵 Blue Ribbon 1",
        "🔘 Blue Ribbon Other",
        "Other",
    ]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        "  <Document>",
        "    <name>Korea Food Lists (Michelin + Blue Ribbon)</name>",
        "    <description>Generated by build_map_list.py</description>",
    ]

    def placemark(p: Place) -> list[str]:
        desc_parts = [
            f"Source: {esc(p.source)}",
            f"Category: {esc(p.category or '')}",
            f"Year: {esc(p.year or '')}",
            f"Cuisine: {esc(p.cuisine or '')}",
            f"Price: {esc(p.price or '')}",
            f"Phone: {esc(p.phone or '')}",
            f"URL: {esc(p.url or '')}",
            f"Captured: {esc(p.captured_at)}",
        ]
        desc = "<br/>".join([x for x in desc_parts if x.split(': ', 1)[1]])

        out = [
            "      <Placemark>",
            f"        <name>{esc(p.name)}</name>",
            f"        <description><![CDATA[{desc}]]></description>",
        ]

        if p.latitude is not None and p.longitude is not None:
            out += [
                "        <Point>",
                f"          <coordinates>{p.longitude},{p.latitude},0</coordinates>",
                "        </Point>",
            ]
        else:
            out += [f"        <address>{esc(p.address or '')}</address>"]

        out += ["      </Placemark>"]
        return out

    for folder_name in folder_order:
        ps = folders.get(folder_name)
        if not ps:
            continue

        # Sort within folder
        ps = sorted(ps, key=lambda x: x.name.lower())

        lines += [
            "    <Folder>",
            f"      <name>{esc(folder_name)}</name>",
            f"      <open>0</open>",
        ]

        for p in ps:
            lines += placemark(p)

        lines += [
            "    </Folder>",
        ]

    lines += ["  </Document>", "</kml>"]

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

# -------------------------
# MICHELIN
# -------------------------
def michelin_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (compatible; KoreaFoodListBuilder/1.0)",
            "Accept-Language": "en-US,en;q=0.8,ko;q=0.7",
        }
    )
    return s


def get_html(s: requests.Session, url: str, sleep_s: float, allow_404: bool = False) -> str | None:
    time.sleep(sleep_s)
    r = s.get(url, timeout=30)
    if allow_404 and r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def michelin_list_page_urls(s: requests.Session, first_list_url: str, sleep_s: float) -> list[str]:
    urls: list[str] = []
    page = 1

    while True:
        list_url = first_list_url if page == 1 else first_list_url.rstrip("/") + f"/page/{page}"
        html = get_html(s, list_url, sleep_s=sleep_s, allow_404=True)
        if html is None:
            break

        soup = BeautifulSoup(html, "lxml")
        found = set()

        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            if "/restaurant/" in href:
                found.add(urljoin(MICHELIN_BASE, href))

        if not found:
            break

        before = len(urls)
        for u in sorted(found):
            if u not in urls:
                urls.append(u)

        print(f"[michelin] page {page}: +{len(found)} candidates, total unique detail URLs={len(urls)}")

        if len(urls) == before:
            break

        page += 1
        if page > 50:
            break

    return urls

# --- helpers ---

def _as_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def _flatten_ldjson(data):
    """Yield all dict nodes from JSON-LD structures (dict/list, including @graph)."""
    stack = [data]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            yield obj
            g = obj.get("@graph")
            if isinstance(g, list):
                stack.extend(g)
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(obj, list):
            stack.extend(obj)

def _addr_to_str(addr):
    if isinstance(addr, str):
        return addr.strip()
    if isinstance(addr, dict):
        parts = [
            addr.get("streetAddress"),
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("postalCode"),
            addr.get("addressCountry"),
        ]
        return ", ".join([p for p in parts if p])
    return None

def _coords_from_hasmap(hasmap):
    """
    Michelin sometimes uses hasMap URLs (Google maps or OSM) containing coords.
    Try to extract lat/lon from common patterns.
    """
    if not isinstance(hasmap, str):
        return (None, None)

    # Patterns: "...?q=lat,lon" or "...@lat,lon," etc.
    m = re.search(r"[?&]q=([-0-9.]+),\s*([-0-9.]+)", hasmap)
    if m:
        return (_as_float(m.group(1)), _as_float(m.group(2)))

    m = re.search(r"@([-0-9.]+),\s*([-0-9.]+)", hasmap)
    if m:
        return (_as_float(m.group(1)), _as_float(m.group(2)))

    return (None, None)

def michelin_extract_coords_from_html(html: str) -> tuple[float | None, float | None]:
    # common patterns: @lat,lon or q=lat,lon or lat=...&lng=...
    m = re.search(r"@([-0-9.]+),\s*([-0-9.]+)", html)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"[?&]q=([-0-9.]+),\s*([-0-9.]+)", html)
    if m:
        return float(m.group(1)), float(m.group(2))

    m = re.search(r"[?&]lat=([-0-9.]+).*?[?&](?:lng|lon)=([-0-9.]+)", html)
    if m:
        return float(m.group(1)), float(m.group(2))

    return None, None


def michelin_extract_geo_and_address(soup: BeautifulSoup) -> tuple[float | None, float | None, str | None]:
    scripts = soup.find_all("script", {"type": "application/ld+json"})
    for sc in scripts:
        raw = (sc.string or "").strip()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for node in _flatten_ldjson(data):
            t = node.get("@type")

            # Accept broader venue types Michelin may use
            venue_types = {"Restaurant", "FoodEstablishment", "LocalBusiness"}
            is_venue = False
            if isinstance(t, str) and t in venue_types:
                is_venue = True
            elif isinstance(t, list) and any(x in venue_types for x in t if isinstance(x, str)):
                is_venue = True

            if not is_venue:
                continue

            # Address
            addr_str = _addr_to_str(node.get("address"))

            # Geo (multiple shapes)
            geo = node.get("geo") or {}
            lat = _as_float(geo.get("latitude", geo.get("lat")))
            lon = _as_float(geo.get("longitude", geo.get("lng", geo.get("lon"))))

            # hasMap fallback
            if lat is None or lon is None:
                lat2, lon2 = _coords_from_hasmap(node.get("hasMap"))
                lat = lat if lat is not None else lat2
                lon = lon if lon is not None else lon2

            # sanity range
            if lat is not None and lon is not None and (-90 <= lat <= 90) and (-180 <= lon <= 180):
                return lat, lon, addr_str

    return None, None, None

# --- rewritten method ---

def michelin_parse_detail(s: requests.Session, url: str, sleep_s: float, captured_at: str) -> Place:
    html = get_html(s, url, sleep_s=sleep_s)
    if html is None:
        raise RuntimeError(f"Failed to fetch Michelin detail page: {url}")

    soup = BeautifulSoup(html, "lxml")
    # TEMP: dump a short JSON-LD snippet so we can see Michelin's schema
    sc = soup.find("script", {"type": "application/ld+json"})
    if sc and sc.string:
        snippet = sc.string.strip().replace("\n", " ")
        print("[michelin ld snippet]", snippet[:600])
    else:
        print("[michelin ld snippet] (missing)")
    # TEMP: check for hasMap / geo keywords in the HTML
    print("[michelin debug] contains 'hasMap':", "hasMap" in html)
    print("[michelin debug] contains 'geo':", '"geo"' in html or "'geo'" in html)
    print("[michelin debug] contains 'latitude':", "latitude" in html)
    print("[michelin debug] contains 'longitude':", "longitude" in html)
    print("[michelin debug] contains 'map':", "map" in html.lower())

    lat, lon, addr_ld = michelin_extract_geo_and_address(soup)

    # ✅ new fallback if JSON-LD doesn't include geo
    if lat is None or lon is None:
        lat2, lon2 = michelin_extract_coords_from_html(html)
        lat = lat if lat is not None else lat2
        lon = lon if lon is not None else lon2

    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1]

    # TEMP DEBUG — you can delete later
    has_ld = soup.find("script", {"type": "application/ld+json"}) is not None
    has_next = soup.find("script", id="__NEXT_DATA__") is not None
    title = soup.title.get_text(strip=True) if soup.title else "(no title)"
    print(f"[michelin detail debug] url={url} | title={title!r} | ld+json={has_ld} | __NEXT_DATA__={has_next} | html_len={len(html)}")

    text = soup.body.get_text(separator="\n", strip=True) if soup.body else ""

    address = None
    city = None
    country = None

    # Heuristic address fallback
    m = re.search(r"([^\n]+,\s*South Korea)", text)
    if m:
        address = m.group(1).strip()
        country = "South Korea"
        if "Seoul" in address:
            city = "Seoul"

    cuisine = None
    price = None
    m_cp = re.search(r"(₩{1,4}|€{1,4}|\${1,4})\s*·\s*([A-Za-z0-9'’\-\s]+)", text)
    if m_cp:
        price = m_cp.group(1)
        cuisine = m_cp.group(2).strip()

    category = None
    if "Bib Gourmand" in text:
        category = "Bib Gourmand"
    elif re.search(r"\b3\s*Stars?\b", text):
        category = "3 Stars"
    elif re.search(r"\b2\s*Stars?\b", text):
        category = "2 Stars"
    elif re.search(r"\b1\s*Star\b", text):
        category = "1 Star"
    elif "Selected Restaurants" in text or "Selected" in text:
        category = "Selected"

    phone = None
    m_phone = re.search(r"\+82\s*\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}", text)
    if m_phone:
        phone = m_phone.group(0).replace(" ", "")

    # ✅ Geo + address from JSON-LD (robust extractor)
    lat, lon, addr_ld = michelin_extract_geo_and_address(soup)
    if addr_ld and not address:
        address = addr_ld
    if address:
        if "South Korea" in address:
            country = country or "South Korea"
        if "Seoul" in address:
            city = city or "Seoul"

    return Place(
        source="michelin",
        name=name,
        address=address,
        city=city,
        country=country,
        category=category,
        cuisine=cuisine,
        price=price,
        phone=phone,
        url=url,
        year=None,
        latitude=lat,
        longitude=lon,
        captured_at=captured_at,
    )

def scrape_michelin_seoul(out_csv: str, sleep_s: float = 0.35, limit: int = 0) -> list[Place]:
    s = michelin_session()
    captured_at = utc_now_iso()

    detail_urls = michelin_list_page_urls(s, MICHELIN_SEOUL_LIST, sleep_s=sleep_s)
    # ✅ Only keep Seoul URLs (pre-filter so test mode is fast and correct)
    detail_urls = [
        u for u in detail_urls
        if "/us/en/seoul-capital-area/kr-seoul/restaurant/" in u
    ]
    print(f"[michelin] filtered to Seoul detail URLs: {len(detail_urls)}")
    places: list[Place] = []

    attempted = 0

    for i, url in enumerate(detail_urls, start=1):
        if limit and attempted >= limit:
            print(f"[michelin] limit reached ({limit}) attempts. Stopping early.")
            break

        attempted += 1

        try:
            p = michelin_parse_detail(s, url, sleep_s=sleep_s, captured_at=captured_at)

            # Keep only Korea (defensive)
            if p.country == "South Korea" or (p.address and "South Korea" in p.address):
                places.append(p)

            print(f"[michelin] {i}/{len(detail_urls)} ok: {p.name}")
        except Exception as e:
            print(f"[michelin] {i}/{len(detail_urls)} FAIL {url}: {e}")

    write_places_csv(places, out_csv)
    return places

# -------------------------
# BLUE RIBBON (API)
# -------------------------
def bluer_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"{BLUER_BASE}/search",
            "Origin": BLUER_BASE,
        }
    )
    # Warm-up to collect cookies
    try:
        s.get(f"{BLUER_BASE}/search", timeout=30)
    except Exception:
        pass
    return s

def bluer_get_json(s: requests.Session, url: str, *, sleep_s: float, max_attempts: int = 10) -> dict:
    """
    Polite GET for Blue Ribbon with exponential backoff + jitter on 429/503.
    Works for page 1 and next pages.
    """
    attempt = 0
    while True:
        attempt += 1

        # Baseline politeness + jitter
        time.sleep(sleep_s + random.uniform(0.0, 0.4))

        r = s.get(url, timeout=30)

        if r.status_code in (429, 503):
            retry_after = r.headers.get("Retry-After")

            # Retry-After can be seconds OR a date string; we only trust numeric seconds.
            if retry_after and retry_after.strip().isdigit():
                wait = float(retry_after.strip())
            else:
                # Exponential backoff with cap + jitter
                wait = min(120.0, (2.0 ** min(attempt, 6)) + random.uniform(0.0, 1.0))

            print(f"[bluer] {r.status_code} rate-limited. Waiting {wait:.1f}s (attempt {attempt}/{max_attempts})...")
            time.sleep(wait)

            if attempt >= max_attempts:
                r.raise_for_status()
            continue

        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict JSON from {url}, got {type(data)}")
        return data

def normalize_bluer_next_url(next_href: str) -> str:
    """
    Normalize weird next links like 'http://bluer.co.kr:443/...'
    and dedupe repeated sort params.
    """
    if next_href.startswith("/"):
        raw = urljoin(BLUER_BASE, next_href)
    else:
        p0 = urlparse(next_href)
        raw = urlunparse(("https", "bluer.co.kr", p0.path, p0.params, p0.query, p0.fragment))

    p = urlparse(raw)
    qs = parse_qsl(p.query, keep_blank_values=True)

    seen_sorts = set()
    cleaned = []
    for k, v in qs:
        if k == "sort":
            if v in seen_sorts:
                continue
            seen_sorts.add(v)
        cleaned.append((k, v))

    new_query = urlencode(cleaned, doseq=True)
    return urlunparse((p.scheme, p.netloc, p.path, p.params, new_query, p.fragment))


def hal_extract_items(doc: dict) -> tuple[list[dict], Optional[str]]:
    items: list[dict] = []
    emb = doc.get("_embedded")
    if isinstance(emb, dict):
        for _, v in emb.items():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                items = v
                break

    next_href = None
    links = doc.get("_links")
    if isinstance(links, dict):
        nxt = links.get("next")
        if isinstance(nxt, dict) and isinstance(nxt.get("href"), str):
            next_href = nxt["href"]

    return items, next_href


def collect_bluer_restaurants_zone(
    s: requests.Session,
    *,
    zone1: str,
    sleep_s: float = 0.8,
    max_pages: int = 250,
) -> list[dict]:
    params = {
        "query": "",
        "zone1": zone1,
        "page": 1,
        "size": 24,
        "foodType": "",
        "foodTypeDetail": "",
    }
    url = f"{BLUER_API}/restaurants?{urlencode(params)}"
    doc = bluer_get_json(s, url, sleep_s=sleep_s)

    items, next_href = hal_extract_items(doc)
    all_items = list(items)
    page_num = 1
    print(f"[bluer] {zone1} page 1 items={len(items)} next={bool(next_href)}")

    while next_href and page_num < max_pages:
        page_num += 1
        url = normalize_bluer_next_url(next_href)

        doc = bluer_get_json(s, url, sleep_s=sleep_s)
        items, next_href = hal_extract_items(doc)
        all_items.extend(items)
        print(f"[bluer] {zone1} page {page_num} items={len(items)} next={bool(next_href)}")

        if not items:
            break

    # --- DEBUG breakdown ---
    def _get_header(it: dict) -> dict:
        h = it.get("headerInfo") or {}
        return h if isinstance(h, dict) else {}

    year_counts: dict[str, int] = {}
    ribbon_counts: dict[str, int] = {}
    missing_header = 0

    for it in all_items:
        h = _get_header(it)
        if not h:
            missing_header += 1
            continue
        y = str(h.get("bookYear") or "").strip() or "(missing)"
        r = str(h.get("ribbonType") or "").strip() or "(missing)"
        year_counts[y] = year_counts.get(y, 0) + 1
        ribbon_counts[r] = ribbon_counts.get(r, 0) + 1

    top_years = sorted(year_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
    top_ribbons = sorted(ribbon_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]

    print(f"[bluer] total items collected: {len(all_items)} (missing headerInfo: {missing_header})")
    print(f"[bluer] top bookYear counts: {top_years}")
    print(f"[bluer] top ribbonType counts: {top_ribbons}")
    # --- end debug ---

    return all_items

def is_blueribbon_winner(item: dict) -> bool:
    if item.get("status") != "ACTIVE":
        return False

    header = item.get("headerInfo") or {}
    ribbon_type = (header.get("ribbonType") or "").strip().upper()

    return ribbon_type in {"RIBBON_ONE", "RIBBON_TWO", "RIBBON_THREE"}

def bluer_item_to_place(item: dict, captured_at: str) -> Place:
    header = item.get("headerInfo") or {}
    juso = item.get("juso") or {}
    gps = item.get("gps") or {}
    default = item.get("defaultInfo") or {}

    name = header.get("nameKR") or header.get("nameEN") or "Unknown"
    year = header.get("bookYear")

    address = juso.get("engAddr") or juso.get("roadAddrPart1")
    if juso.get("roadAddrPart2") and address and address == juso.get("roadAddrPart1"):
        address = f"{address} {juso.get('roadAddrPart2')}"

    city = "Seoul" if (juso.get("siNm") or "").startswith("서울") else None
    country = "South Korea"

    category = header.get("ribbonType")  # RIBBON_ONE/TWO/THREE

    food_types = item.get("foodTypes") or []
    cuisine = ", ".join([x for x in food_types if isinstance(x, str)]) or None

    phone = (default.get("phone") or "").strip() or None
    url = (default.get("website") or "").strip() or None

    lat = gps.get("latitude")
    lon = gps.get("longitude")
    latitude = float(lat) if isinstance(lat, (int, float)) else None
    longitude = float(lon) if isinstance(lon, (int, float)) else None

    return Place(
        source="blueribbon",
        name=name,
        address=address,
        city=city,
        country=country,
        category=category,
        cuisine=cuisine,
        price=None,
        phone=phone,
        url=url,
        year=str(year) if year else None,
        latitude=latitude,
        longitude=longitude,
        captured_at=captured_at,
    )


def scrape_blueribbon_seoul(out_csv: str, sleep_s: float = 0.8) -> list[Place]:
    s = bluer_session()
    captured_at = utc_now_iso()

    zones = ["서울 강북", "서울 강남"]
    all_items: list[dict] = []

    for z in zones:
        print(f"[bluer] starting zone1={z}")
        items = collect_bluer_restaurants_zone(s, zone1=z, sleep_s=sleep_s)
        all_items.extend(items)
        zone_filtered = [it for it in items if is_blueribbon_winner(it)]
        print(f"[bluer] zone1={z} filtered winners 2024–2026: {len(zone_filtered)}/{len(items)}")
        time.sleep(3.0)

    filtered = [it for it in all_items if is_blueribbon_winner(it)]
    print(f"[bluer] filtered ribbon winners: {len(filtered)}/{len(all_items)}")
    places = [bluer_item_to_place(it, captured_at=captured_at) for it in filtered]

    write_places_csv(places, out_csv)
    return places


# -------------------------
# Optional: import Blue Ribbon from CSV
# -------------------------
def load_blueribbon_from_csv(csv_path: str) -> list[Place]:
    captured_at = utc_now_iso()
    df = pd.read_csv(csv_path)

    places: list[Place] = []
    for _, row in df.iterrows():
        places.append(
            Place(
                source="blueribbon",
                name=str(row.get("name", "")).strip(),
                address=(str(row.get("address")).strip() if pd.notna(row.get("address")) else None),
                city=(str(row.get("city")).strip() if pd.notna(row.get("city")) else None),
                country=(str(row.get("country")).strip() if pd.notna(row.get("country")) else "South Korea"),
                category=(str(row.get("category")).strip() if pd.notna(row.get("category")) else None),
                cuisine=(str(row.get("cuisine")).strip() if pd.notna(row.get("cuisine")) else None),
                price=(str(row.get("price")).strip() if pd.notna(row.get("price")) else None),
                phone=(str(row.get("phone")).strip() if pd.notna(row.get("phone")) else None),
                url=(str(row.get("url")).strip() if pd.notna(row.get("url")) else None),
                year=(str(row.get("year")).strip() if pd.notna(row.get("year")) else None),
                latitude=(float(row.get("latitude")) if pd.notna(row.get("latitude")) else None),
                longitude=(float(row.get("longitude")) if pd.notna(row.get("longitude")) else None),
                captured_at=captured_at,
            )
        )
    return places

import json

def write_geojson(places: list[Place], path: str) -> None:
    """
    Writes GeoJSON FeatureCollection.
    - Points only when lat/lon exist (Leaflet pins need coords)
    - Still includes all useful properties for UI/buttons
    """
    features = []
    for p in places:
        props = {
            "name": p.name,
            "source": p.source,
            "category": p.category,
            "cuisine": p.cuisine,
            "price": p.price,
            "phone": p.phone,
            "url": p.url,
            "address": p.address,
            "year": p.year,
        }

        if p.latitude is not None and p.longitude is not None:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [p.longitude, p.latitude]},
                    "properties": props,
                }
            )

    fc = {"type": "FeatureCollection", "features": features}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, ensure_ascii=False, indent=2)

# -------------------------
# Main
# -------------------------
def main() -> None:
    import os

    parser = argparse.ArgumentParser()

    # existing args (keep whatever you already had)
    parser.add_argument("--outdir", default="out", help="Output directory (default: out)")
    parser.add_argument("--sleep", type=float, default=1.5, help="Base sleep between requests (seconds)")
    parser.add_argument("--blueribbon_csv", default="", help="Optional extra Blue Ribbon CSV to merge")

    # ✅ TEMP: Michelin-only test mode (easy to remove later)
    parser.add_argument(
        "--test-michelin",
        type=int,
        default=0,
        help="TEMP: run ONLY Michelin scrape and exit early. Optionally limit to N places (0 disables).",
    )

    args = parser.parse_args()
    outdir = args.outdir.rstrip("/")

    os.makedirs(outdir, exist_ok=True)

    michelin_raw = f"{outdir}/michelin_seoul_raw.csv"
    bluer_raw = f"{outdir}/blueribbon_seoul_raw.csv"
    combined_csv = f"{outdir}/combined.csv"
    combined_kml_layered = f"{outdir}/combined_layered.kml"

    geojson_path = "site/places.geojson"

    def norm(s: str | None) -> str:
        return (s or "").strip().lower()

    def print_counts(label: str, places: list[Place]) -> None:
        mic_total = sum(1 for p in places if "michelin" in norm(p.source))
        br_total = sum(1 for p in places if "blue" in norm(p.source))
        other_total = len(places) - mic_total - br_total

        mic_map = sum(1 for p in places if "michelin" in norm(p.source) and p.latitude is not None and p.longitude is not None)
        br_map = sum(1 for p in places if "blue" in norm(p.source) and p.latitude is not None and p.longitude is not None)

        print(f"\n=== {label} ===")
        print("Total:", len(places))
        print("By source:", {"michelin": mic_total, "blueribbon": br_total, "other": other_total})
        print("Mappable by source:", {"michelin": mic_map, "blueribbon": br_map})

    # ============================================================
    # TEMP: Michelin-only test mode (skip Blue Ribbon entirely)
    # ============================================================
    if args.test_michelin:
        n = args.test_michelin  # N places to keep (e.g. 1, 5, 20)
        print(f"[main] TEST MODE: Michelin-only, limiting to first {n} places. (Blue Ribbon skipped)")
        michelin_places = scrape_michelin_seoul(
            michelin_raw,
            sleep_s=args.sleep,
            limit=args.test_michelin
        )

        # If your scrape returns more than N, truncate for speed
        if n > 0:
            michelin_places = michelin_places[:n]

        # Export just Michelin so your map can be validated quickly
        write_places_csv(michelin_places, f"{outdir}/michelin_only.csv")
        write_kml_for_mymaps_layered(michelin_places, f"{outdir}/michelin_only_layered.kml")
        write_geojson(michelin_places, geojson_path)

        print_counts("MICHELIN-ONLY TEST EXPORT", michelin_places)
        print(f"- Michelin raw: {michelin_raw} ({len(michelin_places)} places in test export)")
        print(f"- Michelin-only CSV: {outdir}/michelin_only.csv")
        print(f"- Michelin-only KML: {outdir}/michelin_only_layered.kml")
        print(f"- GeoJSON: {geojson_path}")
        print("\nDone (test mode).")
        return

    # ============================================================
    # Normal full run
    # ============================================================
    michelin_places = scrape_michelin_seoul(michelin_raw, sleep_s=args.sleep)
    print(f"[main] Michelin done: {len(michelin_places)} places. Starting Blue Ribbon…")
    print(f"[main] Blue Ribbon sleep_s={max(args.sleep, 1.5)}")
    bluer_places = scrape_blueribbon_seoul(bluer_raw, sleep_s=max(args.sleep, 1.5))

    extra_br: list[Place] = []
    if args.blueribbon_csv:
        extra_br = load_blueribbon_from_csv(args.blueribbon_csv)

    combined = merge_places(michelin_places, bluer_places, extra_br)
    combined.sort(key=lambda p: (p.source, (p.category or ""), p.name.lower()))

    write_places_csv(combined, combined_csv)
    write_kml_for_mymaps_layered(combined, combined_kml_layered)
    write_geojson(combined, geojson_path)

    print_counts("COMBINED EXPORT", combined)
    print(f"- MICHELIN raw: {michelin_raw} ({len(michelin_places)} places)")
    print(f"- Blue Ribbon raw: {bluer_raw} ({len(bluer_places)} places)")
    if extra_br:
        print(f"- Extra Blue Ribbon CSV merged: {len(extra_br)} places")
    print(f"- Combined CSV: {combined_csv} ({len(combined)} places)")
    print(f"- Combined KML (layered): {combined_kml_layered} ({len(combined)} places)")
    print(f"- GeoJSON: {geojson_path} ({sum(1 for p in combined if p.latitude and p.longitude)} mappable points)")
    print("\nNext step: open site/index.html or import KML into Google My Maps.")



if __name__ == "__main__":
    main()
