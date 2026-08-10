"""
repair_guide_tiers.py  —  Overhaul Phase 1.1

Repairs the two raw guide inputs that `build_map_list.py build` reads.

WHY THIS EXISTS
---------------
The build reads `data/raw/michelin.csv` and `data/raw/blueribbon_enriched.csv`.
Both were captured 2026-02-17 and both lost tier information:

  * data/raw/michelin.csv           — every starred restaurant collapsed to "Selected"
                                      (0 stars), the `cuisine` column holds a copy of
                                      the address, `price` is empty, and 4 Washington DC
                                      restaurants leaked in from a bad scrape.
  * data/raw/blueribbon_enriched.csv — no RIBBON_THREE, 282 (not 292) RIBBON_TWO,
                                      `cuisine` entirely empty.

A later, cleaner scrape exists in `out/` with the correct tiers:

  * out/michelin_seoul_raw.csv       — 27x "1 Star", 8x "2 Stars", 1x "3 Stars",
                                      real cuisine, real price.
  * out/blueribbon_seoul_raw.csv     — 2x RIBBON_THREE, 292x RIBBON_TWO, real cuisine.

We PATCH the existing files rather than swapping to the `out/` ones, because the Kakao
ledger (data/cache/kakao_ledger.json) is keyed on `slugify(name)__slugify(address)` of
the existing files: they hit 100%, the `out/` files hit ~0%. Swapping would discard every
resolved kakao_id and force ~1,150 fresh Kakao API calls.

So: keep the base rows and their descriptions, overwrite only the fields that are wrong.

Joins name-first (exact after normalisation), then coordinate, because coordinate-only
matching at 4dp produced at least one confirmed mis-pairing.

Idempotent. Writes .bak backups on first run. Run from the project root.
"""

from __future__ import annotations

import csv
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent

MICHELIN_BASE = ROOT / "data" / "raw" / "michelin.csv"
MICHELIN_GOOD = ROOT / "out" / "michelin_seoul_raw.csv"
BLUER_BASE = ROOT / "data" / "raw" / "blueribbon_enriched.csv"
BLUER_GOOD = ROOT / "out" / "blueribbon_seoul_raw.csv"

# A Seoul address either contains Hangul or a romanised Korean road/district suffix.
SEOUL_ADDRESS = re.compile(r"[가-힣]|-(gil|ro|daero|gu|dong)\b", re.IGNORECASE)


def norm_name(s: str | None) -> str:
    """Normalise a restaurant name for joining: strip spaces, punctuation, case."""
    return re.sub(r"[\s\-_.,'’\"()]", "", (s or "").strip()).lower()


def coord_key(row: dict, dp: int) -> tuple[float, float] | None:
    try:
        return (round(float(row["latitude"]), dp), round(float(row["longitude"]), dp))
    except (TypeError, ValueError, KeyError):
        return None


