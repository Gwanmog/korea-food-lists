"""
run_pipeline.py

Waits for receipt_auditor.py to finish, then runs the full post-audit pipeline:
  1. final_verdict.py   — quarantine bad entries, produce clean audited_final
  2. data_quality_fix.py — remove revoked/sponsored-cap/dupes in-place
  3. build_map_list.py  — rebuild site/places.geojson
  4. build_embeddings.py — rebuild FAISS index + inject vector_ids
  5. git commit + push  — triggers Render deploy

Run this once before bed. It will block until the auditor is done, then proceed automatically.
"""

import sys
import os
import time
import subprocess
import psutil

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

VENV_PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.venv', 'Scripts', 'python.exe')
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(script_name, extra_args=None):
    """Run a Python script in the project dir and stream output."""
    cmd = [VENV_PYTHON, os.path.join(SCRIPT_DIR, script_name)]
    if extra_args:
        cmd += extra_args
    print(f"\n{'='*60}")
    print(f">>> Running: {script_name}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"\n!!! {script_name} exited with code {result.returncode}. Stopping pipeline.")
        sys.exit(result.returncode)
    return result


def run_git(args, check=True):
    result = subprocess.run(['git'] + args, cwd=SCRIPT_DIR, capture_output=False)
    if check and result.returncode != 0:
        print(f"\n!!! git {' '.join(args)} failed. Stopping.")
        sys.exit(result.returncode)
    return result


def auditor_is_running():
    """Returns True if any python process is running receipt_auditor.py."""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline') or []
            if any('receipt_auditor' in arg for arg in cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


# ─── STEP 0: Wait for auditor ───────────────────────────────────────────────
print("Checking if receipt_auditor.py is still running...")
if auditor_is_running():
    print("Auditor is running. Waiting for it to finish (checking every 60s)...")
    while auditor_is_running():
        time.sleep(60)
    print("Auditor finished! Proceeding with pipeline.")
else:
    print("Auditor already done. Proceeding immediately.")

# Brief pause to let the final CSV write flush to disk
time.sleep(5)

# ─── STEP 1: final_verdict.py ────────────────────────────────────────────────
# We run it against neon_guide_audited_final.csv (not the old queue file)
# Patch the call inline rather than editing the source file
print("\n>>> Step 1: Running final_verdict cleanup...")
verdict_code = """
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(r'{script_dir}')
from final_verdict import supreme_court_audit
supreme_court_audit('neon_guide_audited_final.csv', 'neon_guide_audited_final.csv')
""".format(script_dir=SCRIPT_DIR)

result = subprocess.run([VENV_PYTHON, '-c', verdict_code], cwd=SCRIPT_DIR)
if result.returncode != 0:
    print("!!! final_verdict failed. Stopping.")
    sys.exit(result.returncode)

# ─── STEP 1.5: reclassify.py ─────────────────────────────────────────────────
# Scans quarantine for Reclassify Eligible entries, asks Gemini for true category,
# re-scores under the corrected keyword, and promotes 85+ scorers back into the queue.
# If anything is promoted, re-run receipt_auditor + final_verdict to process them.
print(f"\n{'='*60}")
print(">>> Step 1.5: Running reclassification engine...")
print(f"{'='*60}")

reclassify_code = """
import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
os.chdir(r'{script_dir}')
from reclassify import run_reclassification
promoted = run_reclassification()
sys.exit(0 if promoted == 0 else 1)
""".format(script_dir=SCRIPT_DIR)

reclassify_result = subprocess.run([VENV_PYTHON, '-c', reclassify_code], cwd=SCRIPT_DIR)

if reclassify_result.returncode == 1:
    # Restaurants were promoted — re-run auditor + final_verdict to process them
    print("\n>>> Promotions detected. Re-running receipt auditor on new queue entries...")
    run('receipt_auditor.py')

    print("\n>>> Re-running final_verdict to process newly audited entries...")
    result = subprocess.run([VENV_PYTHON, '-c', verdict_code], cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print("!!! final_verdict (post-reclassify) failed. Stopping.")
        sys.exit(result.returncode)
else:
    print("   No promotions. Continuing.")

# ─── STEP 2: data_quality_fix.py ─────────────────────────────────────────────
run('data_quality_fix.py')

# ─── STEP 3: build_map_list.py ───────────────────────────────────────────────
run('build_map_list.py', ['build'])

# ─── STEP 4: build_embeddings.py ─────────────────────────────────────────────
run('build_embeddings.py')

# ─── STEP 5: Git commit + push ───────────────────────────────────────────────
print(f"\n{'='*60}")
print(">>> Step 5: Committing and pushing to GitHub (triggers Render deploy)")
print(f"{'='*60}")

# ── Build a dynamic commit message ───────────────────────────────────────────
import re as _re
import csv as _csv

def _next_patch():
    """Auto-increment the patch version from the latest git commit."""
    result = subprocess.run(
        ['git', 'log', '--oneline', '-1'],
        cwd=SCRIPT_DIR, capture_output=True, text=True
    )
    m = _re.search(r'Patch ([\d]+)\.([\d]+)', result.stdout)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        return f'{major}.{minor + 1}'
    return 'next'

def _count_csv(filename):
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return 0
    with open(path, encoding='utf-8-sig') as f:
        return sum(1 for _ in _csv.DictReader(f))

def _neighborhoods_this_run():
    """Derive which neighborhoods were swept by diffing the queue against production."""
    queue_path = os.path.join(SCRIPT_DIR, 'neon_guide_review_queue.csv')
    prod_path  = os.path.join(SCRIPT_DIR, 'neon_guide_audited_final.csv')
    if not os.path.exists(queue_path):
        return set()
    prod_names = set()
    if os.path.exists(prod_path):
        with open(prod_path, encoding='utf-8-sig') as f:
            prod_names = {r.get('Restaurant Name','') for r in _csv.DictReader(f)}
    hoods = set()
    with open(queue_path, encoding='utf-8-sig') as f:
        for row in _csv.DictReader(f):
            if row.get('Restaurant Name','') not in prod_names:
                n = row.get('Neighborhood','').strip()
                if n:
                    hoods.add(n)
    return hoods

patch        = _next_patch()
total_clean  = _count_csv('neon_guide_audited_final.csv')
total_quar   = _count_csv('needs_human_attention.csv')
hoods        = _neighborhoods_this_run()
hood_count   = len(hoods)

commit_msg = (
    f'Patch {patch}: Maintenance pass — {hood_count} neighborhoods\n\n'
    f'{total_clean} restaurants in guide | {total_quar} in quarantine\n\n'
    f'Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>'
)
print(f'Commit message:\n{commit_msg}')
# ─────────────────────────────────────────────────────────────────────────────

run_git(['add',
    'site/places.geojson',
    'data/restaurant_vectors.index',
    'neon_guide_audited_final.csv',
    'needs_human_attention.csv',
    'data_quality_fix_report.txt',
])

run_git(['commit', '-m', commit_msg])

run_git(['push', 'origin', 'master'])

print("\n" + "="*60)
print("Pipeline complete! Site is deploying on Render.")
print("="*60)
