"""
test_data_integrity.py  —  Overhaul Phase 7

A standing check on the shipped artefacts: site/places.geojson, the FAISS index, and the
raw guide captures. Every assertion here exists because the corresponding bug actually
shipped — this is a regression net for the specific failures found during the overhaul,
not a generic smoke test.

Self-contained on purpose: the project has no pytest and adding a test dependency to run
twenty assertions isn't worth it. Same shape as scrape_validation.py.

    python test_data_integrity.py          # run everything
    python test_data_integrity.py -v       # include per-test detail

Exit code 0 = all pass, 1 = at least one failure.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
GEOJSON = ROOT / "site" / "places.geojson"
INDEX = ROOT / "data" / "restaurant_vectors.index"
APP_JS = ROOT / "site" / "js" / "app.js"
INDEX_HTML = ROOT / "site" / "index.html"
NEON_CSV = ROOT / "neon_guide_audited_final.csv"

HANGUL = re.compile(r"[가-힣]")
SEOUL_ADDRESS = re.compile(r"[가-힣]|-(gil|ro|daero|gu|dong)\b", re.IGNORECASE)

# Seoul bounding box, deliberately generous.
LAT_MIN, LAT_MAX = 37.3, 37.75
LON_MIN, LON_MAX = 126.7, 127.25

KNOWN_TIERS = {
    "3 Stars", "2 Stars", "1 Star", "Bib Gourmand", "Selected",
    "RIBBON_THREE", "RIBBON_TWO", "RIBBON_ONE",
    "3 Neon Hearts", "2 Neon Hearts", "1 Neon Heart", "Neon Vetted",
}
NEON_TIER_CODES = {"NEON_3", "NEON_2", "NEON_1", "NEON_VETTED"}

_tests = []
_verbose = "-v" in sys.argv


def test(name):
    def deco(fn):
        _tests.append((name, fn))
        return fn
    return deco


class Fail(AssertionError):
    pass


def check(cond, msg):
    if not cond:
        raise Fail(msg)


def note(msg):
    if _verbose:
        print(f"        {msg}")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def load_features():
    with open(GEOJSON, "r", encoding="utf-8") as f:
        return json.load(f)["features"]


FEATURES = load_features()
PROPS = [f["properties"] for f in FEATURES]


def of_source(src):
    return [p for p in PROPS if src in (p.get("source") or "")]


def nonempty(p, key):
    return bool((p.get(key) or "").strip())


def pct(n, d):
    return 0 if d == 0 else 100 * n / d


# --------------------------------------------------------------------------
# Structural
# --------------------------------------------------------------------------
@test("every pin sits inside the Seoul bounding box")
def t_bbox():
    bad = [f["properties"]["name"] for f in FEATURES
           if not (LAT_MIN <= f["geometry"]["coordinates"][1] <= LAT_MAX
                   and LON_MIN <= f["geometry"]["coordinates"][0] <= LON_MAX)]
    check(not bad, f"{len(bad)} pins outside Seoul: {bad[:5]}")
    note(f"{len(FEATURES)} pins all within bounds")


@test("no duplicate kakao_id (the 104-duplicate-pin bug)")
def t_dupes():
    ids = [str(p.get("kakao_id")) for p in PROPS
           if p.get("kakao_id") and str(p.get("kakao_id")).lower() not in ("none", "nan")]
    dupes = {k: v for k, v in Counter(ids).items() if v > 1}
    check(not dupes, f"{len(dupes)} kakao_ids appear on more than one pin: {list(dupes)[:5]}")
    note(f"{len(ids)} pins carry a kakao_id, all unique")


@test("vector_ids are present and contiguous")
def t_vector_ids():
    vids = [p.get("vector_id") for p in PROPS]
    missing = sum(1 for v in vids if v is None)
    check(missing == 0, f"{missing} features have no vector_id")
    check(sorted(vids) == list(range(len(vids))),
          "vector_ids are not a contiguous 0..N-1 range — the map/index join is positional")
    note(f"vector_id 0..{len(vids) - 1}")


@test("FAISS index matches the map exactly")
def t_index_sync():
    try:
        import faiss
    except ImportError:
        note("faiss not installed — skipped")
        return
    check(INDEX.exists(), "FAISS index file is missing")
    idx = faiss.read_index(str(INDEX))
    check(idx.ntotal == len(FEATURES),
          f"index has {idx.ntotal} vectors but the map has {len(FEATURES)} features — "
          f"they must always ship together")
    note(f"{idx.ntotal} vectors, dim {idx.d}")


# --------------------------------------------------------------------------
# Guide integrity (Phase 1.1)
# --------------------------------------------------------------------------
@test("Michelin stars are on the map (the six-month regression)")
def t_stars():
    mich = of_source("michelin")
    cats = Counter(p.get("category") for p in mich)
    awards = Counter()
    for p in mich:
        for a in (p.get("awards") or []):
            if a.get("guide") == "michelin":
                awards[a.get("tier")] += 1
    combined = {t: max(cats.get(t, 0), awards.get(t, 0)) for t in ("3 Stars", "2 Stars", "1 Star")}
    check(combined["3 Stars"] >= 1, "no 3-star restaurant — this is what silently broke before")
    check(combined["2 Stars"] >= 5, f"only {combined['2 Stars']} two-star restaurants")
    check(combined["1 Star"] >= 20, f"only {combined['1 Star']} one-star restaurants")
    note(f"3★={combined['3 Stars']} 2★={combined['2 Stars']} 1★={combined['1 Star']}")


@test("Blue Ribbon's top tier is present")
def t_ribbon_three():
    n = sum(1 for p in PROPS
            if p.get("category") == "RIBBON_THREE"
            or any(a.get("tier") == "RIBBON_THREE" for a in (p.get("awards") or [])))
    check(n >= 1, "no RIBBON_THREE restaurants")
    note(f"{n} RIBBON_THREE")


@test("no non-Seoul restaurants leaked in (the Washington DC bug)")
def t_geography():
    bad = [p["name"] for p in PROPS
           if nonempty(p, "address") and not SEOUL_ADDRESS.search(p["address"])
           and not SEOUL_ADDRESS.search(p.get("address_ko") or "")]
    check(not bad, f"{len(bad)} non-Korean addresses: {bad[:5]}")


@test("cuisine is not an aliased copy of address (the 200-row bug)")
def t_no_aliasing():
    both = [p for p in PROPS if nonempty(p, "cuisine") and nonempty(p, "address")]
    def squash(s):
        return re.sub(r"\s+", "", s).lower()
    same = sum(1 for p in both if squash(p["cuisine"])[:12] == squash(p["address"])[:12])
    check(pct(same, len(both)) < 10, f"cuisine mirrors address in {same}/{len(both)} rows")


@test("every award tier is a known value")
def t_tier_vocabulary():
    seen = set()
    for p in PROPS:
        if nonempty(p, "category"):
            seen.add(p["category"].strip())
        for a in (p.get("awards") or []):
            if a.get("tier"):
                seen.add(a["tier"].strip())
    unknown = seen - KNOWN_TIERS
    check(not unknown, f"unrecognised tiers would be unfilterable on the map: {sorted(unknown)}")
    note(f"{len(seen)} distinct tiers, all known")


# --------------------------------------------------------------------------
# Merge (Phase 1.2)
# --------------------------------------------------------------------------
@test("restaurants recognised by several guides are merged onto one pin")
def t_merged():
    multi = [p for p in PROPS if " " in (p.get("source") or "")]
    check(len(multi) > 50, f"only {len(multi)} multi-guide pins — dedupe may have regressed")
    for p in multi:
        check(len(p.get("awards") or []) >= 2,
              f"{p['name']} claims multiple guides but carries {len(p.get('awards') or [])} awards")
    note(f"{len(multi)} pins hold more than one guide")


# --------------------------------------------------------------------------
# Trust (Phase 2.1)
# --------------------------------------------------------------------------
@test("no restaurant the receipt auditor rejected is live")
def t_auditor():
    if not NEON_CSV.exists():
        note("neon CSV missing — skipped")
        return
    import csv
    with open(NEON_CSV, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rejected = [r["Restaurant Name"].strip() for r in rows
                if (r.get("Rating Justified") or "").strip().lower() == "no"]
    check(not rejected,
          f"{len(rejected)} rejected restaurants are still in the guide: {rejected[:5]}")
    note(f"{len(rows)} restaurants in the guide, 0 rejected")


# --------------------------------------------------------------------------
# Tiers (Phase 2.3 / 2.4)
# --------------------------------------------------------------------------
@test("Neon tiers are scarce at the top and broad at the base")
def t_tier_distribution():
    neon = of_source("neon")
    c = Counter(p.get("tier") for p in neon)
    n = len(neon)
    check(pct(c["NEON_3"], n) <= 2.5,
          f"3 hearts are {pct(c['NEON_3'], n):.1f}% of the guide — grade inflation is back")
    check(pct(c["NEON_VETTED"], n) >= 70,
          f"only {pct(c['NEON_VETTED'], n):.1f}% sit at the base tier")
    note(f"3♥={c['NEON_3']} 2♥={c['NEON_2']} 1♥={c['NEON_1']} vetted={c['NEON_VETTED']}")


@test("every Neon pin carries a machine-readable tier code")
def t_tier_codes():
    neon = of_source("neon")
    bad = [p["name"] for p in neon if p.get("tier") not in NEON_TIER_CODES]
    check(not bad, f"{len(bad)} Neon pins have no usable tier: {bad[:5]}")


@test("tier boilerplate is not embedded in descriptions")
def t_no_boilerplate():
    bad = [p["name"] for p in PROPS
           if (p.get("description") or "").startswith(("✨", "🌟", "👍", "📌"))]
    check(not bad, f"{len(bad)} descriptions still open with a tier emoji: {bad[:5]}")


# --------------------------------------------------------------------------
# Bilingual (Phase 5.5)
# --------------------------------------------------------------------------
@test("bilingual field coverage")
def t_bilingual():
    n = len(PROPS)
    for field, floor in (("name_ko", 99), ("description_ko", 99), ("address_ko", 98)):
        got = pct(sum(1 for p in PROPS if nonempty(p, field)), n)
        check(got >= floor, f"{field} only {got:.1f}% covered (floor {floor}%)")
        note(f"{field}: {got:.1f}%")


@test("cuisine is English and cuisine_ko is Korean")
def t_cuisine_languages():
    withc = [p for p in PROPS if nonempty(p, "cuisine")]
    check(withc, "no cuisine values at all")
    en_bad = [p["cuisine"] for p in withc if HANGUL.search(p["cuisine"])]
    check(not en_bad, f"{len(en_bad)} `cuisine` values contain Hangul: {en_bad[:3]}")
    ko_missing = [p["name"] for p in withc if not nonempty(p, "cuisine_ko")]
    check(not ko_missing, f"{len(ko_missing)} pins have cuisine but no cuisine_ko")
    note(f"{len(withc)} pins with cuisine, all language-clean")


# --------------------------------------------------------------------------
# Frontend contract
# --------------------------------------------------------------------------
@test("every tier in the data has a filter pill (the missing 'Selected' bug)")
def t_frontend_filters():
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = re.search(r"const TIER_FILTER = \{(.*?)\n\};", app, re.S)
    check(block, "TIER_FILTER map not found in app.js")
    mapped = {m.group(1).strip().upper(): m.group(2)
              for m in re.finditer(r"['\"]?([A-Za-z0-9_ ]+)['\"]?\s*:\s*'(\w+)'", block.group(1))}
    pills = set(re.findall(r"id=\"f_(\w+)\"", html))

    # Mirror what passes() actually looks up, per guide (app.js guideVisible calls):
    #   michelin / blueribbon -> that guide's award tier, falling back to `category`
    #   neon                  -> the `tier` CODE, never `category`
    # Checking every category string instead would wrongly demand a pill for the
    # human-readable Neon labels ("1 Neon Heart"), which are display text and are never
    # used as a lookup key.
    def award_tier(p, guide):
        for a in (p.get("awards") or []):
            if a.get("guide") == guide and a.get("tier"):
                return a["tier"].strip().upper()
        return (p.get("category") or "").strip().upper()

    lookups = set()
    for p in PROPS:
        src = p.get("source") or ""
        if "michelin" in src:
            lookups.add(award_tier(p, "michelin"))
        if "blue" in src:
            lookups.add(award_tier(p, "blueribbon"))
        if "neon" in src:
            lookups.add((p.get("tier") or "").strip().upper())
    lookups.discard("")

    for t in sorted(lookups):
        check(t in mapped, f"tier {t!r} has no entry in TIER_FILTER — it would be unfilterable")
        check(mapped[t] in pills,
              f"tier {t!r} maps to pill '{mapped[t]}' which does not exist in index.html")
    note(f"{len(lookups)} tier values used for filtering, all mapped to real pills")


@test("raw guide captures still pass scrape validation")
def t_scrape_validation():
    try:
        from scrape_validation import validate_file
    except ImportError:
        note("scrape_validation not importable — skipped")
        return
    for source, name in (("michelin", "michelin.csv"),
                         ("blueribbon", "blueribbon_enriched.csv")):
        path = ROOT / "data" / "raw" / name
        if not path.exists():
            continue
        report = validate_file(source, path)
        check(report.ok, f"{name} fails validation: {report.errors[:2]}")
        note(f"{name}: pass")


# --------------------------------------------------------------------------
def main():
    print("=" * 72)
    print(f"DATA INTEGRITY  —  {len(FEATURES)} pins")
    print("=" * 72)
    failures = []
    for name, fn in _tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Fail as e:
            print(f"  FAIL  {name}")
            print(f"        {e}")
            failures.append(name)
        except Exception as e:
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failures.append(name)
    print("-" * 72)
    if failures:
        print(f"{len(failures)} of {len(_tests)} checks FAILED: {', '.join(failures)}")
        return 1
    print(f"all {len(_tests)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
