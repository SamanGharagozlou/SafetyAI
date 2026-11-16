"""Export robot sessions as action-labelled clips for Cosmos post-training.

Input:
* the ClawMetry robot-session journal(s) (`~/.clawmetry/robotics/journal/<run_id>.jsonl`, schema clawmetry.robotics.v1)
* a RECORDED camera: a video file plus its timeline.
  * For the mock this is the venue walkthrough (`--video`); the timeline comes from the journal's `camera.timeline`
    events (loop / freeze / play with wall times).
  * For a real robot run, recorded with the venue's permission only (docs/PRIVACY.md), pass `--video` and
    `--camera-start <epoch s>` (wall time of the video's first frame).

Output (`--out`, default `datasets/cosmos_d1w/`, git-ignored: footage of guests never goes into git):
* `train/videos/<clip>.mp4`: the camera during one motion turn (navigate_to leg / recover), 5 fps, at most 640 px wide.
* `train/annotation/<clip>.json`: for cosmos-predict action-conditioned post-training (# VERIFY the exact keys against
  the cosmos-framework version you train with). Contents:
  * `{"video_path", "fps", "num_frames"}`;
  * `"action"`: per frame `[vx, vy, wz]`, the commanded `cmd_vel` reconstructed from the gate's `turn.command` segments;
  * `"state"`: per frame `[x, y, yaw]` in the zone frame, from the 1 Hz observations, sample-and-hold;
  * `"outcome"`: ok / failed / blocked_by_policy / held, with the message;
  * `"skill"`, `"args"`, `"zones"`, `"verdicts"` (Cosmos verdicts in the window), `"fsm"` (transitions in the window),
    `"labels"` (the governance label below).
* `reason2_sft.jsonl`: one line per clip for Cosmos-Reason2 SFT in the LLaVA conversation layout, with the video, the
  judge prompt for the commanded action, and the answer derived from what governance decided:
  * `leaves_go_zone` is true if L1 / L2 / an operator stopped it at the go-zone edge;
  * `stop` is true for any HOLD;
  * `people_within_2m` comes from L2.
  These are the labels the judge should have produced (docs/COSMOS.md, training roadmap).
* `manifest.json`: clips, skipped turns and the reason for each skip.

    .venv/bin/python scripts/export_cosmos_dataset.py --journal ~/.clawmetry/robotics/journal/<run>.jsonl \\
        --video assets/venue/spa_walkthrough.mp4
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2

from brain.relay import SCHEMA, SYSTEM, prompt

FPS = 5.0
MOTION_SKILLS = ("navigate_to", "recover")


def load(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        for line in open(p):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            r["_t"] = int(r.get("wall_time_ns", "0")) / 1e9
            rows.append(r)
    rows.sort(key=lambda r: r["_t"])
    return rows


class Timeline:
    """wall time -> position in the recorded video (s), or None when the camera was not recording."""

    def __init__(self, rows: list[dict], duration: float, camera_start: float | None):
        self.duration = duration
        self.ev = [
            (r["_t"], (r.get("data") or {}).get("event"), float((r.get("data") or {}).get("t", 0.0)))
            for r in rows
            if r["kind"] == "camera.timeline"
        ]
        self.ev.sort()
        self.camera_start = camera_start

    def at(self, wall: float) -> float | None:
        if self.camera_start is not None:
            t = wall - self.camera_start
            return t if 0 <= t <= self.duration else None
        i = bisect.bisect_right([e[0] for e in self.ev], wall) - 1
        if i < 0:
            return None
        w0, kind, t0 = self.ev[i]
        if kind == "freeze":
            return t0
        return (t0 + (wall - w0)) % self.duration


def turns(rows: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in rows:
        d = r.get("data") or {}
        tid = d.get("turn_id")
        if not tid:
            continue
        t = out.setdefault(tid, {"turn_id": tid, "commands": [], "t0": None, "t1": None})
        if r["kind"] == "turn.observe":
            t.update(t0=r["_t"], skill=d.get("skill"))
        elif r["kind"] == "turn.decide":
            t["args"] = d.get("args")
        elif r["kind"] == "turn.command" and d.get("type") == "velocity":
            t["commands"].append(
                (r["_t"] - float(d.get("duration_s", 0.0)), r["_t"], d.get("vector_mean") or d.get("vector"))
            )
        elif r["kind"] == "turn.outcome":
            t.update(t1=r["_t"], outcome=d.get("outcome"), message=d.get("message"))
    return [t for t in out.values() if t.get("skill") in MOTION_SKILLS and t["t0"] and t["t1"]]


def governance_label(window: list[dict]) -> dict:
    fsm = [r.get("data") or {} for r in window if r["kind"] == "fsm.transition"]
    held = [f for f in fsm if f.get("to") == "HOLD"]
    edge = any(
        f.get("source") in ("L1", "L2", "operator")
        and ("go zone" in str(f.get("reason")) or "no-go" in str(f.get("reason")))
        for f in held
    )
    people = max(
        [int((r.get("data") or {}).get("people_within_2m") or 0) for r in window if r["kind"] == "person_near"] + [0]
    )
    return {
        "leaves_go_zone": edge,
        "stop": bool(held),
        "people_within_2m": people,
        "held_by": sorted({f.get("source") for f in held}),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", nargs="+", required=True, help="journal files (globs ok)")
    ap.add_argument("--video", required=True)
    ap.add_argument(
        "--camera-start", type=float, help="wall time (epoch s) of the video's first frame (real recordings)"
    )
    ap.add_argument("--out", default=str(ROOT / "datasets" / "cosmos_d1w"))
    ap.add_argument("--max-w", type=int, default=640)
    a = ap.parse_args()
    paths = [p for g in a.journal for p in (glob.glob(g) or [g])]
    rows = load(paths)
    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        sys.exit(f"cannot open {a.video}")
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / (cap.get(cv2.CAP_PROP_FPS) or 25.0)
    tl = Timeline(rows, dur, a.camera_start)
    out = Path(a.out)
    (out / "train" / "videos").mkdir(parents=True, exist_ok=True)
    (out / "train" / "annotation").mkdir(parents=True, exist_ok=True)
    obs = [(r["_t"], (r["data"].get("state") or {})) for r in rows if r["kind"] == "observation"]
    obs_t = [o[0] for o in obs]
    manifest = {"journals": [Path(p).name for p in paths], "video": Path(a.video).name, "clips": [], "skipped": []}
    sft = []
    from bridge import load_config

    go_zone = load_config("config/d1w.yaml")["safety"]["go_zone_description"]
    for tn in turns(rows):
        t0, t1 = tn["t0"], tn["t1"]
        if not tn["commands"]:
            manifest["skipped"].append(
                {"turn": tn["turn_id"], "why": "no velocity command (refused / blocked before motion)"}
            )
            continue
        n = int((t1 - t0) * FPS)
        if n < 3:
            manifest["skipped"].append({"turn": tn["turn_id"], "why": "shorter than 0.6 s"})
            continue
        if tl.at(t0) is None or tl.at(t1) is None:
            manifest["skipped"].append({"turn": tn["turn_id"], "why": "no camera recording covers this turn"})
            continue
        clip = f"{tn['turn_id']}"
        vp = out / "train" / "videos" / f"{clip}.mp4"
        writer = None
        actions, states = [], []
        for i in range(n):
            w = t0 + i / FPS
            vt = tl.at(w)
            cap.set(cv2.CAP_PROP_POS_MSEC, (vt or 0.0) * 1000)
            ok, frame = cap.read()
            if not ok:
                break
            h, wd = frame.shape[:2]
            s = min(1.0, a.max_w / wd)
            frame = cv2.resize(frame, (int(wd * s) // 2 * 2, int(h * s) // 2 * 2))
            if writer is None:
                writer = cv2.VideoWriter(
                    str(vp), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (frame.shape[1], frame.shape[0])
                )
            writer.write(frame)
            act = [0.0, 0.0, 0.0]
            for c0, c1, v in tn["commands"]:
                if c0 <= w <= c1:
                    act = [round(float(x), 3) for x in v]
            actions.append(act)
            j = max(0, bisect.bisect_right(obs_t, w) - 1)
            st = obs[j][1] if obs else {}
            states.append(
                [
                    round(float(st.get("x", math.nan)), 3),
                    round(float(st.get("y", math.nan)), 3),
                    round(float(st.get("yaw", math.nan)), 3),
                ]
            )
        if writer is None:
            manifest["skipped"].append({"turn": tn["turn_id"], "why": "no frames read"})
            continue
        writer.release()
        window = [r for r in rows if t0 <= r["_t"] <= t1 + 1.0]
        before = [r for r in rows if t0 - 10.0 <= r["_t"] < t0 and r["kind"] == "person_near"]  # guests already there
        label = governance_label(window + before)
        verdicts = [
            {
                k: (r["data"] or {}).get(k)
                for k in ("leaves_go_zone", "hazards", "people_within_2m", "stop", "confidence", "reason", "trigger")
            }
            for r in window
            if r["kind"] == "judge_verdict"
        ]
        fsm = [
            {k: (r["data"] or {}).get(k) for k in ("from", "to", "source", "reason")}
            for r in window
            if r["kind"] == "fsm.transition"
        ]
        zones = sorted({str((st or {}).get("zone")) for _, st in obs if t0 <= _ <= t1})
        ann = {
            "video_path": f"videos/{clip}.mp4",
            "fps": FPS,
            "num_frames": len(actions),
            "action": actions,
            "action_space": "cmd_vel [vx m/s, vy m/s, wz rad/s], body frame, as commanded through the gate",
            "state": states,
            "state_space": "[x m, y m, yaw rad], venue zone frame (policies/zones_therme.yaml)",
            "skill": tn.get("skill"),
            "args": tn.get("args"),
            "outcome": {"outcome": tn.get("outcome"), "message": tn.get("message")},
            "zones": zones,
            "verdicts": verdicts,
            "fsm": fsm,
            "labels": label,
        }
        (out / "train" / "annotation" / f"{clip}.json").write_text(json.dumps(ann, indent=1))
        mean = [round(sum(a_[k] for a_ in actions) / len(actions), 2) for k in range(3)]
        action_txt = f"drive at vx {mean[0]} m/s, vy {mean[1]} m/s, turn {mean[2]} rad/s for {len(actions) / FPS:.0f} s"
        answer = {
            "leaves_go_zone": label["leaves_go_zone"],
            "hazards": [],
            "people_within_2m": label["people_within_2m"],
            "stop": label["stop"],
            "confidence": 0.9,
            "reason": f"governance outcome: {tn.get('outcome')}"
            + (f", held by {', '.join(label['held_by'])}" if label["held_by"] else ""),
        }
        sft.append(
            {
                "video": f"train/videos/{clip}.mp4",
                "conversations": [
                    {"from": "system", "value": SYSTEM},
                    {"from": "human", "value": "<video>\n" + prompt(go_zone, action_txt)},
                    {"from": "gpt", "value": json.dumps(answer)},
                ],
            }
        )
        manifest["clips"].append(
            {
                "clip": clip,
                "frames": len(actions),
                "skill": tn.get("skill"),
                "outcome": tn.get("outcome"),
                "labels": label,
            }
        )
    with open(out / "reason2_sft.jsonl", "w") as f:
        for s_ in sft:
            f.write(json.dumps(s_) + "\n")
    manifest["schema_note"] = f"judge answer schema: {SCHEMA}"
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"{len(manifest['clips'])} clips, {len(manifest['skipped'])} skipped -> {out}")
    for c in manifest["clips"]:
        print(f"  {c['clip']}: {c['frames']} frames, {c['skill']} {c['outcome']} labels={c['labels']}")


if __name__ == "__main__":
    main()
