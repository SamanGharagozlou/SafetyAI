"""Calibrate the floor_transition detector (perception/boundary.py): per-class floor colour histograms from venue
frames. No floor tape needed: you label patches of the floors that are already there.

Frames come from the walkthrough video (every --every-s seconds) and/or the stills in docs/venue/.

Interactive (a desktop session):
    .venv/bin/python scripts/calibrate_floor.py --video assets/venue/spa_walkthrough.mp4 --stills docs/venue
    For each frame: press a class key, then drag a box over that floor, Enter to accept, repeat; n = next frame.
      1 concourse_tile   2 wood   3 terracotta   4 checker_tile   5 wet_deck   q = finish
Headless (labels file, e.g. made on a laptop):
    .venv/bin/python scripts/calibrate_floor.py --labels perception/floor_labels.json
    floor_labels.json: {"<image or video@seconds>": [["wood", x, y, w, h], ...], ...}
Writes perception/floor_classes.json (path with --out). Check it with --check <image>: prints the strip classes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2

from perception.boundary import FLOOR_CLASSES, FloorModel, FloorTransitionDetector
from perception.camera import Camera

KEYS = {ord(str(i + 1)): c for i, c in enumerate(FLOOR_CLASSES)}


def frames(video: str | None, stills: str | None, every_s: float):
    if video and Path(video).exists():
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(fps * every_s))
        i = 0
        while True:
            ok, f = cap.read()
            if not ok:
                break
            if i % step == 0:
                yield f"{video}@{i / fps:.1f}", f
            i += 1
    if stills and Path(stills).is_dir():
        for p in sorted(Path(stills).glob("*")):
            if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
                img = cv2.imread(str(p))
                if img is not None:
                    yield str(p), img


def load_frame(key: str):
    if "@" in key and not Path(key).exists():
        path, t = key.rsplit("@", 1)
        cap = cv2.VideoCapture(path)
        cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
        ok, f = cap.read()
        return f if ok else None
    return cv2.imread(key)


def interactive(args) -> dict:
    samples: dict = {c: [] for c in FLOOR_CLASSES}
    labels: dict = {}
    for key, img in frames(args.video, args.stills, args.every_s):
        cls = FLOOR_CLASSES[0]
        while True:
            view = img.copy()
            cv2.putText(
                view,
                f"class: {cls}  (1-5 class, b box, n next, q quit)",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (0, 255, 0),
                2,
            )
            cv2.imshow("calibrate_floor", view)
            k = cv2.waitKey(0) & 0xFF
            if k in KEYS:
                cls = KEYS[k]
            elif k == ord("b"):
                x, y, w, h = cv2.selectROI("calibrate_floor", img, showCrosshair=False)
                if w > 4 and h > 4:
                    samples[cls].append(img[y : y + h, x : x + w].copy())
                    labels.setdefault(key, []).append([cls, int(x), int(y), int(w), int(h)])
            elif k == ord("n"):
                break
            elif k == ord("q"):
                cv2.destroyAllWindows()
                return {"samples": samples, "labels": labels}
    cv2.destroyAllWindows()
    return {"samples": samples, "labels": labels}


def from_labels(path: str) -> dict:
    labels = json.loads(Path(path).read_text())
    samples: dict = {c: [] for c in FLOOR_CLASSES}
    for key, boxes in labels.items():
        img = load_frame(key)
        if img is None:
            print(f"skip {key}: cannot read")
            continue
        for cls, x, y, w, h in boxes:
            samples[cls].append(img[y : y + h, x : x + w].copy())
    return {"samples": samples, "labels": labels}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", default="assets/venue/spa_walkthrough.mp4")
    ap.add_argument("--stills", default="docs/venue")
    ap.add_argument("--every-s", type=float, default=3.0)
    ap.add_argument("--labels")
    ap.add_argument("--out", default="perception/floor_classes.json")
    ap.add_argument("--no-go", default="wet_deck", help="comma-separated floor classes that mean 'leaving the go zone'")
    ap.add_argument("--check")
    a = ap.parse_args()
    if a.check:
        m = FloorModel.load(ROOT / a.out)
        det = FloorTransitionDetector(m, Camera())
        img = cv2.imread(a.check)
        for row, cls, d in det.strip_classes(img):
            print(f"row {row:4d}  {cls:15s} {d:.2f}")
        print(det.detect(img))
        return
    got = from_labels(a.labels) if a.labels else interactive(a)
    samples = {k: v for k, v in got["samples"].items() if v}
    if not samples:
        sys.exit("no labelled patches: nothing to calibrate")
    model = FloorModel.fit(
        samples,
        no_go=tuple(c.strip() for c in a.no_go.split(",")),
        meta={"patches": {k: len(v) for k, v in samples.items()}},
    )
    model.save(ROOT / a.out)
    Path(ROOT / a.out).with_name("floor_labels.json").write_text(json.dumps(got["labels"], indent=1))
    print(f"saved {a.out}: {model.meta['patches']}")


if __name__ == "__main__":
    main()
