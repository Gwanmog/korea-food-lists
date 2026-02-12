from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin
import requests


BASE = "https://bluer.co.kr"
API = f"{BASE}/api/v1"


def make_session() -> requests.Session:
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
            "Referer": f"{BASE}/search",
            "Origin": BASE,
        }
    )
    return s
def ribbon_score(item: Dict[str, Any]) -> int:
    """
    Try to infer ribbon count from list endpoint item.
    Returns 0 if unknown.
    """
    hi = item.get("headerInfo") or {}
    candidates = [
        hi.get("ribbonCount"),
        hi.get("ribbonCnt"),
        hi.get("ribbon"),
        item.get("ribbonCount"),
        item.get("ribbonCnt"),
        item.get("ribbon"),
        item.get("ribbonType"),
        hi.get("ribbonType"),
    ]
    for v in candidates:
        if v is None:
            continue
        # Sometimes ribbonType might be "ONE"/"TWO"/"THREE" or 1/2/3
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            vv = v.strip().upper()
            if vv.isdigit():
                return int(vv)
            if vv in {"ONE", "RIBBON_ONE"}:
                return 1
            if vv in {"TWO", "RIBBON_TWO"}:
                return 2
            if vv in {"THREE", "RIBBON_THREE"}:
                return 3
    return 0

def filter_ribboned(items: List[Dict[str, Any]], min_ribbons: int = 1) -> List[Dict[str, Any]]:
    keep = [it for it in items if ribbon_score(it) >= min_ribbons]
    return keep

