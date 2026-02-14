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
import json
import os

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
@dataclass
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
    kakao_id: str | None = None
    kakao_url: str | None = None


# -------------------------
# Shared helpers
# -------------------------
def kakao_rest_key() -> str | None:
        return os.getenv("KAKAO_REST_API_KEY") or None

def kakao_local_keyword_search(
    s: requests.Session,
    *,
    api_key: str,
    query: str,
    x: float | None = None,  # longitude
    y: float | None = None,  # latitude
    radius_m: int | None = 2000,
    size: int = 5,
    sleep_s: float = 0.15,
    category_group_code: str | None = "FD6",  # restaurants
) -> list[dict]:
    """
    Kakao Local Keyword Search:
    https://dapi.kakao.com/v2/local/search/keyword.json

    Returns list of documents (dicts).
    Raises for non-HTTP issues, but lets caller handle HTTP errors.
    """
    q = (query or "").strip()
    if not q:
        return []

    # Kakao can be picky about very long queries; keep it sane.
    # (Business-name-only queries perform best anyway.)
    if len(q) > 80:
        q = q[:80].rstrip()

    time.sleep(sleep_s)

    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params = {"query": q, "size": int(size)}

    if category_group_code:
        params["category_group_code"] = category_group_code

    # If we have coordinates, bias results to that area.
    if x is not None and y is not None:
        params.update({"x": str(x), "y": str(y)})
        if radius_m is not None:
            r = max(0, min(int(radius_m), 20000))
            params["radius"] = str(r)

    r = s.get(url, params=params, headers={"Authorization": f"KakaoAK {api_key}"}, timeout=30)

    # Let caller decide what to do on 400/401/429 etc.
    r.raise_for_status()

    data = r.json()
    docs = data.get("documents") or []
    return docs if isinstance(docs, list) else []


def choose_best_kakao_doc(docs: list[dict], name: str, address: str | None) -> dict | None:
        """
        Very simple scorer:
        - prefer exact-ish name match
        - prefer address overlap
        - otherwise first
        """
        if not docs:
            return None

        n = (name or "").strip().lower()
        a = (address or "").strip().lower()

        def score(d: dict) -> int:
            s = 0
            pn = (d.get("place_name") or "").strip().lower()
            ra = (d.get("road_address_name") or "").strip().lower()
            aa = (d.get("address_name") or "").strip().lower()

            if pn == n:
                s += 50
            elif n and pn and (n in pn or pn in n):
                s += 25

            # any address overlap helps
            for candidate in (ra, aa):
                if a and candidate:
                    if candidate == a:
                        s += 40
                    elif a in candidate or candidate in a:
                        s += 15

            # prefer docs that have a phone (often more “real listing”)
            if (d.get("phone") or "").strip():
                s += 2

            return s

        return max(docs, key=score)

