# Eat My Seoul — Overhaul Plan

**Started:** 2026-08-09
**Last updated:** 2026-08-09
**Status legend:** `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked/needs decision

---

## 0. How to use this document

This is the working plan for bringing Eat My Seoul back to health and pushing it past
where it was. It is written to be resumable: if a session ends mid-way, say
*"pick up where you left off on overhaul.md"* and the next session can read this file,
find the first unchecked item in the lowest-numbered incomplete phase, and continue.

**Rules for whoever works on this:**
1. Update the checkbox **and** the changelog at the bottom when you finish something.
2. If you discover a new gap, add it to the right phase rather than fixing it silently.
3. Acceptance criteria are not optional — an item is `[x]` only when its criteria are
   verified against real data, not when the code "looks right."
4. Items marked `[!]` need a decision from Alexander before proceeding. Don't guess.

---

## 1. Product thesis (read this before changing scoring)

**What this is:** an English-language, opinionated, *reliable* map of where to eat in Seoul —
combining two established guides (Michelin, Blue Ribbon) with our own AI-built guide to
elevated comfort food and drinking culture (the Neon Guide).

**The promise to the user:** *"If it's on this map, it's been vetted. Show up, order what
they're known for, and you will have a genuinely good time — food, vibe, service, and value."*

**The core design principle — broad floor, scarce peak.**

This is the single most important correction to the current system. We are **not** cutting the
map down to a few hundred restaurants. A big, fun, dense map is the point. What has to change
is that **inclusion and distinction are two different things**, and right now they're collapsed
into one inflated scale:

| Layer | Meaning | Should be | Currently |
|---|---|---|---|
| **Inclusion** (on the map at all) | "The people have vetted this. It's reliably good." | Broad — thousands | Broad ✅ but **unearned** (see Phase 2) |
| **Distinction** (hearts) | "This is exceptional. Go out of your way." | Scarce — low single-digit % | **44% of entries hold 2–3 hearts** ❌ |

Michelin works exactly this way: the *selection* is hundreds of restaurants; stars are rare.
We should mirror that. The base tier is not a consolation prize — it is the whole product.
The hearts are the editorial signal on top.

**Our actual differentiator** is not "AI search on a map." Naver and Kakao will always beat us
on location, hours, and reviews. It is **trustworthy editorial judgment about Korean comfort
food, in English, for people who cannot read Korean blogs.** Every decision below should be
tested against: *does this make the map more trustworthy, or just bigger?*

---

## 2. Current state snapshot (2026-08-09)

Live at `https://eatmyseoul.onrender.com` (Render + Cloudflare, HTTP 200, brotli at edge).

**`site/places.geojson` — 2,670 pins, built 2026-04-23 from raw scrapes captured 2026-02-14.**

| Source | Pins | Tiers present | Tiers missing |
|---|---|---|---|
| Michelin | 200 | Selected (144), Bib Gourmand (56) | **all 36 starred restaurants** |
| Blue Ribbon | 939 | RIBBON_ONE (657), RIBBON_TWO (282) | **RIBBON_THREE (2)** |
| Neon | 1,531 | 1♥ 608 · 2♥ 571 · 3♥ 97 · "None" 228 · blank 26 | — |

**Health indicators:**

- 104 restaurants render as **two separate pins** (same `kakao_id`, one Michelin + one Blue Ribbon)
- 303 restaurants the receipt auditor marked `Rating Justified = No` are **live on the map**
- 413 Neon entries (27%) have **no receipt audit verdict at all**
- 786 Neon entries (51%) have **no sponsorship ratio** — the anti-paid-blog caps had no input
- Neon score distribution: min 70 · p25 80 · **median 86** · p75 90 · p90 93 · max 99
- Coverage: **43 of Seoul's ~467 dong**; 치킨 alone is 327 entries (21% of the guide)
- Nothing in the system can detect a closure, a chef change, or a decline. The guide only grows.
- `.git` is **296 MB** across 126 commits (32MB FAISS index + 4.7MB geojson committed each patch)

---

## 3. PHASE 1 — Data integrity emergency (P0)

*Goal: the map should honestly represent the two guides it claims to represent.*
*This is the highest value-per-hour work in the entire plan.*

### 1.1 Restore Michelin stars and the third Blue Ribbon `[x]` — done 2026-08-09

**The bug:** `build_map_list.py:653` builds from `data/raw/michelin.csv` (captured 2026-02-17),
in which every starred restaurant was collapsed to `Selected`. A later scrape,
`out/michelin_seoul_raw.csv` (2026-03-14), has the correct tiers: **27 × 1 Star, 8 × 2 Stars,
1 × 3 Stars**. The star-detection logic at `build_map_list.py:410-417` is correct and works —
it simply never ran into the file the build reads. Identical story for Blue Ribbon:
`data/raw/blueribbon.csv` has 0 × `RIBBON_THREE`; `out/blueribbon_seoul_raw.csv` has 2
(and 292 vs 282 `RIBBON_TWO`).

**Why it matters beyond the data:** the frontend *already ships* ⭐⭐⭐ / ⭐⭐ / ⭐ / 🔵🔵🔵
filter pills (`site/index.html:46-51`) and badge rendering (`app.js:258-268`) that currently
match **zero** restaurants. Six dead controls in the UI. Fixing the data lights them up with
no frontend work.

**Tasks:**
- [x] Reconcile the two raw scrape locations (`data/raw/` vs `out/`) into one canonical input path
- [x] Point `build` at the star-bearing data; verify `1 Star` / `2 Stars` / `3 Stars` survive into `places.geojson`
- [x] Same for `RIBBON_THREE`
- [ ] Confirm the ⭐ and 🔵🔵🔵 filter pills now match restaurants in the live UI *(needs a browser check)*

**How it was fixed — `repair_guide_tiers.py` (new, idempotent, writes `.bak` backups):**

We **patch** the existing raw files rather than swapping to the `out/` ones. The Kakao ledger
(`data/cache/kakao_ledger.json`) is keyed on `slugify(name)__slugify(address)` of the *existing*
files — they hit 100%, the `out/` files hit ~0%. Swapping would have discarded every resolved
`kakao_id` and forced ~1,150 fresh Kakao API calls. So we keep the base rows and their
descriptions and overwrite only the fields that are wrong. Joins are name-first (exact after
normalisation), then coordinate, because coordinate-only matching at 4dp produced a confirmed
mis-pairing. Names that are ambiguous within a file are excluded from the name index.