def pretty_dump(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def normalize_next_url(next_href: str) -> str:
    """
    Normalize weird next links like 'http://bluer.co.kr:443/...'
    and dedupe repeated sort params.
    """
    if next_href.startswith("/"):
        raw = urljoin(BASE, next_href)
    else:
        p0 = urlparse(next_href)
        raw = urlunparse(("https", "bluer.co.kr", p0.path, p0.params, p0.query, p0.fragment))

    p = urlparse(raw)
    qs = parse_qsl(p.query, keep_blank_values=True)

    # Deduplicate sort params while preserving order
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


def get_restaurants_page(
    s: requests.Session,
    *,
    zone1: str,
    query: str = "",
    page: int = 1,
    size: int = 24,
    sleep_s: float = 0.25,
) -> Dict[str, Any]:
    params = {
        "query": query,
        "zone1": zone1,
        "page": page,
        "size": size,
        # present in site requests (safe blank defaults)
        "foodType": "",
        "foodTypeDetail": "",
    }
    url = f"{API}/restaurants?{urlencode(params)}"
    time.sleep(sleep_s)
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def hal_extract_items(doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    For HAL docs:
      {_embedded:{<collection>:[...]}, _links:{next:{href:...}}, page:{...}}
    Returns (items, next_href)
    """
    items: List[Dict[str, Any]] = []
    emb = doc.get("_embedded")
    if isinstance(emb, dict):
        # pick the first list of dicts
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


def collect_restaurants_hal(
    s: requests.Session,
    *,
    zone1: str,
    max_pages: int = 200,
    sleep_s: float = 0.8,
) -> List[Dict[str, Any]]:
    """
    Follow HAL _links.next until exhausted, with retry/backoff for 429.
    """
    first = get_restaurants_page(s, zone1=zone1, page=1, size=24, sleep_s=sleep_s)
    items, next_href = hal_extract_items(first)

    all_items = list(items)
    page_num = 1
    print(f"[restaurants] page {page_num} items={len(items)} next={bool(next_href)}")

    while next_href and page_num < max_pages:
        page_num += 1
        url = normalize_next_url(next_href)

        attempt = 0
        doc = None

        while attempt < 6:
            attempt += 1
            time.sleep(sleep_s)

            r = s.get(url, timeout=30)

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) else min(60.0, 2.0 ** attempt)
                print(
                    f"[restaurants] 429 rate-limited on page {page_num}. Waiting {wait:.1f}s then retrying (attempt {attempt})...")
                time.sleep(wait)
                continue

            r.raise_for_status()
            doc = r.json()
            break

        if doc is None:
            raise requests.HTTPError(f"Failed to fetch page after {attempt} attempts: {url}")

        items, next_href = hal_extract_items(doc)
        all_items.extend(items)
        print(f"[restaurants] page {page_num} items={len(items)} total={len(all_items)} next={bool(next_href)}")

        if not items:
            break

    return all_items

def is_ribboned_2024_2026(item: Dict[str, Any]) -> bool:
    if item.get("status") != "ACTIVE":
        return False

    header = item.get("headerInfo") or {}
    ribbon_type = (header.get("ribbonType") or "").strip().upper()
    book_year = (header.get("bookYear") or "").strip()

    if book_year not in {"2024", "2025", "2026"}:
        return False

    # Exclude 0-ribbon / missing
    if not ribbon_type.startswith("RIBBON_"):
        return False

    return True


def filter_blueribbon(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    filtered = [it for it in items if is_ribboned_2024_2026(it)]
    return filtered

def extract_best_id_field(items: List[Dict[str, Any]]) -> Optional[str]:
    """
    Guess the ID field by looking for a key that:
      - exists in many items
      - is scalar-ish (int/str)
    """
    if not items:
        return None

    # candidate keys to prioritize
    preferred = ["id", "restaurantId", "rid", "storeId", "placeId", "uuid", "seq", "restaurantSeq"]
    keys = set()
    for it in items[:10]:
        keys.update(it.keys())

    # Try preferred first
    for k in preferred:
        if k in keys:
            # ensure it looks like a real id in most items
            good = 0
            for it in items[:30]:
                v = it.get(k)
                if isinstance(v, (int, str)) and str(v).strip():
                    good += 1
            if good >= max(3, min(10, len(items[:30]) // 2)):
                return k

    # Otherwise, pick any key named like "*id*" that behaves well
    id_like = [k for k in keys if "id" in k.lower() or k.lower().endswith("seq")]
    for k in sorted(id_like):
        good = 0
        for it in items[:30]:
            v = it.get(k)
            if isinstance(v, (int, str)) and str(v).strip():
                good += 1
        if good >= max(3, min(10, len(items[:30]) // 2)):
            return k

    return None


def extract_candidate_ids(items: List[Dict[str, Any]]) -> List[str]:
    field = extract_best_id_field(items)
    if not field:
        print("[restaurants] Could not infer ID field. First item keys:", list(items[0].keys()) if items else None)
        return []
    ids: List[str] = []
    for it in items:
        v = it.get(field)
        if isinstance(v, (int, str)) and str(v).strip():
            ids.append(str(v).strip())
    print(f"[restaurants] using id field '{field}', extracted ids={len(ids)}")
    return ids


def try_search_details(
    s: requests.Session,
    *,
    candidate_ids: List[str],
    sleep_s: float = 0.25,
) -> Optional[Dict[str, Any]]:
    url = f"{API}/searchDetails"

    # Keep the batch modest; many APIs have size limits.
    batch = candidate_ids[:20]

    payloads = [
        {"ids": batch},
        {"idList": batch},
        {"restaurantIds": batch},
        {"restaurantIdList": batch},
        {"items": batch},
        {"data": batch},
        {"ids": [int(x) for x in batch if x.isdigit()]},
        {"restaurantIds": [int(x) for x in batch if x.isdigit()]},
        {"items": [{"id": x} for x in batch]},
        {"restaurants": [{"id": x} for x in batch]},
    ]

    for idx, payload in enumerate(payloads, start=1):
        time.sleep(sleep_s)
        r = s.post(url, json=payload, timeout=30)
        if r.status_code == 200:
            try:
                j = r.json()
            except Exception:
                print(f"[searchDetails] variant {idx} got 200 but JSON parse failed.")
                continue
            print(f"[searchDetails] SUCCESS with variant {idx}: keys={list(payload.keys())}")
            return {"payload_used": payload, "response": j}

        print(f"[searchDetails] variant {idx} failed: status={r.status_code}")

    return None


def main() -> None:
    outdir = Path("out_bluer_probe")
    outdir.mkdir(exist_ok=True)

    s = make_session()
    s.get(f"{BASE}/search", timeout=30)

    for zone1 in ("서울 강북", "서울 강남"):
        print(f"\n=== PROBING zone1={zone1} ===")
        items = collect_restaurants_hal(s, zone1=zone1)
        before = len(items)
        items = filter_blueribbon(items)
        print(f"[restaurants] filtered ribboned 2024–2026: {len(items)}/{before}")
        # Save a sample of the first few items so we can see fields without huge files
        pretty_dump({"sample_items": items[:5], "sample_count": len(items)}, outdir / f"restaurants_{zone1.replace(' ', '_')}_SAMPLE.json")

if __name__ == "__main__":
    main()