def enrich_with_kakao(places: list[Place], *, sleep_s: float = 0.15) -> list[Place]:
    key = kakao_rest_key()
    if not key:
        print("[kakao] KAKAO_REST_API_KEY not set; skipping enrichment.")
        return places

    def _extract_gu(addr: str | None) -> str | None:
        if not addr:
            return None
        # Works for both Korean "강남구" and English "Gangnam-gu"
        m = re.search(r"([A-Za-z]+-gu)\b", addr)
        if m:
            return m.group(1)
        m = re.search(r"([가-힣]+구)\b", addr)
        if m:
            return m.group(1)
        return None

    def _short_addr(addr: str | None, limit: int = 40) -> str | None:
        if not addr:
            return None
        # Strip punctuation that makes "address blobs"
        a = re.sub(r"[,()]+", " ", addr)
        a = re.sub(r"\s+", " ", a).strip()
        return a[:limit].rstrip() if a else None

    s = requests.Session()

    out: list[Place] = []
    hit = 0
    hard_fail = 0

    for p in places:
        if p.kakao_id or p.kakao_url:
            out.append(p)
            continue

        name = (p.name or "").strip()
        gu = _extract_gu(p.address)
        addr_short = _short_addr(p.address)

        # IMPORTANT: do NOT start with name+full address (too long / too address-y).
        candidates: list[str] = []
        if name:
            candidates.append(name)
        if name and gu:
            candidates.append(f"{name} {gu}")
        if name and addr_short:
            candidates.append(f"{name} {addr_short}")

        best_doc = None

        for q in candidates:
            try:
                docs = kakao_local_keyword_search(
                    s,
                    api_key=key,
                    query=q,
                    x=p.longitude,
                    y=p.latitude,
                    radius_m=2500,
                    size=5,
                    sleep_s=sleep_s,
                    category_group_code="FD6",
                )
            except requests.HTTPError as e:
                # If Kakao says 400, try the next shorter query instead of crashing.
                status = getattr(e.response, "status_code", None)
                if status == 400:
                    continue
                # If rate-limited, back off a bit then continue
                if status in (429,):
                    time.sleep(1.0)
                    continue
                # Any other HTTP error: surface but don't kill the whole run
                hard_fail += 1
                break

            best_doc = choose_best_kakao_doc(docs, p.name, p.address)
            if best_doc:
                break

        if best_doc:
            hit += 1

            d = asdict(p)  # includes kakao_id/kakao_url already (possibly None)
            d["kakao_id"] = str(best_doc.get("id") or "") or None
            d["kakao_url"] = str(best_doc.get("place_url") or "") or None

            out.append(Place(**d))
        else:
            out.append(p)

    print(f"[kakao] enriched {hit}/{len(places)} places with kakao place ids. hard_fail={hard_fail}")
    return out

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
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
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

def _coords_from_hasmap(hasmap: str | None) -> tuple[float | None, float | None]:
    if not hasmap or not isinstance(hasmap, str):
        return None, None

    # patterns like q=lat,lon
    m = re.search(r"[?&]q=([-0-9.]+),\s*([-0-9.]+)", hasmap)
    if m:
        return _as_float(m.group(1)), _as_float(m.group(2))

    # patterns like @lat,lon
    m = re.search(r"@([-0-9.]+),\s*([-0-9.]+)", hasmap)
    if m:
        return _as_float(m.group(1)), _as_float(m.group(2))

    return None, None

def _coords_from_html_lat_lon_literals(html: str) -> tuple[float | None, float | None]:
    """
    Extract latitude/longitude literal pairs from raw HTML, e.g.
    "latitude":37.1234 ... "longitude":126.9876
    """
    m_lat = re.search(r'"latitude"\s*:\s*([-0-9.]+)', html)
    m_lon = re.search(r'"longitude"\s*:\s*([-0-9.]+)', html)
    if m_lat and m_lon:
        lat = _as_float(m_lat.group(1))
        lon = _as_float(m_lon.group(1))
        if lat is not None and lon is not None and (-90 <= lat <= 90) and (-180 <= lon <= 180):
            return lat, lon
    return None, None

def michelin_extract_geo_address_from_page(soup: BeautifulSoup, html: str) -> tuple[float | None, float | None, str | None]:
    # 1) JSON-LD
    scripts = soup.find_all("script", {"type": "application/ld+json"})
    for sc in scripts:
        raw = (sc.string or "").strip()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # JSON-LD sometimes is a list, but your snippet shows a dict
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node.get("@type") not in ("Restaurant", "FoodEstablishment", "LocalBusiness"):
                continue

            addr_str = _addr_to_str(node.get("address"))

            # try geo if present (often missing)
            geo = node.get("geo") or {}
            lat = _as_float(geo.get("latitude", geo.get("lat")))
            lon = _as_float(geo.get("longitude", geo.get("lng", geo.get("lon"))))
            if lat is not None and lon is not None:
                return lat, lon, addr_str

            # try hasMap (your debug says this exists)
            lat2, lon2 = _coords_from_hasmap(node.get("hasMap"))
            if lat2 is not None and lon2 is not None:
                return lat2, lon2, addr_str

    # 2) Raw HTML fallback (your debug says latitude/longitude strings exist)
    lat3, lon3 = _coords_from_html_lat_lon_literals(html)
    if lat3 is not None and lon3 is not None:
        return lat3, lon3, None

    return None, None, None

