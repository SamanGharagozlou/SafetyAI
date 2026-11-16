#!/usr/bin/env python3
"""Export the pitch deck to PDF (one 1920x1080 page per slide) with headless Chrome or Edge.

No Python packages and no Node needed, unlike export_pdf.js (Playwright).
Usage: python3 pitch_deck/tools/export_pdf.py [out.pdf]
Set CHROME=/path/to/chrome if it is not found automatically.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DECK = Path(__file__).resolve().parent.parent / "index.html"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else DECK.parent / "sepazu_pitch.pdf").resolve()

CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    shutil.which("microsoft-edge") or "",
]
chrome = next((c for c in CANDIDATES if c and Path(c).exists()), None)
if not chrome:
    sys.exit("Chrome or Edge not found; set CHROME=/path/to/browser")

# The deck's print CSS lays out one slide per 1920x1080 page; the time budget lets fonts and images load.
subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                "--run-all-compositor-stages-before-draw", "--virtual-time-budget=8000",
                f"--print-to-pdf={OUT}", DECK.as_uri()],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

pages = len(re.findall(rb"/Type\s*/Page[^s]", OUT.read_bytes()))
print(f"wrote {OUT} ({pages} pages, {OUT.stat().st_size // 1024} KB)")
