"""
scrape_validation.py  —  Overhaul Phase 1.5

Guards the guide scrapers so a degraded scrape can never silently replace good data.

WHY THIS EXISTS
---------------
The Michelin stars weren't lost to a crash. A scrape produced degraded output — every
starred restaurant collapsed to "Selected" — `save_raw()` overwrote `data/raw/michelin.csv`
without checking, and the map shipped with zero stars for roughly six months. The same run
also let 4 Washington DC restaurants through and aliased `cuisine` onto `address`.

Every one of those failures is mechanically detectable. A Seoul Michelin scrape with no
three-star restaurant is not a valid scrape, it is a broken parser, and the correct
response is to abort and keep yesterday's data rather than publish the damage.

THE MODEL: validate, then promote
---------------------------------
    scrape -> validate -> write dated capture -> promote to the canonical filename

Nothing overwrites the file the build reads until the data has passed. Captures are kept
per-date so a regression can be diffed and rolled back.

The diff report produced here is an INTERNAL pre-promotion check only — it is not a
user-facing feature and is deliberately not surfaced in the UI.

USAGE
-----
    # Validate a file without writing anything
    python scrape_validation.py michelin data/raw/michelin.csv

    # From code (this is what build_map_list.py fetch does)
    report = validate_scrape(rows, "michelin", previous_path=Path("data/raw/michelin.csv"))
    if report.ok:
        promote_capture(rows, "michelin", DIR_RAW, fieldnames)
"""

from __future__ import annotations

import csv
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


# ==========================================================================
# CANONICAL TIER VOCABULARY
# ==========================================================================
# Single source of truth, shared with merge_duplicate_places() and the frontend
# filters. A scraped tier outside this set is a FAILURE, not a warning: an
# unrecognised value silently lands on the map as an unfilterable category
# (exactly how "Selected" swallowed all 36 starred restaurants).
GUIDE_SPECS: dict[str, dict] = {
    "michelin": {
        "tiers": ["3 Stars", "2 Stars", "1 Star", "Bib Gourmand", "Selected"],
        # Floors, not targets. Seoul has consistently had far more than these; the
        # numbers are set low enough that a real year-on-year change passes and only
        # a parser break fails.
        "min_per_tier": {"3 Stars": 1, "2 Stars": 5, "1 Star": 20, "Bib Gourmand": 40},
        "min_rows": 150,
    },
    "blueribbon": {
        "tiers": ["RIBBON_THREE", "RIBBON_TWO", "RIBBON_ONE"],
        "min_per_tier": {"RIBBON_THREE": 1, "RIBBON_TWO": 150, "RIBBON_ONE": 400},
        "min_rows": 700,
    },
}

# A Seoul address contains Hangul or a romanised Korean road/district suffix.
SEOUL_ADDRESS = re.compile(r"[가-힣]|-(gil|ro|daero|gu|dong)\b", re.IGNORECASE)

REQUIRED_COLUMNS = ["source", "name", "address", "category", "latitude", "longitude"]

# How far row count may drift from the previous capture before we refuse to promote.
MAX_ROWCOUNT_DRIFT = 0.20


@dataclass
class ValidationReport:
    source: str
    rows: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tier_counts: Counter = field(default_factory=Counter)
    diff: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [
            "=" * 72,
            f"SCRAPE VALIDATION — {self.source}  ({self.rows} rows)",
            "=" * 72,
            "  tiers: " + (", ".join(f"{k}={v}" for k, v in sorted(self.tier_counts.items()))
                           or "(none)"),
        ]
        if self.diff:
            d = self.diff
            lines += [
                f"  vs previous ({d.get('previous_rows', '?')} rows): "
                f"+{len(d.get('added', []))} added, -{len(d.get('removed', []))} removed, "
                f"{len(d.get('tier_changes', []))} tier changes",
            ]
            for name, old, new in d.get("tier_changes", [])[:10]:
                lines.append(f"      {name}: {old} -> {new}")
            if len(d.get("tier_changes", [])) > 10:
                lines.append(f"      ... and {len(d['tier_changes']) - 10} more")
        for w in self.warnings:
            lines.append(f"  [WARN]  {w}")
        for e in self.errors:
            lines.append(f"  [FAIL]  {e}")
        lines.append("  RESULT: " + ("PASS — safe to promote" if self.ok
                                     else "FAIL — refusing to promote, previous data kept"))
        lines.append("=" * 72)
        return "\n".join(lines)


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _norm(s) -> str:
    return re.sub(r"\s+", "", str(s or "")).strip().lower()