def michelin_category_from_next_data(soup: BeautifulSoup) -> str | None:
    """
    Michelin pages are Next.js. __NEXT_DATA__ contains award/selection info, but
    the exact tokens vary by locale/build.

    We detect, in order:
    1) Common structured award slugs/codes (most reliable)
    2) Numeric star fields (keys containing 'star' with value 1/2/3)
    3) Broad string matching ("1 MICHELIN Star", "oneStar", etc.)
    """
    sc = soup.find("script", id="__NEXT_DATA__")
    raw = (sc.string or "").strip() if sc else ""
    if not raw:
        return None

    try:
        data = json.loads(raw)
    except Exception:
        return None

    # Collect strings + try to interpret structured award objects.
    strings: list[str] = []
    star_num: int | None = None

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.strip().lower())

    def award_from_obj(d: dict) -> str | None:
        """
        Michelin commonly stores award info in objects like:
          {"award": {"slug": "1-star-michelin"}}
          {"award": {"code": "m1"}}
          {"selection": "SELECTED_RESTAURANTS"}
        We look at common keys.
        """
        candidates = []

        for key in ("award", "awards", "selection", "distinction", "distinctions", "classification"):
            if key in d:
                candidates.append(d[key])

        # Flatten candidates into strings
        flat: list[str] = []

        def collect(x):
            if isinstance(x, dict):
                for kk, vv in x.items():
                    if isinstance(kk, str):
                        flat.append(kk)
                    collect(vv)
            elif isinstance(x, list):
                for vv in x:
                    collect(vv)
            elif isinstance(x, str):
                flat.append(x)

        for c in candidates:
            collect(c)

        blob = " ".join(flat).lower()

        # Very common slug/code styles
        if "bib" in blob and "gourmand" in blob:
            return "Bib Gourmand"
        if "3-star" in blob or "three-star" in blob or "3 star" in blob or "threestar" in blob:
            return "3 Stars"
        if "2-star" in blob or "two-star" in blob or "2 star" in blob or "twostar" in blob:
            return "2 Stars"
        if "1-star" in blob or "one-star" in blob or re.search(r"\b1\s*star\b", blob):
            return "1 Star"

        if "selected" in blob or "selection" in blob:
            return "Selected"

        # Sometimes Michelin uses "MICHELIN_STAR_1" / "ONE_STAR" style enums
        if "three" in blob and "star" in blob:
            return "3 Stars"
        if "two" in blob and "star" in blob:
            return "2 Stars"
        if "one" in blob and "star" in blob:
            return "1 Star"

        return None

    def walk(x):
        nonlocal star_num
        if isinstance(x, dict):
            # Try structured award detection at each dict node (cheap + high hit rate)
            aw = award_from_obj(x)
            if aw:
                strings.append(aw)  # stash result too
                # Return early? No: we still want to discover highest star_num if present
                # but we can short-circuit later.
                # We'll just record it in strings and keep walking.

            for k, v in x.items():
                kl = k.lower() if isinstance(k, str) else ""
                if isinstance(k, str):
                    strings.append(k)

                # numeric star signals
                if kl and isinstance(v, (int, float)):
                    if ("star" in kl or "stars" in kl) and int(v) in (1, 2, 3):
                        star_num = max(star_num or 0, int(v))

                # string star signals (sometimes "1", "2", "3")
                if kl and isinstance(v, str):
                    vv = v.strip()
                    if ("star" in kl or "stars" in kl) and vv.isdigit() and int(vv) in (1, 2, 3):
                        star_num = max(star_num or 0, int(vv))

                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str):
            strings.append(x)

    walk(data)

    # 1) If we discovered structured awards by inserting into strings, prefer them
    blob = norm(" ".join(strings))

    # If multiple appear, choose the "best"
    if "3 stars" in blob:
        return "3 Stars"
    if "2 stars" in blob:
        return "2 Stars"
    if "1 star" in blob:
        return "1 Star"
    if "bib gourmand" in blob:
        return "Bib Gourmand"
    if "selected" in blob:
        return "Selected"

    # 2) If numeric star field was found, trust it
    if star_num == 3:
        return "3 Stars"
    if star_num == 2:
        return "2 Stars"
    if star_num == 1:
        return "1 Star"

    # 3) Broad string matching fallback
    if re.search(r"\b3\s*michelin\s*star", blob) or "three star" in blob or "three_star" in blob or "3-star" in blob:
        return "3 Stars"
    if re.search(r"\b2\s*michelin\s*star", blob) or "two star" in blob or "two_star" in blob or "2-star" in blob:
        return "2 Stars"
    if re.search(r"\b1\s*michelin\s*star", blob) or re.search(r"\b1\s*star\b", blob) or "one star" in blob or "one_star" in blob or "1-star" in blob:
        return "1 Star"
    if "bib" in blob and "gourmand" in blob:
        return "Bib Gourmand"
    if "michelin selected" in blob or re.search(r"\bselected\b", blob):
        return "Selected"

    return None

