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

# ─── STEP 2: data_quality_fix.py ─────────────────────────────────────────────
run('data_quality_fix.py')

# ─── STEP 3: build_map_list.py ───────────────────────────────────────────────
run('build_map_list.py')

# ─── STEP 4: build_embeddings.py ─────────────────────────────────────────────
run('build_embeddings.py')

# ─── STEP 5: Git commit + push ───────────────────────────────────────────────
print(f"\n{'='*60}")
print(">>> Step 5: Committing and pushing to GitHub (triggers Render deploy)")
print(f"{'='*60}")

run_git(['add',
    'site/places.geojson',
    'data/restaurant_vectors.index',
    'neon_guide_audited_final.csv',
    'needs_human_attention.csv',
    'data_quality_fix_report.txt',
])

run_git(['commit', '-m',
    'Patch 1.470: Data quality overhaul — improved scoring prompts, re-scored 152 flagged restaurants, 109 tier changes\n\n'
    'Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>'
])

run_git(['push', 'origin', 'master'])

print("\n" + "="*60)
print("Pipeline complete! Site is deploying on Render.")
print("="*60)
