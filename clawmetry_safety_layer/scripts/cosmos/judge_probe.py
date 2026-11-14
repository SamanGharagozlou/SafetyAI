"""Send frames to the Cosmos judge exactly as the edge does (<= 640x360 JPEG, go-zone text, intended action) and print
the verdict and latency per frame, plus a summary (cold first call, warm p50 / p90 / max).

    .venv/bin/python scripts/cosmos/judge_probe.py                       # docs/venue/*.jpg
    .venv/bin/python scripts/cosmos/judge_probe.py --images 'docs/isaac_*.jpg' --repeat 3 --json out.json
COSMOS_URL (default http://127.0.0.1:8000/v1). Nothing is stored except what --json writes (verdicts, no frames).
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import cv2

from brain.relay import CosmosJudge
from bridge import load_config
from leukerbad_g1 import settings
from perception.sampler import to_jpeg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", default="docs/venue/*.jpg")
    ap.add_argument("--action", default="forward 2 m at 0.3 m/s toward bath_entrance")
    ap.add_argument("--repeat", type=int, default=1, help="send each frame N times (cache off between repeats)")
    ap.add_argument("--json")
    a = ap.parse_args()
    cfg = load_config("config/d1w.yaml")
    go = cfg["safety"]["go_zone_description"]
    url = settings.get("COSMOS_URL", "http://127.0.0.1:8000/v1")
    files = sorted(glob.glob(str(ROOT / a.images))) or sorted(glob.glob(a.images))
    if not files:
        sys.exit(f"no images match {a.images}")
    rows, lat = [], []
    for rep in range(a.repeat):
        judge = CosmosJudge(url, settings.get("COSMOS_MODEL", "nvidia/Cosmos-Reason2-2B"))  # fresh cache per repeat
        for f in files:
            img = cv2.imread(f)
            if img is None:
                continue
            jpeg = to_jpeg(img)
            t0 = time.time()
            v = judge.judge(jpeg, go, a.action)
            dt = time.time() - t0
            lat.append(dt)
            v.pop("frame_hash", None)  # cache key only; not worth publishing
            rows.append(
                {"file": str(Path(f).name), "repeat": rep, "bytes": len(jpeg), "latency_s": round(dt, 3), "verdict": v}
            )
            ok = (
                "UNAVAILABLE " + v.get("error", "")
                if v.get("judge_unavailable")
                else (
                    f"leaves={v['leaves_go_zone']} stop={v['stop']} people={v['people_within_2m']} conf={v['confidence']:.2f} hazards={v['hazards']} | {v['reason']}"
                )
            )
            print(f"{dt:6.2f} s  {Path(f).name:40s} {ok}")
    warm = lat[1:] if len(lat) > 1 else lat
    summary = {
        "url": url,
        "frames": len(lat),
        "cold_first_s": round(lat[0], 3),
        "warm_p50_s": round(statistics.median(warm), 3),
        "warm_p90_s": round(sorted(warm)[max(0, int(0.9 * len(warm)) - 1)], 3),
        "warm_max_s": round(max(warm), 3),
        "unavailable": sum(1 for r in rows if r["verdict"].get("judge_unavailable")),
    }
    print(json.dumps(summary))
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