**Additional corruption found and fixed along the way:**
- **4 Washington DC restaurants** (Café Riggs, Family Ethiopian, La'Shukran, Xiquet) had leaked
  into the Seoul Michelin file from a bad scrape. Dropped.
- **`cuisine` held a copy of `address` for all 200 Michelin rows** — and that garbage was being
  fed into the embeddings as `카테고리:`. Replaced with real cuisine.
- **`price` was empty for all Michelin rows.** Backfilled (₩ – ₩₩₩₩).
- **`cuisine` was empty for all 939 Blue Ribbon rows.** 843 backfilled.
- 10 Blue Ribbon entries were holding a stale RIBBON_TWO and are actually RIBBON_ONE.
- 다이닝마 and 권숙수 (both RIBBON_THREE) were missing from the base file entirely. Added.

**Result:**

| | before | after |
|---|---|---|
| Michelin 3 Stars | 0 | **1** (Mingles) |
| Michelin 2 Stars | 0 | **8** |
| Michelin 1 Star | 0 | **27** |
| RIBBON_THREE | 0 | **2** |
| Non-Seoul rows | 4 | 0 |
| Michelin rows with `cuisine == address` | 200 | 0 |

**Residual, accepted:** 96 Blue Ribbon rows could not be joined to the good scrape (44 names are
ambiguous across branches; the rest are English-named rows absent from the `out/` scrape). They
keep their existing tier. Verified that for all 24 affected rows the current tier is already among
the donor's tiers, so nothing is known-stale. **Phase 1.4 (fresh re-scrape) supersedes this.**

---

### 1.2 Deduplicate Michelin ↔ Blue Ribbon onto single pins `[x]` — done 2026-08-09

**The bug:** `build_map_list.py` merges on `slugify(name + address)`. Michelin names are
romanized (`Bongsanok`), Blue Ribbon names are Hangul (`봉산옥`) — the keys can never collide.
The one reliable join key, `kakao_id`, *is* resolved — but by `enrich_places_with_ledger()`,
which runs **after** the merge. Result: 104 duplicate pins including 벽제갈비, 비채나, 봉밀가, 대성집.

**This is the founding premise of the project failing visibly on the map.**

**Tasks:**
- [x] Move Kakao ledger enrichment to run **before** deduplication
- [x] Dedupe on `kakao_id` as primary key; keep name+address slug as fallback for null IDs
- [x] **Merge awards rather than dropping a record**
- [x] Introduce a multi-award data shape (`awards: [{guide, tier}]`), keep flat `category`
- [x] Handle rows with no `kakao_id` — they fall back to the slug key and simply stay unmerged

**How it was fixed — `build_map_list.py`:**
- New `merge_duplicate_places()` groups by `kakao_id`, falling back to a name+address slug.
  The fallback can only *fail to merge* (a duplicate pin), never wrongly merge two different
  restaurants — the safe direction.
- `enrich_places_with_ledger()` now runs on the full record set **before** merging.
- Added `awards: list` to the `Place` dataclass. `category` still holds the primary guide's tier.
- `source` is now a **space-joined** string (`"michelin blueribbon"`). This required **zero
  frontend changes**: `app.js` matches source with `.includes()` everywhere
  (`:216-220`, `:254`, `:385-386`, `:430-431`), so both guides' filter pills correctly
  control the pin, and `getBadge()`'s michelin→blue→neon precedence picks the right badge.

**Two bugs found and fixed while in here:**
- `build_map_list.py` never called `load_dotenv`, so `kakao_rest_key()` returned `None` and
  ledger enrichment **silently no-opped** unless the key happened to be exported in the shell.
  Now loads `soul-food-api/.env` like every other script in the project.
- `load_raw()` was **deleting `description_ko` on load**, throwing away Ghostwriter-authored
  native Korean descriptions and forcing `build_embeddings.py` to re-translate the English.
  Now preserved. Also now filters unknown CSV columns instead of crashing on them.

**Result:** 2,672 records → **2,566 pins**. 106 duplicates collapsed; **104 pins now carry
multiple guides**; **zero remaining `kakao_id` collisions**; no restaurant lost.

| source combination | pins |
|---|---|
| `michelin blueribbon` | 92 |
| `blueribbon neon` | 11 |
| `michelin blueribbon neon` | 1 (Mandujip) |

Spot-checked: 벽제갈비, 봉산옥, 비채나 each one pin with both guides. 권숙수 now correctly
shows **Michelin 2 Stars + Blue Ribbon RIBBON_THREE** on a single pin. Mingles is the only 3-star.

**Known-good residual:** 142 pins still have no `kakao_id` (8 michelin, 122 blueribbon, 12 neon)
and therefore cannot participate in ID-based merging. Down from 142 *Michelin alone* before.

**Frontend limitation introduced — fold into 2.4, do not fix separately.**
`passes()` (`app.js:225-240`) is an `if / else if` chain on source, so a merged pin only
evaluates its **primary** guide's tier filters. Concretely: a Michelin + RIBBON_TWO pin ignores
the 🔵🔵 pill, and a Blue Ribbon + Neon pin ignores the Neon tier pills. The **source-level**
pills (Michelin / Blue Ribbon / Neon) work correctly for every guide on the pin, because those
are checked first and use `.includes()`.

The proper fix is to filter off the `awards` array instead of the flat `category`, which is
exactly the refactor 2.4 already requires. Do both at once.

---

### 1.3 Retire or repair `dedupe_master.py` `[x]` — done 2026-08-09

It is documented as Phase 3 of the pipeline in `README.md` but is **dead code**:
- reads `Latitude`/`Longitude`; the queue writes `Lat`/`Lon`
- loads master data from `seoul-food-api/` — the directory is `soul-food-api/`, so it silently
  falls back to a **hardcoded 2-restaurant dummy list** (Mingles, Ggupdang)
- requires a local Ollama `qwen2.5:3b` on `localhost:11434` that the pipeline never starts
- its output, `ready_for_map_import.csv`, is read by nothing
- `build_map_list.py`'s `build` command never calls it

- [x] Deleted, along with its orphaned output `ready_for_map_import.csv`
- [x] Removed from `master_agent.py` (was PHASE 4) and renumbered the README pipeline
- [x] README also corrected: 768-dim → 3072-dim, plus a warning that `places.geojson` and
      `restaurant_vectors.index` must always ship together (`vector_id` is positional)

---

### 1.4 Pin coordinates silently defer to the Kakao ledger `[ ]`

**Found while fixing 1.2 — not yet fixed, deliberately not bundled.**

`enrich_places_with_ledger()` decides what to trust with
`has_coords = isinstance(p.latitude, float)` (`build_map_list.py:179`). But `load_raw()`
builds `Place` objects straight from CSV strings, so **`latitude` is a `str` and `has_coords`
is always `False`** for every Michelin and Blue Ribbon row. Two consequences:

1. The scraped coordinates are discarded in favour of whatever the Kakao ledger cached.
2. The 2,000 m sanity check that guards against a bad Kakao match **never runs** on the
   cache-hit path — it is guarded by `has_coords`.

This is very likely the root cause of the one-off pin fixes in the git history
(e.g. *"Patch 1.475: Fix Yukjeon Hoekwan pin location"*).

Not fixed in the same pass as 1.2 because casting to `float` flips which coordinate source
wins and would move a large number of pins — that deserves its own before/after diff.

- [ ] Cast `latitude`/`longitude` to `float` in `load_raw()`
- [ ] Re-run the build and produce a diff of how far each pin moved
- [ ] Review the largest movements by hand before accepting
- [ ] Re-run `audit_michelin_coords.py` afterwards to confirm the improvement

---

### 1.5 Future-proof the scrapers so this can't silently happen again `[x]` — done 2026-08-09

**Shipped as `scrape_validation.py` + `save_raw_guarded()` in `build_map_list.py`.**
Model is **validate → dated capture → promote**; nothing overwrites the file the build
reads until the data has passed.

**Proof it works — the guard was fed the actual historical bad scrape:**
```
      Evett: 2 Stars -> Selected          ← diff names each restaurant losing a star
      Exquisine: 1 Star -> Selected
  [FAIL] Only 0 x '3 Stars' (expected >= 1)
  [FAIL] 4 rows have a non-Korean address (['Café Riggs', 'Family Ethiopian', ...])
  [FAIL] Columns 'address' and 'cuisine' hold the same value in 204/204 rows
  [FAIL] Tiers present before but absent now: ['3 Stars', '2 Stars', '1 Star']
  RESULT: FAIL — refusing to promote, previous data kept
→ promoted? False | canonical file byte-identical afterwards? True
```
Current repaired files both **PASS**; both historical degraded files **FAIL**.

- [x] Tier floors that abort the write (≥1 3★, ≥5 2★, ≥20 1★, ≥40 Bib, ≥1 RIBBON_THREE)
- [x] Row count within ±20% of the previous capture, plus absolute floors
- [x] Non-Seoul address rejection (the Washington DC check)
- [x] Field-aliasing detection (the `cuisine == address` check)
- [x] Tier vocabulary pinned in `GUIDE_SPECS`; an unrecognised tier **fails** rather than
      landing on the map as an unfilterable category
- [x] Dated captures (`michelin.<date>.csv`) + promotion to the canonical name — never
      overwrite in place, so any regression can be diffed and rolled back
- [x] Internal pre-promotion diff (added / removed / tier changes). **Not user-facing** —
      the "restaurant gains a star" product feature was explicitly shelved.
- [x] `--test-limit` runs write to `michelin.testrun.csv` and never promote — a truncated
      test can't meet the floors and must not be able to overwrite production input
- [x] `data/raw/` now version-controlled
- [x] Standalone CLI: `python scrape_validation.py michelin data/raw/michelin.csv`
- [ ] Fold into the Phase 7 test suite so CI runs it too

**Two design notes for whoever tunes this:**
- Aliasing detection uses **prefix overlap, not equality** — the real incident had `cuisine`
  holding the address *plus* a postcode and country, so a byte-comparison missed it entirely.
- Language variants (`name`/`name_ko`, `address`/`address_ko`) are **excluded** from aliasing
  checks. They are legitimately identical for a Korean-sourced guide; comparing them flags
  correct data as corruption.

**A real defect the validator found on its first run:** 427 Blue Ribbon rows had the
restaurant's own name sitting in the `description` field. Harmless on the map only because
`load_raw()` prefers `description_en`. Cleared — but **21 pins now have no description at
all** (their `description_en` was also empty). Empty is more honest than a name masquerading
as a description, and they still embed via name + category, but they're candidates for
`generate_guide_descriptions.py`.

<details><summary>Original problem statement (superseded)</summary>

**This is the durable fix behind 1.1.** The star data wasn't lost to a hard bug — it was lost
because a scrape silently produced degraded output and nothing checked. `build_map_list.py fetch`
overwrites `data/raw/*.csv` unconditionally, so the next re-scrape can wipe the repair from 1.1
with no warning. Everything below must exist **before** running 1.5.

**Make the scraper fail loudly instead of quietly:**
- [ ] **Post-scrape assertions that abort the write.** A Seoul Michelin scrape with **zero starred
      restaurants is not a valid scrape** — it's a parser break. Assert: ≥1 three-star, ≥5 two-star,
      ≥20 one-star, ≥40 Bib Gourmand, ≥1 RIBBON_THREE, and total rows within ±20% of the previous
      capture. Fail the run and keep the old file rather than overwrite with garbage.
- [ ] **Reject rows that aren't in Seoul** — the check that would have caught the 4 Washington DC
      restaurants. Require Hangul or a romanised Korean road suffix in the address.
- [ ] **Reject field-aliasing** — assert `cuisine != address` (the bug that hit all 200 Michelin
      rows). More generally, assert no two columns are identical across >50% of rows.
- [ ] **Never overwrite in place.** Write `data/raw/michelin.<YYYY-MM-DD>.csv`, validate, then
      update a `latest` pointer. Keeps every capture for diffing and makes rollback trivial.
- [ ] **Emit a diff report** on every scrape: additions, removals, tier changes — reviewed before
      the new capture is promoted. Guides gaining/losing a star is exactly the news the map exists
      to convey, so this is a product feature, not just a safety check.
- [ ] **Preserve the `awards`-era shape.** `fetch` must emit every column `load_raw()` expects and
      the tier vocabulary `merge_duplicate_places()` keys on (`3 Stars`/`2 Stars`/`1 Star`/
      `Bib Gourmand`/`Selected`, `RIBBON_THREE`/`RIBBON_TWO`/`RIBBON_ONE`). Pin these in one
      constant and assert scraped values are in it — an unrecognised tier string should fail the
      run, not silently land on the map as an unfilterable category.
- [ ] **Version-control `data/raw/`** — currently untracked, so the build inputs aren't in git.
- [ ] Fold these assertions into the Phase 7 test suite so CI runs them too.

**Acceptance criteria:** deliberately feeding the scraper a broken selector fails the run and
leaves the previous data intact. Re-running 1.1's repair after a fresh scrape is a no-op.

</details>

---

### 1.6 Re-scrape both guides `[!]` — BLOCKED 2026-08-09, needs a decision

**Attempted and stopped safely. Both scrapers are dead — the sites changed their access
posture during the six months the pipeline sat idle.** No production data was touched.

| Guide | Result | Diagnosis |
|---|---|---|
| Michelin | `HTTP 202`, empty/challenge body | **AWS WAF JavaScript challenge** (`window.awsWafCookieDomainList`, `gokuProps`, served via CloudFront). Not a selector or header problem — better headers only change an empty body into the 2 KB challenge page itself. |
| Blue Ribbon | `403` with params, `404` without | **The `/api/v1/*` endpoints are gone.** Every variant tried (`/api/v1/restaurants`, `/api/v2/…`, `/api/restaurants`, `…/search`) returns the SPA's HTML 404 fallback. The site itself is fine (homepage 200, 96 KB) — the API moved or was versioned away. |

**1.5 worked exactly as designed.** `--test-limit` caught the Michelin failure before any
network write, and the guard would have refused the empty result regardless. The live map is
untouched and still serving the repaired February data. This is the system doing its job:
six months ago this same failure silently shipped.

**Decision needed — these are different problems with different answers.**

*Michelin (WAF challenge).* Getting past it means deliberately defeating an anti-bot control
the site owner put in place. That is a judgement call for Alexander, not one to make silently.
Worth weighing: **the Michelin Guide updates once a year** (the 2026 Seoul & Busan ceremony has
already happened), and Seoul is ~200 restaurants of which only the ~92 starred/Bib entries carry
tier information that matters. Annual manual curation is genuinely defensible here and sidesteps
the question entirely.
  - [ ] Option A: manual/annual tier curation from the published guide (recommended)
  - [ ] Option B: drive a real browser (Selenium is already a project dependency) — works, but
        is deliberate circumvention, brittle, and needs re-fixing whenever the challenge changes
  - [ ] Option C: look for an official Michelin data feed / licensing route

*Blue Ribbon (API moved).* Much less fraught — this is a public SPA calling its own public API.
The fastest fix is to read the current endpoint off the network tab:
  - [ ] Open `bluer.co.kr`, search Seoul, copy the request URL the page itself issues, and point
        `scrape_bluer_run()` at it. Two minutes of Alexander's time beats an hour of guessing.

**Until this is resolved, guide data stays at the 2026-02-14 capture.** Note the 2026 guide was
announced with a *"record number of new and promoted starred restaurants,"* so our 27 one-star /
8 two-star counts are probably understated. The one number we did verify against the live guide
is correct: **Mingles is Korea's only three-star restaurant.**

<details><summary>Original task description</summary>

Current data is from **2026-02-14** — roughly six months and two guide news cycles stale.
`build_map_list.py fetch` already does this.

- [ ] Run a fresh Michelin scrape; verify star counts against the published guide
- [ ] Run a fresh Blue Ribbon scrape; verify ribbon counts
- [ ] Diff old vs new: report additions, removals, and tier changes rather than silently overwriting
- [ ] Record `captured_at` per source and surface "guide data as of {date}" in the UI

**Acceptance criteria:** a written diff report, reviewed before the new data goes live.

</details>

---

## 4. PHASE 2 — Earn the word "vetted" (P1)

*Goal: make the inclusion floor mean what we tell users it means, and make the hearts scarce.*

### 2.1 Enforce the auditor we already built `[x]` — done 2026-08-09

`README.md` core principle #1 states that receipt-verified negatives revoke an award.
**They do not.** `final_verdict.py:308-318` quarantines only on `Needs Manual Review`,
`Score < 70`, or `Upgrade Recommended`. `Rating Justified` is never a gate — the only place
it's consulted (`final_verdict.py:347`) decides who gets *rescued out* of quarantine.

**303 restaurants the auditor explicitly condemned are on the live map.**

- [x] Added `Rating Justified == 'No'` to the quarantine mask (`final_verdict.py`)
- [x] Ran the gate in-place over the existing 1,531 — **303 quarantined**
- [x] `Unknown` (17) and blank (413) deliberately **not** quarantined — absence of evidence
      isn't evidence of guilt, and blanket-quarantining them would gut the guide over a
      scraper limitation. They're 2.2's job (backfill + `verification_level`).

**Result:** Neon guide 1,531 → **1,228**. Zero live pins carry a `No` verdict.
README core principle #1 is now true. Map: 2,566 → **2,267 pins**.

**Second bug found and fixed while in here:** the quarantine write was a bare
`to_csv('needs_human_attention.csv')` — **every run silently discarded everything previous
runs had quarantined.** The quarantine pile is the rescrape/appellate work queue, so this
was ongoing loss of real work. Now merges, with fresh verdicts superseding stale rows
(641 existing + 357 new → 998, nothing lost).

**Judgement call worth knowing about:** running the gate standalone also swept out 54
restaurants flagged `Upgrade Recommended` — places the auditor thought deserved a *higher*
score (70–95, including a 3-heart). In a full pipeline run `appellate_court.py` re-judges
and promotes them, but that re-scrapes 12 blogs each via Gemini. Rather than launch that
unilaterally, they were **restored to the guide with their upgrade flag intact** so the next
full run handles them. Net effect is exactly the 303 intended removals, nothing else.

---

### 2.2 Close the receipt-verification hole `[ ]`

We cannot honestly say "vetted by the people" when **27% of the Neon Guide has no verdict at all**
and **51% has no sponsorship ratio**. The floor is currently a claim, not a fact.

Root causes: `receipt_auditor.py` runs only over `neon_guide_audited_final.csv`, so entries added
later by `reclassify.py`, `appellate_court.py`, or the protected-row merge in
`final_verdict.py:360-363` never pass through it. Naver Map's DOM also defeats the Selenium
scraper often enough to leave "Unknown" verdicts.

- [ ] Backfill: run the auditor over every Neon entry with a blank or `Unknown` verdict
- [ ] Make the auditor run **after** reclassify/appellate, not only before
- [ ] Harden `scrape_receipt_reviews()` — it depends on brittle XPath (`//li`, class `YwYLL`)
- [ ] Capture **review count and rating** alongside review text — volume is itself a vetting signal
- [ ] Add a `verification_level` field per restaurant: `receipt-verified` / `blog-only` / `unverified`
- [ ] **Do not show `unverified` entries on the map** once backfill is complete

**Acceptance criteria:** ≥95% of live Neon pins are `receipt-verified`; the rest are hidden, not shown.

---

### 2.3 Re-tier the Neon Guide: broad floor, scarce peak `[x]` — done 2026-08-09

> **DECIDED (Alexander, 2026-08-09):** global thresholds, not per-dish. Base tier is
> **"Neon Vetted"** (70–90). Rationale in his words: *"I don't want to over-inflate hearts
> if the score doesn't meet the criteria."* Per-dish ranking was rejected because it would
> award a 3-heart to the best place in a weak category regardless of absolute merit.
>
> **Paired requirement he added:** every dish/category must still be *present and accurately
> represented* on the map — coverage is not the same as awards. That is **Phase 5.1's** job,
> not this item's. Global tiering decides what badge a place wears; coverage decides what's
> on the map at all. Do not "fix" thin coverage by loosening tiers.
>
> **Correction on the record:** an earlier version of this doc recommended per-dish ranking
> because 치킨 is 24% of the guide. That reasoning was doubly wrong — 치킨 is *under*-represented
> at the top (9% of qualifiers at ≥95), and, as Alexander pointed out, the dish mix reflects
> **which keywords he searched**, not Seoul's culinary reality. Per-dish score comparisons in
> this doc are confounded by sampling and should not be used as evidence about dish quality.

**Shipped as built:**

| Tier | `tier` code | Rule | Count | Share |
|---|---|---|---|---|
| 3 Neon Hearts | `NEON_3` | ≥ 98 | 14 | 1.1% |
| 2 Neon Hearts | `NEON_2` | 95–97 | 71 | 5.8% |
| 1 Neon Heart | `NEON_1` | 91–94 | 174 | 14.2% |
| **Neon Vetted** | `NEON_VETTED` | 70–90 | **969** | **78.9%** |

Thresholds live in one place — `NEON_TIERS` in `build_map_list.py::load_neon_guide`. The CSV's
stored `Award Level` is deliberately ignored; it came from the old inflated scale.

**Still open from this item:** the anchor set (hand-scored reference restaurants) — this
re-tiered existing scores, it did not recalibrate *scoring*. 제육볶음 is the natural first
anchor category: 45 entries, none scoring above 91, while a similarly-sized 순대국 sample
(47) reached 99. That gap is not explained by sample size and is worth a human check.

<details><summary>Original decision framing (superseded)</summary>

The rubric in `critic_agent.py:331-336` defines 95-100 as "flawless execution, destination-worthy."
**97 restaurants currently hold that tier.** Michelin Seoul has one three-star. The scale has no
fixed reference point, so an LLM scoring each restaurant in isolation drifts upward indefinitely.

**Proposed model** (keeps the map big — only ~20% of entries carry a heart at all):

| Tier | Meaning | Share | ~Count of 1,531 |
|---|---|---|---|
| **Neon Vetted** (no heart) | "The people vouch for this. You'll have a good time." | ~80% | ~1,210 |
| **1 Neon Heart** | "Worth crossing the neighbourhood for." | ~15% | ~230 |
| **2 Neon Hearts** | "Worth crossing the city for." | ~4% | ~60 |
| **3 Neon Hearts** | "Worth planning a trip around." | ~1% | ~15 |

**Tasks:**
- [!] **Decision needed:** confirm the tier names and thresholds, and name the base tier
      ("Neon Vetted" is a placeholder — it's what 79% of the map carries, so it matters most)
- [!] **Decision needed:** ~~rank within dish category~~ → **rank globally** (recommendation
      reversed 2026-08-09 — see the measured data below)

**Measured 2026-08-09 against the post-2.1 guide (n=1,228). The per-category concern was wrong:**

치킨 is 24% of the guide but has the **lowest mean score of any major category (83.0** vs 86–88),
so it is already *under*-represented at the top — 9% of qualifiers at ≥95, 12% at ≥91. A global
cut yields 순대국 / 육회 / 곱창 / 국밥 at the summit, which reads like a real guide.

Per-category ranking has the opposite flaw: it **forces a 3-heart into every dish**, including
수제맥주 (mean 79.1, weakest in the guide) — guaranteeing a "destination-worthy craft beer bar"
whether or not one exists. Same inflation, just distributed.

**Use score thresholds, not percentages.** Scores are coarse integers with large tie groups
(125 restaurants share score 90; 29 share 96), so a percentage cutoff slices a tie group
arbitrarily — identical scores landing in different tiers on sort order. Thresholds include ties.

| Threshold | Qualifying | Share |   | Proposed tier | Rule | Count | Share |
|---|---|---|---|---|---|---|---|
| ≥ 98 | 14 | 1.1% |   | **3 Neon Hearts** | ≥ 98 | 14 | 1.1% |
| ≥ 96 | 47 | 3.8% |   | **2 Neon Hearts** | 95–97 | 71 | 5.8% |
| ≥ 95 | 85 | 6.9% |   | **1 Neon Heart** | 91–94 | 174 | 14.2% |
| ≥ 93 | 152 | 12.4% |   | **Neon Vetted** | 70–90 | 969 | **79%** |
| ≥ 91 | 259 | 21.1% |   | | | | |
| ≥ 88 | 535 | 43.6% ← today's inflated line | | | | | |

**Caveat:** this re-tiers the *existing* scores, which came from the uncalibrated rubric — it fixes
the distribution immediately. The anchor set fixes the *scoring*, and is independent of this.

</details>
- [ ] Build an **anchor set**: 30–40 restaurants Alexander has personally eaten at, hand-scored.
      Feed as few-shot exemplars so the model has fixed reference points instead of a floating scale.
- [ ] Convert scoring from absolute thresholds to **rank-based cutoffs** applied after all scoring
- [ ] Re-tier the existing 1,531 without re-scraping (scores are already on disk)
- [ ] Fix the 33 rows where `Award Level` contradicts `Score`, and the 1 malformed value
      (`"2 Neon Hearts (exceptional neighborhood staple)"`)

**Acceptance criteria:** ≤2% of the guide holds 3 hearts; ≥75% sits at the base tier; the map still
shows well over a thousand pins.

---

### 2.4 Move Neon tiering off emoji-in-description `[x]` — done 2026-08-09 (with 2.3 and 4.2)

**Shipped together as predicted.** Neon pins now carry a machine-readable `tier` field
(`NEON_3` / `NEON_2` / `NEON_1` / `NEON_VETTED`), the frontend filters and badges read it,
and the `"✨ Exceptional Gastronomic Experience"` prefix is gone from descriptions — which
also completes **4.2** (that string was being embedded into every Neon vector, clustering
them by award tier rather than by food). Filter pills relabelled 💖💖💖 / 💖💖 / 💖 / ✅ Vetted.

**Bonus fix — the `if/else if` limitation logged under 1.2 is now closed.** `passes()` checked
guides as an else-if chain, so a merged pin only ever evaluated its *primary* guide's tier
filters (a Michelin + RIBBON_TWO pin ignored the 🔵🔵 pill). Each guide is now checked
independently, reading from the `awards` array.

<details><summary>Original problem statement (superseded)</summary>

**Dependency warning — this must ship together with 2.3 and 4.2.**

Neon tiers are currently detected by looking for emoji inside the description string
(`app.js:237-239` and `:272-274` test `desc.includes("✨")`). The emoji gets there from
`build_map_list.py:590`, which prepends `"✨ Exceptional Gastronomic Experience"` to every
description — and that prefix is then **embedded into the search vector** (Phase 4.2).

So: removing the boilerplate to fix retrieval will silently break the frontend filters unless
tiering moves to a real field first.

- [ ] Add a first-class `tier` property to Neon features
- [ ] Update `app.js:237-239`, `:272-274`, `:367-369` to read `tier`, not description text
- [ ] Remove the tier prefix from the description string
- [ ] Update the filter pill labels in `site/index.html:53-55` to the new tier names

</details>

---

## 5. PHASE 3 — Freshness: make the guide able to change its mind (P1)

*Goal: a guide, not a snapshot. Today the system is structurally incapable of noticing that a
restaurant closed, changed chefs, or declined.*

Two mechanisms lock it in place:
- `master_agent.py:151-172` — any restaurant name ever seen is skipped forever
- `final_verdict.py:312-318` — anything already scoring ≥70 is permanently shielded from demotion

Six months have passed. Seoul restaurant churn is high. Some meaningful number of our 1,531
"vetted" recommendations no longer exist, and we would never know.

- [ ] Add `last_verified` and `first_listed` timestamps to every entry, all three sources
- [ ] Build a **closure check**: probe each `kakao_id` and flag places that no longer resolve
- [ ] Run the closure check across all 2,670 pins and report the damage
- [ ] Add a re-verification TTL (proposal: 6 months) that returns stale entries to the queue
- [ ] Replace the permanent shield with **hysteresis** — a listed restaurant needs a clearly worse
      re-score to be demoted, but demotion must be possible
- [ ] Surface freshness in the UI: "verified {month}" on each card
- [ ] Schedule the whole thing to run on a cadence rather than by hand

**Acceptance criteria:** a restaurant that closes is off the map within one refresh cycle.

---

## 6. PHASE 4 — Retrieval quality (P2)

*Goal: stop patching a retrieval problem in the orchestration layer.*

### 4.1 Put location into the embeddings `[ ]`

`build_embeddings.py:177-182` builds `rich_text` from name + cuisine + description + verdict.
**There is no address and no neighbourhood in it.** That single omission is why `server.js:133-145`
carries a hardcoded 60-item neighbourhood list and three fallback "Plans" — all of it compensating
for an index that cannot answer "near Itaewon."

- [ ] Add `address_ko`, neighbourhood (dong), and district (gu) to `rich_text`
- [ ] Re-embed and confirm neighbourhood queries work **without** the hardcoded list
- [ ] Delete or shrink `SEOUL_NEIGHBOURHOODS` in `server.js` once the index handles it

### 4.2 Remove tier boilerplate from vectors `[x]` — done 2026-08-09 (shipped with 2.3/2.4)
Every Neon vector used to begin with the same tier phrase. Removed; the tier now lives in its
own field. Index rebuilt against the cleaned descriptions.

### 4.3 Use embedding task types `[ ]` — ⚠️ **conditional, see §9b**
Neither `build_embeddings.py:63-71` nor `server.js:52` passes a `task_type`.
`RETRIEVAL_DOCUMENT` for indexing and `RETRIEVAL_QUERY` for search is a free accuracy win **on
`gemini-embedding-001`**. `gemini-embedding-2` **removes `task_type`** in favour of instructions in
the text — so do this only if we stay on `-001`. Decide the model first (§9b), then this item.

### 4.4 Kill the per-query subprocess `[ ]`
`server.js:60` spawns a Python process per search, and `search_vectors.py:41` reads the **32 MB
FAISS index from disk on every single query**. 2,670 × 3072 floats is ~32 MB — it fits in Node's
memory trivially, and a matrix multiply removes the subprocess, the 10 s timeout, and the cold-read
latency entirely.

- [ ] Load vectors once at boot; do cosine similarity in-process
- [ ] Remove the subprocess plumbing and the viewport reconstruct loop (`search_vectors.py:50`)

### 4.4b Retrieval is quality-blind — tiers don't influence ranking `[x]` — done 2026-08-09

**Fixed by blended re-ranking.** `search_vectors.py` now returns a wider pool (40) with a
similarity score per candidate; `server.js` re-ranks on
`similarity + TIER_INFLUENCE * awardWeight()` and trims to 20 before the model sees them.

`TIER_INFLUENCE = 0.12` — deliberately small. The requirement written here before building was
*"a Vetted place that nails the dish should still beat a 1-heart place that's only loosely
related,"* and the measured result honours it: 이구역의요리왕 (Neon Vetted) still holds a top-5 slot
for 제육볶음 because it is a strong semantic match.

| Query | Before | After |
|---|---|---|
| `제육볶음 맛집` | 18 Vetted / 2 hearts | **14 Vetted / 6 hearts**; top 4 are all heart-holding 제육볶음 |
| `special occasion fine dining` | 1 starred restaurant | **2 Stars + two 1 Stars promoted in**; RIBBON_TWO 6 → 12 |

6 of the 8 heart-holding 제육볶음 places now reach the pool (was 2). Backwards compatible: a
legacy bare-vector payload still works, and `server.js` tolerates the old bare-id array so a
stale `search_vectors.py` can't take the endpoint down.

<details><summary>Original finding (superseded)</summary>

**Found 2026-08-09, immediately after 2.3 made tiers meaningful.** Measured on the live index:

> Query `제육볶음 맛집` → FAISS top-20 = **18 `NEON_VETTED` + 2 `NEON_1`**.
> Of the 8 제육볶음 places holding a heart, only **2 reach the candidate pool at all**, and
> neither lands in the top 5.

FAISS ranks by semantic similarity only. Nothing in the vector encodes how *good* a place is,
so a dish query returns whatever is textually closest to the dish — not the best examples of it.

**This is not a regression from 2.3/4.2** — removing the tier phrase from descriptions changed
what clusters, not what ranks; a dish query never ranked on quality. But it *matters* now in a
way it didn't before, because the tier finally carries meaning worth surfacing.

**Partial mitigation already in place:** `server.js::toRow` passes `award` to Gemini, which
picks the final 1–3 — so the LLM *can* prefer a higher tier. But it can only choose from what
FAISS surfaced, and an 18/20 Vetted pool caps how much that helps.

- [ ] Blend tier into candidate ranking (rank by similarity **and** tier, not similarity alone)
- [ ] Re-test the same query — heart-holders should reach the pool without crowding out
      genuinely better semantic matches
- [ ] ⚠️ Do **not** overcorrect into "hearts always win": a Vetted place that nails the dish
      should still beat a 1-heart place that's only loosely related

</details>

### 4.5 Add hybrid retrieval + reranking `[ ]`
Pure dense retrieval misses exact dish and restaurant names. Add BM25/lexical over Korean names,
dish terms, and neighbourhoods; fuse with vector scores; then rerank the top ~50 with an LLM pass
before answering. This is where the biggest quality jump lives once 4.1–4.4 are done.

### 4.6 Restore the evaluation harness `[ ]`
`automated_ndcg_evaluator.py`, `run_averaged_eval.py`, and `diagnose_regressions.py` exist but are
uncommitted and last ran in March (`ndcg_eval_run.log`). Every change in this phase should be
measured, not eyeballed.

- [ ] Commit the eval scripts
- [ ] Build a fixed query set with expected results
- [ ] Record a baseline NDCG **before** changing retrieval, and after each change

---

## 7. PHASE 5 — Coverage and evidence quality (P2)

### 5.1 Fix the coverage skew `[ ]`
43 of ~467 dong. 치킨 is 327 entries (21%); whole districts are absent.

- [ ] Map current coverage by gu/dong and publish the gap list
- [ ] Expand `NEIGHBORHOODS` in `master_agent.py:27-30` (currently just 청담동, 삼성동)
- [ ] Rebalance `KEYWORDS` (`master_agent.py:33-44`) — add per-keyword caps so one dish can't dominate
- [ ] Add underrepresented categories: 한정식, 백반, 노포, 수산물, 만두, 국수, 전, 막걸리, 커피

### 5.2 Upgrade the evidence base `[ ]`
Naver blogs are our only evidence source and are heavily sponsored — and we couldn't even measure
sponsorship for half the guide. **영수증 리뷰 (receipt-verified reviews) on Naver/Kakao Place are the
higher-signal source** and map directly onto the "vetted by the people" promise.

- [ ] Make receipt reviews the **primary** evidence, blogs secondary
- [ ] Pull review count and average rating as a popularity/consistency prior
- [ ] Weight recent reviews more heavily than old ones
- [ ] Reject any restaurant whose evidence is >X% sponsored outright, rather than capping its score

### 5.3 Add hours and closures `[ ]`
We have no hours data at all — `server.js:220` is a prompt-level apology for it. Kakao's place
detail endpoint has opening hours and regular closing days.

- [ ] Ingest hours, closed days, and break times
- [ ] Add them to popups, and let the AI actually answer "open now?"

---

## 7b. PHASE 5.5 — Dual-language integrity (P1)

*Goal: a Korean speaker and an English speaker each get a complete, native experience.*
*Audited 2026-08-09. The data layer is in good shape; the **presentation layer ignores it**.*

**What's already right:** `name_ko` 100%, `description_ko` 99.9% (2 missing), `address_ko` 100%
on Michelin and Blue Ribbon. Fixing `load_raw()` in 1.2 made this better — Ghostwriter-authored
native Korean is now preserved instead of being re-translated from English.

> **5.5.1–5.5.3 done 2026-08-09.** `description_ko` now renders directly (no `/translate`
> round trip, and expand/collapse works in Korean — the KR branch previously had no expand
> button and `toggleDesc` would have expanded to the *English* text). Titles lead with the
> reader's language. Neon `address_ko` went **0% → 99%**: `_backfill_address_from_kakao()`
> in `build_map_list.py` copies Kakao's official `road_address_name`, which was already
> sitting unused in the ledger for 1,217 of 1,228 entries. The address is now shown in the
> popup at all — it never was before. **5.5.4 (bilingual `cuisine`) is still open.**

### 5.5.1 The frontend doesn't use the Korean it already has `[x]`
In KO mode `renderPopup()` (`app.js:~300`) throws away `p.description_ko` and instead renders a
`translating…` spinner while calling `POST /translate` over the network. The server answers from a
geojson-derived cache — so we do a **round trip to fetch a string that was already in the browser**.

- [ ] Render `p.description_ko` directly in KO mode. Removes the spinner, the latency, and the
      per-popup request; works offline; drops `/translate` to a true fallback for unknown text.

### 5.5.2 Korean users read a romanised headline `[ ]`
`titleHtml` always shows `p.name` (English/romanised) as the title with `name_ko` as small grey
subtitle — **regardless of `currentLang`**. A Korean user opening 봉산옥 sees "Bongsanok" as the
headline. Note 100% of Michelin and 50% of Blue Ribbon names are Latin-script.

- [ ] Swap primary/secondary by `currentLang` so KO shows 봉산옥 with *Bongsanok* underneath.

### 5.5.3 Neon has no Korean address at all `[ ]`
`load_neon_guide()` (`build_map_list.py:597`) hardcodes `address=None, address_ko=None`, so
**1,531 pins — 60% of the map — carry no address in either language.** Beyond the UI, `server.js:118`
feeds `address_ko || address` to the AI, so the model gets no location for most of the guide. This
is a second, independent cause of the location weakness that 4.1 addresses.

- [ ] Populate neon addresses from the Kakao ledger (already resolved during enrichment)
- [ ] Show the address in the popup — it currently renders cuisine/price/phone only

### 5.5.4 `cuisine` is whatever language the source used `[x]` — done 2026-08-09

**Fixed via a cached bilingual lookup — `build_cuisine_map.py` → `data/cuisine_map.json`.**

Only ~437 distinct cuisine values exist across 2,267 restaurants (labels repeat heavily), so
translating distinct values once and caching costs 9 API calls instead of thousands, and makes
every rebuild free. Same pattern as `data/translation_cache.json`. Idempotent — re-running only
translates values it hasn't seen.

`build_map_list.py::apply_cuisine_map()` then normalises `cuisine` to English and fills
`cuisine_ko`, falling back to the original string in both languages if a value is unmapped, so
a gap degrades to the old behaviour rather than producing an empty label.

| source | before | after |
|---|---|---|
| michelin | 200 English, 0 Korean | **100% English `cuisine` + 100% Korean `cuisine_ko`** |
| blueribbon | mostly Korean | same |
| neon | 1,228 Korean, 0 English | same |

Samples: `Barbecue ↔ 바비큐`, `Jeyuk-bokkeum ↔ 제육볶음`, `Naengmyeon ↔ 냉면` (romanised Korean
dish names preserved rather than over-translated), `한식(육류), 돼지갈비 → Korean (Meat), Pork Ribs`.

**Also fixed the embedding side.** `build_embeddings.py` builds a Korean `rich_text` but was
splicing in the raw `cuisine`, so ~200 Michelin restaurants had an English fragment
(`카테고리: Barbecue`) inside otherwise-Korean embedded text. Now uses `cuisine_ko`.

**Future-proofed against new dishes and re-scrapes.** Three things had to be true, and the
first two were wrong on the first attempt:

1. **`build_cuisine_map.py` reads the build INPUTS**, not `places.geojson`. Reading the output
   left it permanently one build behind — a new dish keyword would only become visible after it
   had already shipped untranslated. It now reads `neon_guide_audited_final.csv`'s `Category`
   column and `data/raw/*.csv`, so a new value is translated *before* the build consumes it.
   Verified: 25 values are discoverable from source files that do not exist on the built map.
2. **The map is idempotent.** After a build, `cuisine` holds the English form, so the next run
   saw "Chicken" as a brand-new value and the map grew every time (437 → 884). `expand_aliases()`
   registers both language forms as keys pointing at the same pair, so re-runs cost nothing.
   Verified: consecutive runs report `needing translation: 0`.
3. **It runs automatically.** New `PHASE 4.75` in `master_agent.py`, before the map build.
   Idempotent, so an unchanged sweep makes no API calls.

And if something still slips through, `apply_cuisine_map()` now **prints a loud warning naming
the unmapped values** instead of silently falling back — silent fallback is precisely how Korean
text would end up in the English field again.

**Phase 5.5 is complete.** Toggling to KR no longer leaks English into a pin's title,
description, cuisine, or address, and no `/translate` call fires for a restaurant in the dataset.

<details><summary>Original problem statement (superseded)</summary>
Michelin cuisine is English (`Barbecue`), Blue Ribbon and Neon are Korean (`디저트/스위트`, `치킨`).
The popup's 🍴 line is mixed-language for every user regardless of the toggle. This string is also
embedded into the search vector, so it's a retrieval issue too.

- [ ] Normalise to a bilingual `cuisine` / `cuisine_ko` pair
- [ ] Audit the rest of the UI for untranslated strings (badges, tier names, filter pills)

</details>

**Still open in 5.5:** tier names and filter pill labels are English-only in both modes
("Neon Vetted", "Selected", "Bib"). Lower priority than the data-level fixes above, but it is
the last English leak in KR mode.

**Acceptance criteria:** toggle to KR and no English leaks into a pin's title, description, cuisine,
or tier label; no `/translate` call fires for a restaurant already in the dataset.

---

## 8. PHASE 6 — Product and trust surface (P3)

- [ ] **Show our reasoning.** The score breakdown (ingredients/technique/experience/value/consistency)
      already exists in the data and is invisible in the UI. It is the most trust-building thing we have.
- [ ] **Disclose AI authorship.** Michelin and Blue Ribbon descriptions are written by our ghostwriter
      (`enrich_guides.py`, `generate_guide_descriptions.py`), not by those guides. Say so.
- [ ] **Replace the Google tile source.** `app.js:82-83` pulls tiles directly from `mt0.google.com`,
      outside Google Maps ToS. Harmless at zero traffic, a real liability with growth.
      Move to MapTiler / Mapbox / OSM.
- [ ] Fix the 254 pins with award `"None"` or blank — they should carry the base tier (see 2.3)
- [ ] Answer the open UX questions in `game_plan.txt:75-80` (mobile layout, iOS Kakao login,
      error states, is the three-layer system self-explanatory, is there a reason to return)
- [ ] Offline caching — `site/sw.js` is a stub that just passes requests through
- [ ] Record the demo video (`game_plan.txt:30`)

---

## 9. PHASE 7 — Engineering hygiene (P3)

- [ ] **Shrink the repo.** `.git` is 296 MB because a 4.7 MB geojson and a 32 MB FAISS index are
      committed on every patch, 126 times. Move to Git LFS or generate the index at deploy time.
- [ ] **Write a real `.gitignore`.** It is currently one line (`**/.env`); `node_modules/`, `.venv/`,
      `__pycache__/`, and `*.log` are all untracked-but-not-ignored.
- [ ] **Fix README drift.** It claims 768-dim embeddings (actually 3072, `build_embeddings.py:113`)
      and documents `dedupe_master.py` as a live pipeline stage (it is dead code).
- [ ] **Rotate credentials.** `soul-food-api/.env` is correctly gitignored — confirm no key ever
      landed in history across 126 commits.
- [ ] **Add tests.** There are none. Minimum: a data-integrity suite asserting no duplicate
      `kakao_id`, no pin outside the Seoul bbox, every pin has a `vector_id`, tier distribution
      within expected bounds.
- [ ] Make the pipeline resumable and idempotent — currently a mid-run failure leaves partial state
      across a dozen CSVs.
- [ ] Consolidate the CSV sprawl (`neon_guide_review_queue`, `neon_guide_audited_final`,
      `needs_human_attention`, `ready_for_map_import`, `skip_cache`, `..._rescored`) into a
      single store — SQLite or the existing Supabase instance.

---

## 9b. Model and API strategy (researched 2026-08-09, live sources)

### Where the project stands
| We use | Status | Notes |
|---|---|---|
| `gemini-2.5-flash-lite` (analyst, categories) | Still free-tier | Two generations old |
| `gemini-2.5-flash` (critic, auditor, translate) | Still free-tier | Two generations old |
| `gemini-embedding-001` (3072-dim) | Still free-tier | Superseded |

Nothing is broken and nothing needs an emergency migration. But the free tier tightened —
**Pro-class models left the free tier on 2026-04-01** (Flash and Flash-Lite still qualify, which is
all we use), and rate limits are now per-project rather than published, so read the live number in
AI Studio rather than trusting any blog.

### The one upgrade that clearly matters: `gemini-embedding-2` `[ ]`
This is a **bilingual Korean/English** product, so a top-of-leaderboard multilingual embedding model
is squarely on our critical path.

| | `gemini-embedding-001` (now) | `gemini-embedding-2` |
|---|---|---|
| Multilingual | good | **MTEB-multilingual leader, 100+ languages** |
| Max input | 2,048 tokens | **8,192** |
| Dimensions | 3072 used | 768 recommended — *near-peak quality at ¼ the storage* |
| Task types | `RETRIEVAL_DOCUMENT` / `_QUERY` | **removed** — put the instruction in the text |
| Free tier | yes | yes; Batch API at 50% |

At **768 dims the index drops ~32 MB → ~8 MB**, which simultaneously fixes the repo bloat (7.1) and
the 32 MB-per-query disk read (4.4). Embedding spaces are **incompatible** — this is a full re-embed,
so bundle it with 4.1 (add location) and 4.2 (drop tier boilerplate) into **one** re-embed, not three.

- [ ] ⚠️ **Supersedes 4.3.** Only add `task_type` if we stay on `-001`; embedding-2 removes it.
- [ ] Re-embed once, with location added, tier boilerplate removed, at 768 dims
- [ ] Benchmark old vs new on the 4.6 eval set before committing — *measure, don't assume*

### Where each piece of AI work should run

The useful split is **batch/offline** (can be Claude, in-session) vs **runtime** (must be an API).

| Work | Best home | Why |
|---|---|---|
| Tier calibration, anchor set, rubric design (2.3) | **Claude, in session** | High judgment, low volume — the best possible use of the subscription |
| Reviewing/spot-checking AI output | **Claude, in session** | Exactly what a subscription is for |
| Re-tiering by rank (2.3) | **No LLM at all** | Pure arithmetic on scores already on disk |
| Sponsorship detection | **No LLM at all** | `critic_agent.py:167` already lists the literal phrases (협찬, 소정의 원고료, 제공받아) — a regex is more reliable *and* free, and would have covered the 51% of rows with no ratio |
| Red-flag keyword pass | **Mostly regex**, LLM for ambiguity | Same reasoning |
| Deduplication | **Already no LLM** | kakao_id — done in 1.2 |
| Bulk scoring 1,500+ restaurants | **API** (Gemini free tier, or Anthropic Batch at 50%) | Too much volume for in-session work |
| Guide descriptions at scale | **API** | Same |
| **Embeddings** | **Must stay Gemini** (or Voyage/local) | **Anthropic has no embedding model** — this is the hard constraint |
| Live `/chat` endpoint | **Must be an API** | Runs at user request time |

**The subscription boundary, stated plainly:** work I do *inside this session* is covered by the
Claude Code subscription. A Python script calling the Anthropic API needs a separate API key with
separate per-token billing (Opus 5 $5/$25 per MTok; Haiku 4.5 $1/$5; Batch API −50%). So "use Claude
instead of Gemini" is a real saving for **judgment-heavy, low-volume** work and a real *cost* for
**high-volume batch** work that Gemini currently does free.

**The highest-value item in this table is the cheapest one:** moving sponsorship detection to regex
costs nothing, removes an LLM call per restaurant, and fixes a 51% data gap that no model upgrade
addresses.

- [ ] Move sponsorship + red-flag detection to deterministic matching, LLM only for ambiguity
- [ ] Consider a local model (Ollama — already a project dependency) for high-volume grunt work
- [ ] Keep embeddings on Gemini; evaluate Voyage/local only if we leave Google entirely

---

## 10. Explicitly out of scope for now

Recorded so they don't get silently forgotten:

- Personalisation / the DeepFM model hinted at in `interaction_logs_schema.sql`
- Expansion beyond Seoul
- Native mobile apps
- Any monetisation or restaurant-facing product
- User-generated reviews or ratings

---

## 11. Changelog

| Date | Change |
|---|---|
| 2026-08-09 | Document created. Full audit of live data, pipeline, and frontend completed. |
| 2026-08-09 | **1.1 done** — `repair_guide_tiers.py` added. 36 Michelin stars and 2 RIBBON_THREE restored; 4 Washington DC rows removed; Michelin `cuisine`/`price` repaired; 843 Blue Ribbon cuisines backfilled. |
| 2026-08-09 | **1.2 done** — `merge_duplicate_places()` added to `build_map_list.py`; ledger enrichment moved before dedupe; `awards[]` added to `Place`. 2,670 → 2,566 pins, 104 now multi-guide, zero `kakao_id` collisions. Also fixed missing `load_dotenv` and `load_raw()` discarding `description_ko`. |
| 2026-08-09 | **1.3 done** — `dedupe_master.py` and `ready_for_map_import.csv` deleted; removed from `master_agent.py`; README renumbered and corrected. |
| 2026-08-09 | FAISS index rebuilt: **2,566 vectors / 3072-dim, contiguous `vector_id` 0–2565, exactly matching the 2,566 map features.** 0 translation API calls (129 cache hits). Retrieval smoke-tested in both Korean and English. |
| 2026-08-09 | Wrote a real `.gitignore` (was one line). New item **1.4** logged: `enrich_places_with_ledger` discards scraped coordinates because CSV lat/lon load as strings. |
| 2026-08-09 | Added **1.5** (scraper future-proofing — validate-then-promote, so a re-scrape can't silently undo 1.1) and **Phase 5.5** (dual-language integrity: frontend ignores the Korean it already has; Neon has no address in either language). |
| 2026-08-09 | Added **§9b** model/API strategy from live sources: `gemini-embedding-2` is the one clear upgrade (768 dims → ~8 MB index); Anthropic has **no** embedding model; sponsorship detection should be regex, not an LLM call. |
| 2026-08-09 | **2.1 done** — auditor verdict is now binding. Guide 1,531 → 1,228; map 2,566 → **2,267 pins**; zero live `Rating Justified = No`. Also fixed a second data-loss bug: quarantine writes clobbered the file every run. |
| 2026-08-09 | **Sponsorship detection made deterministic** (`critic_agent.py`): phrase-list counting + cap enforced in Python. ⚠️ **Future sweeps only** — blog text is never persisted, so the existing 786 blank ratios need a re-scrape (Phase 3). |
| 2026-08-09 | **5.5.1–5.5.3 done** — KR renders local `description_ko` (no `/translate` round trip), titles lead with reader's language, Korean expand/collapse fixed, Neon `address_ko` 0% → 99%, address shown in popup. |
| 2026-08-09 | FAISS rebuilt: **2,267 vectors, contiguous `vector_id` 0–2266, matching 2,267 features.** 0 translation API calls. Retrieval smoke-tested EN + KO. |
| 2026-08-09 | **Diff report decision:** user-facing "what changed" feature **shelved** (clutter risk). Internal pre-promotion scrape diff **retained** in 1.5 as a safety check only. |
| 2026-08-09 | **2.3 analysis done — recommendation reversed.** Per-dish ranking was recommended on a wrong assumption; measured data shows 치킨 is *under*-represented at the top (9% at ≥95 vs 24% of guide). **Global thresholds now recommended.** Awaiting decision. |
| 2026-08-09 | **2.3 + 2.4 + 4.2 shipped.** Global thresholds decided by Alexander. Neon re-tiered: 14 / 71 / 174 / **969 Neon Vetted** (was 97 / 571 / 608 / 254). Tier moved to a real `tier` field; emoji boilerplate removed from descriptions; filter pills relabelled; `passes()` else-if chain fixed so merged pins honour every guide's tier filter. |
| 2026-08-09 | Noted for later: dish-mix statistics in this doc are **confounded by which keywords were searched** and must not be read as evidence about Seoul's cuisine. |
| 2026-08-09 | FAISS rebuilt (3rd time today): 2,267 vectors, contiguous, zero boilerplate, 0 translation API calls. Map + index consistent and deployable. |
| 2026-08-09 | New item **4.4b** logged from a post-retier smoke test: retrieval is quality-blind (18/20 Vetted for a dish query; only 2 of 8 heart-holders reach the pool). Pre-existing, but newly consequential. |

---

## 12. State of the working tree (for whoever picks this up)

**Phase 1 is complete except 1.4 (coordinate trust) and 1.5 (fresh re-scrape).**

Changed and **not yet committed or deployed** — deploying is a deliberate decision, see below:

| File | Change |
|---|---|
| `repair_guide_tiers.py` | **new** — idempotent tier/cuisine/price repair, writes `.bak` backups |
| `build_map_list.py` | `merge_duplicate_places()`, `awards` field, `load_dotenv`, `load_raw` fixes |
| `master_agent.py` | dead PHASE 4 (dedupe_master) removed |
| `dedupe_master.py` | **deleted** |
| `README.md` | pipeline renumbered, 3072-dim corrected, ship-together warning added |
| `.gitignore` | rewritten |
| `site/places.geojson` | rebuilt — 2,566 pins |
| `data/restaurant_vectors.index` | rebuilt — 2,566 vectors |
| `data/raw/*.csv` | repaired (`.bak` backups alongside) |

Backup of the pre-overhaul map now lives at
`backups/places.geojson.2026-04-23.pre-overhaul.json` (moved out of the temp scratchpad).

**Before deploying, decide:** ship Phase 1 now (stars and de-duplicated pins go live, but the
Neon tiers are still inflated and 303 auditor-rejected restaurants are still on the map), or
hold until Phase 2 lands the trust fixes. Shipping now is defensible — it strictly improves the
two established guides — but it does not yet make the "vetted" promise true.

**Note:** `data/raw/` is untracked, so the build inputs are not version-controlled. Worth fixing.