def validate_scrape(rows: list[dict], source: str,
                    previous_path: Path | None = None) -> ValidationReport:
    """Check a freshly scraped guide. Returns a report; caller decides what to do."""
    spec = GUIDE_SPECS.get(source)
    report = ValidationReport(source=source, rows=len(rows))

    if spec is None:
        report.errors.append(f"No validation spec for source '{source}'")
        return report

    if not rows:
        report.errors.append("Scrape returned zero rows")
        return report

    # --- structure -------------------------------------------------------
    missing = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing:
        report.errors.append(f"Missing required columns: {missing}")
        return report

    # --- tier vocabulary + floors ---------------------------------------
    report.tier_counts = Counter((r.get("category") or "").strip() for r in rows)
    unknown = set(report.tier_counts) - set(spec["tiers"])
    if unknown:
        report.errors.append(
            f"Unrecognised tier values {sorted(unknown)} — the parser changed, or the guide "
            f"introduced a tier. Add it to GUIDE_SPECS deliberately; do not let it through."
        )

    for tier, floor in spec["min_per_tier"].items():
        found = report.tier_counts.get(tier, 0)
        if found < floor:
            report.errors.append(
                f"Only {found} x '{tier}' (expected >= {floor}). "
                f"This is the signature of a degraded scrape, not a quiet year."
            )

    if len(rows) < spec["min_rows"]:
        report.errors.append(f"{len(rows)} rows is below the floor of {spec['min_rows']}")

    # --- geography: the Washington DC check ------------------------------
    non_seoul = [r["name"] for r in rows if not SEOUL_ADDRESS.search(r.get("address") or "")]
    if non_seoul:
        report.errors.append(
            f"{len(non_seoul)} rows have a non-Korean address "
            f"(e.g. {non_seoul[:3]}) — wrong city leaked into the scrape"
        )

    # --- field aliasing: the cuisine==address check ----------------------
    # Deliberately NOT exact equality. The real incident had `cuisine` holding the
    # address plus a postcode and country ("...Seoul, 06646, South Korea" vs
    # "...Seoul"), so a byte-comparison missed it entirely. Prefix overlap is what
    # actually catches one column being derived from another.
    def _aliased(x: str, y: str) -> bool:
        if not x or not y:
            return False
        if x == y:
            return True
        n = min(len(x), len(y), 20)
        return n >= 12 and x[:n] == y[:n]

    # Language variants of the same field are SUPPOSED to match — a Korean restaurant's
    # `name` and `name_ko` are legitimately identical, as are `address`/`address_ko` for a
    # Korean-sourced guide. Comparing them flags correct data as corruption.
    def _same_family(a: str, b: str) -> bool:
        def base(c: str) -> str:
            for suffix in ("_ko", "_en"):
                if c.endswith(suffix):
                    return c[: -len(suffix)]
            return c
        return base(a) == base(b)

    cols = [c for c in rows[0].keys() if c not in ("source", "city", "country")]
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            if _same_family(a, b):
                continue
            both = [r for r in rows if (r.get(a) or "").strip() and (r.get(b) or "").strip()]
            if len(both) < 10:
                continue
            same = sum(1 for r in both if _aliased(_norm(r[a]), _norm(r[b])))
            if same / len(both) > 0.5:
                report.errors.append(
                    f"Columns '{a}' and '{b}' hold the same value in {same}/{len(both)} rows — "
                    f"field aliasing (this is how `cuisine` became a copy of `address`)"
                )

    # --- coordinates ------------------------------------------------------
    bad_coords = 0
    for r in rows:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
            if not (37.3 <= lat <= 37.75 and 126.7 <= lon <= 127.25):
                bad_coords += 1
        except (TypeError, ValueError):
            bad_coords += 1
    if bad_coords:
        pct = bad_coords / len(rows)
        msg = f"{bad_coords} rows have missing or non-Seoul coordinates"
        (report.errors if pct > 0.10 else report.warnings).append(msg)

    # --- duplicates -------------------------------------------------------
    dupes = sum(c - 1 for c in Counter(_norm(r.get("name")) for r in rows).values() if c > 1)
    if dupes:
        report.warnings.append(f"{dupes} duplicate restaurant names within the scrape")

    # --- diff vs the capture we would be replacing ------------------------
    if previous_path is not None:
        prev = _read_rows(previous_path)
        if prev:
            report.diff = _build_diff(prev, rows)
            drift = abs(len(rows) - len(prev)) / len(prev)
            if drift > MAX_ROWCOUNT_DRIFT:
                report.errors.append(
                    f"Row count moved {drift:.0%} ({len(prev)} -> {len(rows)}), "
                    f"beyond the {MAX_ROWCOUNT_DRIFT:.0%} guard"
                )
            lost = [t for t in spec["min_per_tier"]
                    if Counter((r.get('category') or '').strip() for r in prev).get(t, 0) > 0
                    and report.tier_counts.get(t, 0) == 0]
            if lost:
                report.errors.append(f"Tiers present before but absent now: {lost}")

    return report


def _build_diff(prev: list[dict], new: list[dict]) -> dict:
    """Internal pre-promotion diff. Not a user-facing feature."""
    pmap = {_norm(r.get("name")): r for r in prev}
    nmap = {_norm(r.get("name")): r for r in new}
    added = [nmap[k].get("name") for k in nmap.keys() - pmap.keys()]
    removed = [pmap[k].get("name") for k in pmap.keys() - nmap.keys()]
    changes = []
    for k in pmap.keys() & nmap.keys():
        old = (pmap[k].get("category") or "").strip()
        cur = (nmap[k].get("category") or "").strip()
        if old != cur:
            changes.append((nmap[k].get("name"), old, cur))
    return {"previous_rows": len(prev), "added": sorted(added),
            "removed": sorted(removed), "tier_changes": sorted(changes)}


def promote_capture(rows: list[dict], source: str, raw_dir: Path,
                    fieldnames: list[str], canonical_name: str | None = None) -> Path:
    """
    Write a dated capture, then promote it to the filename the build reads.

    Dated captures are kept so any regression can be diffed and rolled back — the old
    behaviour (overwrite in place) left nothing to compare against.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    dated = raw_dir / f"{source}.{date.today().isoformat()}.csv"
    with open(dated, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    canonical = raw_dir / (canonical_name or f"{source}.csv")
    shutil.copy2(dated, canonical)
    print(f"[promote] {len(rows)} rows -> {dated.name}, promoted to {canonical.name}")
    return canonical


def validate_file(source: str, path: Path) -> ValidationReport:
    """Validate an existing CSV on disk (no diff — it IS the current capture)."""
    return validate_scrape(_read_rows(Path(path)), source)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print(f"known sources: {', '.join(GUIDE_SPECS)}")
        sys.exit(2)
    source, path = sys.argv[1], Path(sys.argv[2])
    report = validate_file(source, path)
    print(report.render())
    sys.exit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