def read_csv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]):
    backup = path.with_suffix(path.suffix + ".bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
        print(f"   [backup] {path.name} -> {backup.name}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"   [write]  {len(rows)} rows -> {path}")


def build_index(rows: list[dict]) -> tuple[dict, dict, dict]:
    """Index donor rows by normalised name and by coordinate. Ambiguous keys are dropped."""
    by_name: dict[str, dict] = {}
    name_counts = Counter(norm_name(r["name"]) for r in rows)
    for r in rows:
        k = norm_name(r["name"])
        if k and name_counts[k] == 1:
            by_name[k] = r

    def coord_index(dp: int) -> dict:
        idx: dict = {}
        counts = Counter(coord_key(r, dp) for r in rows if coord_key(r, dp))
        for r in rows:
            k = coord_key(r, dp)
            if k and counts[k] == 1:
                idx[k] = r
        return idx

    return by_name, coord_index(4), coord_index(3)


def match(row: dict, by_name: dict, by_c4: dict, by_c3: dict) -> tuple[dict | None, str]:
    """Name first (most reliable), then tightening coordinate matches."""
    k = norm_name(row.get("name"))
    if k and k in by_name:
        return by_name[k], "name"
    for idx, label in ((by_c4, "coord4"), (by_c3, "coord3")):
        c = coord_key(row, 4 if label == "coord4" else 3)
        if c and c in idx:
            return idx[c], label
    return None, "miss"


def clean_cuisine(value: str | None) -> str:
    """out/ cuisine looks like 'Samgyetang\\nEat like a local\\n' — keep the first line."""
    if not value:
        return ""
    return value.split("\n")[0].strip()


def repair_michelin() -> bool:
    print("\n" + "=" * 70)
    print("MICHELIN")
    print("=" * 70)
    base, good = read_csv(MICHELIN_BASE), read_csv(MICHELIN_GOOD)
    print(f"   base {MICHELIN_BASE.name}: {len(base)} rows")
    print(f"   good {MICHELIN_GOOD.name}: {len(good)} rows")

    by_name, by_c4, by_c3 = build_index(good)

    # 1. Drop rows whose address is not in Korea (the Washington DC scrape leak).
    kept, dropped = [], []
    for r in base:
        (dropped if not SEOUL_ADDRESS.search(r.get("address") or "") else kept).append(r)
    for r in dropped:
        print(f"   [drop]   {r['name']} — {r.get('address', '')[:44]}")

    # 2. Patch category / cuisine / price from the good scrape.
    tier_changes, how, misses = Counter(), Counter(), []
    cuisine_fixed = price_fixed = 0

    for r in kept:
        donor, method = match(r, by_name, by_c4, by_c3)
        how[method] += 1
        if not donor:
            misses.append(r["name"])
            continue

        new_cat = (donor.get("category") or "").strip()
        if new_cat and new_cat != r.get("category"):
            tier_changes[(r.get("category"), new_cat)] += 1
            r["category"] = new_cat

        # `cuisine` currently holds a duplicate of the address — always replace it.
        new_cuisine = clean_cuisine(donor.get("cuisine"))
        if new_cuisine and new_cuisine != r.get("cuisine"):
            r["cuisine"] = new_cuisine
            cuisine_fixed += 1

        new_price = (donor.get("price") or "").strip()
        if new_price and not (r.get("price") or "").strip():
            r["price"] = new_price
            price_fixed += 1

    print(f"\n   joined: {dict(how)}")
    if misses:
        print(f"   unmatched ({len(misses)}): {misses[:8]}")
    print(f"   cuisine repaired: {cuisine_fixed}   price filled: {price_fixed}")
    print(f"   tier changes:")
    for (old, new), n in sorted(tier_changes.items(), key=lambda kv: -kv[1]):
        print(f"      {old or '(blank)':>14s} -> {new:<14s} {n}")

    final = Counter(r["category"] for r in kept)
    print(f"   final tiers: {dict(final)}")

    write_csv(MICHELIN_BASE, kept, list(base[0].keys()))
    return True


def repair_blueribbon() -> bool:
    print("\n" + "=" * 70)
    print("BLUE RIBBON")
    print("=" * 70)
    base, good = read_csv(BLUER_BASE), read_csv(BLUER_GOOD)
    print(f"   base {BLUER_BASE.name}: {len(base)} rows")
    print(f"   good {BLUER_GOOD.name}: {len(good)} rows")

    by_name, by_c4, by_c3 = build_index(good)

    tier_changes, how, misses = Counter(), Counter(), []
    cuisine_added = 0
    matched_donor_names: set[str] = set()

    for r in base:
        donor, method = match(r, by_name, by_c4, by_c3)
        how[method] += 1
        if not donor:
            misses.append(r["name"])
            continue
        matched_donor_names.add(norm_name(donor["name"]))

        new_cat = (donor.get("category") or "").strip()
        if new_cat and new_cat != r.get("category"):
            tier_changes[(r.get("category"), new_cat)] += 1
            r["category"] = new_cat

        new_cuisine = clean_cuisine(donor.get("cuisine"))
        if new_cuisine and not (r.get("cuisine") or "").strip():
            r["cuisine"] = new_cuisine
            cuisine_added += 1

    print(f"\n   joined: {dict(how)}")
    if misses:
        print(f"   unmatched ({len(misses)}): {misses[:8]}")
    print(f"   cuisine added: {cuisine_added}")
    print(f"   tier changes:")
    for (old, new), n in sorted(tier_changes.items(), key=lambda kv: -kv[1]):
        print(f"      {old or '(blank)':>14s} -> {new:<14s} {n}")

    # Any RIBBON_THREE in the good scrape that never got matched is missing entirely
    # from the base file — add it so the top tier is complete.
    fields = list(base[0].keys())
    added = []
    for d in good:
        if d.get("category") != "RIBBON_THREE":
            continue
        if norm_name(d["name"]) in matched_donor_names:
            continue
        row = {k: "" for k in fields}
        for k in fields:
            if k in d:
                row[k] = d[k]
        row["cuisine"] = clean_cuisine(d.get("cuisine"))
        row["source"] = "blueribbon"
        row["name_ko"] = d["name"]
        base.append(row)
        added.append(d["name"])
    if added:
        print(f"   added missing RIBBON_THREE rows: {added}")

    final = Counter(r["category"] for r in base)
    print(f"   final tiers: {dict(final)}")

    write_csv(BLUER_BASE, base, fields)
    return True


def main():
    for p in (MICHELIN_BASE, MICHELIN_GOOD, BLUER_BASE, BLUER_GOOD):
        if not p.exists():
            print(f"FATAL: missing required input {p}")
            sys.exit(1)

    repair_michelin()
    repair_blueribbon()

    print("\n" + "=" * 70)
    print("Done. Next: run `python build_map_list.py build` to regenerate places.geojson.")
    print("=" * 70)


if __name__ == "__main__":
    main()
