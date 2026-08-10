"""
build_cuisine_map.py  —  Overhaul Phase 5.5.4

Builds a persistent bilingual lookup for the `cuisine` label, so a Korean reader and an
English reader each see it in their own language.

THE PROBLEM
-----------
`cuisine` arrives in whatever language its source used:

    michelin     200 latin   ("Barbecue", "Contemporary")
    blueribbon   773 korean  ("한식(육류), 돼지갈비")
    neon        1228 korean  ("치킨", "제육볶음")

So the popup's 🍴 line is mixed-language for every user regardless of the toggle. The same
string is also embedded into the search vector as "카테고리: …", meaning a third of the index
has an English fragment sitting inside otherwise-Korean text.

WHY A CACHED MAP AND NOT PER-ROW TRANSLATION
--------------------------------------------
There are only ~437 distinct values across 2,267 restaurants — cuisine labels repeat heavily.
Translating distinct values once and caching them costs a handful of API calls instead of
thousands, and makes rebuilds free. Same reasoning as data/translation_cache.json.

Idempotent: only untranslated values are sent. Safe to re-run after new restaurants land.

    python build_cuisine_map.py            # translate anything new
    python build_cuisine_map.py --report   # show coverage, call nothing
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT / "soul-food-api" / ".env")

GEOJSON = ROOT / "site" / "places.geojson"
MAP_PATH = ROOT / "data" / "cuisine_map.json"
BATCH = 50

HANGUL = re.compile(r"[가-힣]")

PROMPT = """You translate short restaurant CUISINE LABELS between Korean and English.

These are category labels, not sentences — keep them short and natural, the way a menu or
restaurant guide would write them. Korean inputs often come from Kakao/Blue Ribbon category
paths like "한식(육류), 돼지갈비" (a broad category, then a specific one); render those as a
natural English label such as "Korean (Meat), Pork Ribs".

For every input, return BOTH an English form and a Korean form:
- If the input is Korean, keep it as "ko" and translate to "en".
- If the input is English, keep it as "en" and translate to "ko".
- Preserve romanised Korean dish names that English speakers use (Naengmyeon, Gomtang,
  Mandu, Bibimbap) rather than over-translating them.

Return ONLY a JSON object mapping each input string to {"en": ..., "ko": ...}.
Every input must appear exactly once as a key.

Inputs:
%s
"""


def load_map() -> dict:
    if MAP_PATH.exists():
        with open(MAP_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def expand_aliases(m: dict) -> int:
    """
    Make the map idempotent under re-application.

    After a build, `cuisine` holds the English form, so the next run would see "Chicken"
    as an unknown value and pay to translate it again — the map would grow on every
    build. Registering both the en and ko forms as keys pointing at the same pair means
    looking up either side resolves, and a second pass costs nothing.
    """
    added = 0
    for entry in list(m.values()):
        for form in (entry.get("en"), entry.get("ko")):
            key = (form or "").strip()
            if key and key not in m:
                m[key] = {"en": entry["en"], "ko": entry["ko"]}
                added += 1
    return added


def save_map(m: dict):
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2, sort_keys=True)


def distinct_cuisines() -> list[str]:
    """
    Gather cuisine values from the SOURCE files the build reads — not from the built
    geojson.

    Reading the output would leave this permanently one build behind: a new dish keyword
    would only become visible here after it had already shipped to the map untranslated,
    which is exactly the mixed-language bug this map exists to prevent. Reading the
    inputs instead means build_cuisine_map.py can run BEFORE build_map_list.py build and
    have the new value ready in time.
    """
    vals: set[str] = set()

    # Neon: the dish keyword lives in the CSV's "Category" column.
    neon = ROOT / "neon_guide_audited_final.csv"
    if neon.exists():
        with open(neon, "r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                vals.add((row.get("Category") or "").strip())

    # Michelin / Blue Ribbon raw captures.
    for name in ("michelin.csv", "blueribbon_enriched.csv", "blueribbon.csv"):
        p = ROOT / "data" / "raw" / name
        if p.exists():
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    vals.add((row.get("cuisine") or "").strip())

    # Whatever is already on the map, so nothing already live gets dropped.
    if GEOJSON.exists():
        with open(GEOJSON, "r", encoding="utf-8") as f:
            for x in json.load(f)["features"]:
                vals.add((x["properties"].get("cuisine") or "").strip())
                vals.add((x["properties"].get("cuisine_ko") or "").strip())

    return sorted(v for v in vals if v)


def translate_batch(client, values: list[str]) -> dict:
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=PROMPT % json.dumps(values, ensure_ascii=False, indent=1),
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           temperature=0.1),
    )
    data = json.loads(resp.text)
    out = {}
    for v in values:
        entry = data.get(v)
        if isinstance(entry, dict) and entry.get("en") and entry.get("ko"):
            out[v] = {"en": str(entry["en"]).strip(), "ko": str(entry["ko"]).strip()}
    return out


def main():
    cuisine_map = load_map()
    # Resolve both language forms of anything already known before deciding what's
    # genuinely new — otherwise a rebuilt geojson looks like 437 brand-new values.
    aliased = expand_aliases(cuisine_map)
    if aliased:
        print(f"(registered {aliased} alias keys for already-translated values)")
        save_map(cuisine_map)

    values = distinct_cuisines()
    missing = [v for v in values if v not in cuisine_map]

    print(f"distinct cuisine values : {len(values)}")
    print(f"already mapped          : {len(values) - len(missing)}")
    print(f"needing translation     : {len(missing)}")

    if "--report" in sys.argv:
        return
    if not missing:
        print("nothing to do.")
        return

    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    added = 0
    for i in range(0, len(missing), BATCH):
        batch = missing[i:i + BATCH]
        label = f"[{i // BATCH + 1}/{(len(missing) + BATCH - 1) // BATCH}]"
        for attempt in range(3):
            try:
                got = translate_batch(client, batch)
                cuisine_map.update(got)
                added += len(got)
                print(f"  {label} translated {len(got)}/{len(batch)}")
                break
            except genai_errors.ServerError:
                wait = 10 * (attempt + 1)
                print(f"  {label} 503 — retrying in {wait}s")
                time.sleep(wait)
            except Exception as e:
                print(f"  {label} failed: {e}")
                break
        save_map(cuisine_map)  # checkpoint after every batch

    # Anything the model skipped falls back to itself in both languages, so a missing
    # translation degrades to today's behaviour rather than an empty label.
    for v in values:
        if v not in cuisine_map:
            is_ko = bool(HANGUL.search(v))
            cuisine_map[v] = {"en": v, "ko": v}
            print(f"  [fallback] {v!r} left untranslated ({'ko' if is_ko else 'en'})")
    expand_aliases(cuisine_map)
    save_map(cuisine_map)

    print(f"\nwrote {len(cuisine_map)} entries to {MAP_PATH} (+{added} new)")


if __name__ == "__main__":
    main()