# --- rewritten method ---

def michelin_parse_detail(s: requests.Session, url: str, sleep_s: float, captured_at: str) -> Place:
    html = get_html(s, url, sleep_s=sleep_s)
    if html is None:
        raise RuntimeError(f"Failed to fetch Michelin detail page: {url}")

    soup = BeautifulSoup(html, "lxml")

    # --- Name early (helps debug readability) ---
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else url.rstrip("/").split("/")[-1]

# --- TEMP DEBUG (safe) ---
#    sc = soup.find("script", {"type": "application/ld+json"})
#    if sc and sc.string:
#        snippet = sc.string.strip().replace("\n", " ")
#        print("[michelin ld snippet]", snippet[:600])
#    else:
#        print("[michelin ld snippet] (missing)")
#
#    print("[michelin debug] contains 'hasMap':", "hasMap" in html)
#    print("[michelin debug] contains 'geo':", '"geo"' in html or "'geo'" in html)
#    print("[michelin debug] contains 'latitude':", "latitude" in html)
#    print("[michelin debug] contains 'longitude':", "longitude" in html)
#    print("[michelin debug] contains 'map':", "map" in html.lower())
#
#    has_ld = soup.find("script", {"type": "application/ld+json"}) is not None
#    has_next = soup.find("script", id="__NEXT_DATA__") is not None
#    title = soup.title.get_text(strip=True) if soup.title else "(no title)"
#    print(f"[michelin detail debug] url={url} | title={title!r} | ld+json={has_ld} | __NEXT_DATA__={has_next} | html_len={len(html)}")
#
    # --- Text heuristics ---
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

    # Category (award) — prefer Next.js payload when available, then fall back to text heuristics.
    category = michelin_category_from_next_data(soup)
    # If Michelin doesn't explicitly say stars/bib in the Next.js blob,
    # it's very often "Michelin Selected". Avoid showing "(none)".
    if category is None:
        category = "Selected"

    if not category:
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

    # --- ✅ Geo + optional address from page (ONLY ONCE) ---
    lat, lon, addr_ld = michelin_extract_geo_address_from_page(soup, html)

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
#    def _get_header(it: dict) -> dict:
#        h = it.get("headerInfo") or {}
#        return h if isinstance(h, dict) else {}
#
#    year_counts: dict[str, int] = {}
#    ribbon_counts: dict[str, int] = {}
#    missing_header = 0
#
#    for it in all_items:
#        h = _get_header(it)
#        if not h:
#            missing_header += 1
#            continue
#        y = str(h.get("bookYear") or "").strip() or "(missing)"
#        r = str(h.get("ribbonType") or "").strip() or "(missing)"
#        year_counts[y] = year_counts.get(y, 0) + 1
#        ribbon_counts[r] = ribbon_counts.get(r, 0) + 1
#
#    top_years = sorted(year_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
#    top_ribbons = sorted(ribbon_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
#
#    print(f"[bluer] total items collected: {len(all_items)} (missing headerInfo: {missing_header})")
#    print(f"[bluer] top bookYear counts: {top_years}")
#    print(f"[bluer] top ribbonType counts: {top_ribbons}")
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
        print(f"[bluer] zone1={z} filtered winners: {len(zone_filtered)}/{len(items)}")
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
            "kakao_id": p.kakao_id,
            "kakao_url": p.kakao_url,
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
        # --- sanity check ---
        from collections import Counter
        print("[michelin] category counts:", Counter([p.category or "(none)" for p in michelin_places]).most_common(20))

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
    combined = enrich_with_kakao(combined, sleep_s=0.15)
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
