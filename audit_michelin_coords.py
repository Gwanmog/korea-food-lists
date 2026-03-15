"""
audit_michelin_coords.py
Audits Michelin entries in site/places.geojson by verifying their coordinates
against the Kakao Local API. Flags any entry where the discrepancy exceeds 300m.

Output: out/michelin_coord_audit.csv
"""

import json
import math
import os
import csv
import time
import sys
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
if not KAKAO_REST_API_KEY:
    raise EnvironmentError("KAKAO_REST_API_KEY not set in environment / .env")

GEOJSON_PATH = "site/places.geojson"
OUTPUT_PATH = "out/michelin_coord_audit.csv"
DISTANCE_THRESHOLD_M = 300


def haversine_m(lat1, lon1, lat2, lon2):
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def kakao_search_by_id(name_ko, lat, lon, target_kakao_id):
    """
    Searches Kakao by keyword and returns coords for the result whose place ID
    matches target_kakao_id. Fetches up to 3 pages (45 results) to find it.
    Returns (kakao_lat, kakao_lon, kakao_place_url, matched) where matched=True
    if the stored ID was found, False if we fell back to the first result.
    """
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}

    first_doc = None
    for page in range(1, 4):
        params = {
            "query": name_ko,
            "x": str(lon),
            "y": str(lat),
            "radius": 20000,
            "size": 15,
            "page": page,
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            documents = resp.json().get("documents", [])
        except Exception as e:
            print(f"  ⚠️  Kakao API error for '{name_ko}': {e}")
            return None, None, None, False

        if not documents:
            break

        if first_doc is None and documents:
            first_doc = documents[0]

        for doc in documents:
            place_url = doc.get("place_url", "")
            # Kakao place URLs end with the numeric ID
            if place_url.rstrip("/").split("/")[-1] == str(target_kakao_id):
                return float(doc["y"]), float(doc["x"]), place_url, True

        if resp.json().get("meta", {}).get("is_end", True):
            break
        time.sleep(0.1)

    # ID not found in search results — return first result as fallback
    if first_doc:
        return float(first_doc["y"]), float(first_doc["x"]), first_doc.get("place_url", ""), False
    return None, None, None, False


def kakao_keyword_search(name_ko, lat, lon):
    """
    For entries without a kakao_id: return the first keyword search result.
    Returns (kakao_lat, kakao_lon, kakao_place_url) or (None, None, None).
    """
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}
    params = {
        "query": name_ko,
        "x": str(lon),
        "y": str(lat),
        "radius": 20000,
        "size": 1,
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        documents = resp.json().get("documents", [])
        if not documents:
            return None, None, None
        doc = documents[0]
        return float(doc["y"]), float(doc["x"]), doc.get("place_url", "")
    except Exception as e:
        print(f"  ⚠️  Kakao API error for '{name_ko}': {e}")
        return None, None, None


def main():
    with open(GEOJSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    michelin_features = [
        feat for feat in data["features"]
        if feat["properties"].get("source") == "michelin"
    ]

    print(f"Found {len(michelin_features)} Michelin entries. Auditing coordinates...")

    os.makedirs("out", exist_ok=True)
    rows = []

    for i, feat in enumerate(michelin_features, 1):
        props = feat["properties"]
        name = props.get("name", "")
        name_ko = props.get("name_ko") or name
        kakao_id = props.get("kakao_id")
        coords = feat["geometry"]["coordinates"]
        current_lon, current_lat = float(coords[0]), float(coords[1])

        print(f"[{i}/{len(michelin_features)}] {name_ko} (kakao_id={kakao_id})")

        if not kakao_id:
            # No stored ID — use keyword search first result as candidate
            kakao_lat, kakao_lon, kakao_url = kakao_keyword_search(name_ko, current_lat, current_lon)
            id_matched = None  # not applicable
            if kakao_lat is None:
                print("  ⚠️  No kakao_id and not found in search.")
                rows.append({
                    "name": name, "name_ko": name_ko,
                    "current_lat": current_lat, "current_lon": current_lon,
                    "kakao_lat": "", "kakao_lon": "", "distance_m": "",
                    "kakao_url": "", "flagged": "NO_KAKAO_ID_NOT_FOUND",
                })
                continue
        else:
            kakao_lat, kakao_lon, kakao_url, id_matched = kakao_search_by_id(
                name_ko, current_lat, current_lon, kakao_id
            )
            if kakao_lat is None:
                rows.append({
                    "name": name, "name_ko": name_ko,
                    "current_lat": current_lat, "current_lon": current_lon,
                    "kakao_lat": "", "kakao_lon": "", "distance_m": "",
                    "kakao_url": "", "flagged": "NOT_FOUND",
                })
                time.sleep(0.2)
                continue
            if not id_matched:
                # Fallback result — don't flag, just record as unverified
                dist_m = haversine_m(current_lat, current_lon, kakao_lat, kakao_lon)
                rows.append({
                    "name": name, "name_ko": name_ko,
                    "current_lat": current_lat, "current_lon": current_lon,
                    "kakao_lat": kakao_lat, "kakao_lon": kakao_lon,
                    "distance_m": round(dist_m, 1),
                    "kakao_url": kakao_url, "flagged": "ID_NOT_IN_RESULTS",
                })
                time.sleep(0.15)
                continue

        dist_m = haversine_m(current_lat, current_lon, kakao_lat, kakao_lon)
        flagged = "YES" if dist_m > DISTANCE_THRESHOLD_M else "no"

        if flagged == "YES":
            print(f"  🚨 FLAGGED — discrepancy {dist_m:.0f}m")

        rows.append({
            "name": name,
            "name_ko": name_ko,
            "current_lat": current_lat,
            "current_lon": current_lon,
            "kakao_lat": kakao_lat,
            "kakao_lon": kakao_lon,
            "distance_m": round(dist_m, 1),
            "kakao_url": kakao_url,
            "flagged": flagged,
        })

        time.sleep(0.15)  # be polite to the API

    # Sort: flagged YES first, then by distance desc
    rows.sort(key=lambda r: (0 if r["flagged"] == "YES" else 1, -(float(r["distance_m"]) if r["distance_m"] != "" else 0)))

    fieldnames = ["name", "name_ko", "current_lat", "current_lon", "kakao_lat", "kakao_lon", "distance_m", "kakao_url", "flagged"]
    with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    flagged_count = sum(1 for r in rows if r["flagged"] == "YES")
    print(f"\n✅ Audit complete. {flagged_count} entries flagged (>{DISTANCE_THRESHOLD_M}m discrepancy).")
    print(f"   Results saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
