"""Five demo beats on the mock D1 Ultra-W (scripts/run_mock.sh must be running; bash scripts/demo_d1w_mock.sh wraps both).

  1. A guest asks a price, then to be taken to the Therme entrance -> concierge answer + guide_to(bath_entrance).
  2. Person-near: the camera shows guests close by -> L2 CAUTION (0.15 m/s) while guiding to the shop.
  3. "Take me to the pool" -> policy block before anything moves + staff-escort approval -> Deny -> alternative.
  4. Staged breach toward the pool deck (an outside motion source, like the team's keyboard teleop) -> stopped by
     L1 (measured motion) + L2 (no-go floor) + L3 (Cosmos verdict) -> Reset (recover into the go zone) -> Approve.
  5. ClawMetry Stop mid-roll (the controller's own /stop, exactly what the Stop button calls) -> Reset.

Writes a timeline (JSON) and a transcript (Markdown) to --out (default docs/demo_d1w_mock-transcript.md). Every
call goes to the edge API with X-Api-Key, like the mission-control console.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leukerbad_g1 import settings  # noqa: E402

EDGE = os.environ.get("EDGE_URL") or settings.get("EDGE_URL", "http://127.0.0.1:8766")
KEY = settings.get("EDGE_API_KEY")
LOG: list[dict] = []
T0 = time.time()

# walkthrough times (s) for each beat's camera (docs/venue/walk_NNN.jpg = t 3*(NNN-1))
FRAME_EMPTY = 6.0  # walk_003: grey concourse tile, no one close
FRAME_PEOPLE = 3.0  # walk_002: three guests at the Kasse
FRAME_NOGO = 60.0  # walk_021: wood floor, terracotta stairs ~1.3 m ahead (no-go floor class ahead)


def call(path, body=None, timeout=240.0):
    req = urllib.request.Request(
        EDGE.rstrip("/") + path,
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={"content-type": "application/json", "x-api-key": KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def note(beat, what, data=None):
    row = {"t": round(time.time() - T0, 1), "beat": beat, "what": what, **({"data": data} if data is not None else {})}
    LOG.append(row)
    print(
        f"[{row['t']:6.1f}s] beat {beat}: {what}"
        + (f"  {json.dumps(data, ensure_ascii=False)[:300]}" if data is not None else ""),
        flush=True,
    )


def state():
    return call("/state")


def wait_for(pred, timeout=60.0, every=0.25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = state()
        if pred(s):
            return s
        time.sleep(every)
    return state()


def fsm(s):
    return s["safety"]["state"]


def events_since(t_wall, kinds=None):
    return [e for e in state()["events"] if e["ts"] >= t_wall and (kinds is None or e["kind"] in kinds)]


def reset_all(beat):
    s = state()
    if fsm(s) != "NORMAL" or s["paused"]:
        call("/resume", {})
        call("/reset", {"by": "demo"})
        s = wait_for(lambda s: fsm(s) in ("NORMAL", "CAUTION") or s["safety"]["awaiting_approval"], 30)
        if s["safety"]["awaiting_approval"]:
            call("/approve", {"by": "demo"})
        note(beat, "re-armed", {"fsm": fsm(state())})


def beat1():
    call("/mock/video", {"t": FRAME_EMPTY})
    time.sleep(1.0)
    q = "How much is a three-hour ticket for an adult?"
    r = call("/ask", {"text": q})
    note(1, f'guest: "{q}"', {"reply": r["reply"], "source": r.get("source"), "degraded": r.get("degraded")})
    q = "Please take me to the Therme entrance."
    t = time.time()
    r = call("/ask", {"text": q})
    note(1, f'guest: "{q}"', {"reply": r["reply"], "actions": r.get("actions")})
    s = wait_for(
        lambda s: any(
            e["kind"] == "guide" and e.get("phase") in ("arrived", "stopped") and e["ts"] >= t for e in s["events"]
        ),
        120,
    )
    g = [e for e in s["events"] if e["kind"] == "guide" and e["ts"] >= t]
    note(1, "guide finished", {"zone": s["robot"]["zone"], "fsm": fsm(s), "guide": g[-1] if g else None})


def beat2():
    reset_all(2)
    call("/mock/video", {"t": FRAME_PEOPLE})
    time.sleep(1.5)
    s = state()
    note(
        2,
        "camera: guests close by",
        {
            "people_within_2m": s["observation"]["people_within_2m"],
            "nearest_m": s["observation"]["nearest_person_m"],
            "fsm": fsm(s),
            "speed_cap": s["safety"]["speed_cap"],
        },
    )
    t = time.time()
    r = call("/guide_to", {"poi": "shop", "wait": False})
    note(2, "guide_to(shop) with people near", {"message": r.get("message")})
    caps = []
    for _ in range(16):
        time.sleep(0.5)
        s = state()
        caps.append((fsm(s), s["safety"]["speed_cap"]))
    note(
        2,
        "FSM while guiding",
        {"states": sorted({c[0] for c in caps}), "speed_caps": sorted({str(c[1]) for c in caps})},
    )
    call("/mock/video", {"t": FRAME_EMPTY})
    s = wait_for(
        lambda s: any(
            e["kind"] == "guide" and e.get("phase") in ("arrived", "stopped") and e["ts"] >= t for e in s["events"]
        ),
        120,
    )
    note(2, "guide finished once the guests moved on", {"zone": s["robot"]["zone"], "fsm": fsm(s)})


def beat3():
    reset_all(3)
    t = time.time()
    q = "Can you take me to the pool?"
    r = call("/ask", {"text": q})
    note(3, f'guest: "{q}"', {"reply": r["reply"], "actions": r.get("actions")})
    s = wait_for(
        lambda s: s.get("escort_pending") or any(e["kind"] == "approval" and e["ts"] >= t for e in s["events"]), 20
    )
    appr = events_since(t, ("approval",))
    note(3, "approval requested (staff escort)", appr[-1:] if appr else s.get("escort_pending"))
    time.sleep(2.0)
    d = call("/deny", {"by": "duty-manager"})
    note(3, "operator: Deny", d)
    time.sleep(1.0)
    note(
        3,
        "robot offers the alternative",
        {"said": [e for e in events_since(t, ("approval",))][-1:], "zone": state()["robot"]["zone"]},
    )


def beat4():
    reset_all(4)
    call("/mock/video", {"t": FRAME_EMPTY})
    time.sleep(1.0)
    t = time.time()
    r = call(
        "/mock/stage_breach",
        {"speed": 0.3, "max_s": 8.0, "slip_m": 1.1, "slip_after_s": 4.0, "start": [10.0, 5.45, 1.5708]},
    )
    note(
        4,
        "staged: an outside motion source (like the team's keyboard teleop) drives the robot from the entrance toward the turnstiles",
        r,
    )
    s = wait_for(lambda s: fsm(s) == "HOLD", 15, 0.1)
    note(
        4,
        "HOLD",
        {"latched": s["safety"]["latched"], "pose": [s["robot"]["x"], s["robot"]["y"]], "zone": s["robot"]["zone"]},
    )
    call("/mock/video", {"t": FRAME_NOGO})  # the camera now shows the no-go floor ahead (stand-in for a wet deck)
    s = wait_for(lambda s: s["robot"]["zone"] == "pool_deck", 10, 0.2)  # the staged slip (4 s after the stop)
    time.sleep(1.0)
    s = state()
    note(
        4,
        "staged slip: wet tile carried it on past the line",
        {"zone": s["robot"]["zone"], "pose": [s["robot"]["x"], s["robot"]["y"]]},
    )
    # past the turnstiles the zone is camera_off: L2 / L3 look at nothing there (privacy); their part came before
    evs = events_since(t, ("breach_predicted", "hazard", "judge_verdict", "fsm.transition", "mock.slip"))
    layers = sorted(
        {e.get("source") for e in evs if e["kind"] in ("breach_predicted", "hazard") and e.get("source")}
        | {e.get("source") for e in evs if e["kind"] == "fsm.transition" and e.get("source") in ("L1", "L2", "L3")}
        | ({"L3"} if any(e["kind"] == "judge_verdict" for e in evs) else set())
    )
    note(
        4,
        "layers that fired",
        {
            "layers": layers,
            "events": [
                {
                    k: e.get(k)
                    for k in (
                        "kind",
                        "source",
                        "basis",
                        "detector",
                        "floor_here",
                        "floor_ahead",
                        "stop",
                        "leaves_go_zone",
                        "hazards",
                        "reason",
                        "to",
                    )
                    if e.get(k) is not None
                }
                for e in evs
            ][:10],
        },
    )
    call("/mock/video", {"t": FRAME_EMPTY})
    r = call("/reset", {"by": "duty-manager"})
    note(4, "operator: Reset (recover into the go zone)", {"latched": r.get("latched"), "state": r.get("state")})
    s = wait_for(lambda s: s["safety"]["awaiting_approval"] or (fsm(s) == "HOLD" and not s["safety"]["latched"]), 30)
    rec = events_since(t, ("recover",))
    note(
        4,
        "recovered",
        {
            "recover": rec[-1] if rec else None,
            "zone": s["robot"]["zone"],
            "pose": [s["robot"]["x"], s["robot"]["y"]],
            "fsm": fsm(s),
            "awaiting_approval": s["safety"]["awaiting_approval"],
        },
    )
    time.sleep(1.5)
    a = call("/approve", {"by": "duty-manager"})
    note(4, "operator: Approve", {"decided": a.get("decided"), "fsm": a.get("safety", {}).get("state")})


def clawmetry_controller():
    d = Path.home() / ".clawmetry" / "robotics" / "control"
    regs = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
    ctl_id = state().get("clawmetry", {}).get("robot_id")
    for p in reversed(regs):
        reg = json.loads(p.read_text())
        if (reg.get("metadata") or {}).get("robot_id") == ctl_id:
            return reg
    raise SystemExit("controller registry not found (CLAWMETRY_CONTROLLER=0?)")


def ctl_post(reg, path, extra=None):
    body = {"controller_id": reg["controller_id"], **(extra or {})}
    req = urllib.request.Request(
        f"http://127.0.0.1:{reg['port']}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"content-type": "application/json", "authorization": "Bearer " + reg["token"]},
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def beat5():
    reset_all(5)
    call("/mock/video", {"t": FRAME_EMPTY})
    t = time.time()
    call("/guide_to", {"poi": "kasse", "wait": False})
    wait_for(lambda s: s["robot"]["mode"] == "walking", 30, 0.1)
    reg = clawmetry_controller()
    st = ctl_post(reg, "/stop")
    note(5, "ClawMetry Stop mid-roll", {"state": st.get("state"), "generation": st.get("stop_generation")})
    s = wait_for(lambda s: s["robot"]["mode"] != "walking", 10, 0.1)
    time.sleep(1.0)
    st = ctl_post(reg, "/status")
    note(
        5,
        "stopped (physical stop: yes)",
        {
            "controller_state": st.get("state"),
            "physical_stop_confirmed": st.get("physical_stop_confirmed"),
            "mode": s["robot"]["mode"],
            "fsm": fsm(state()),
        },
    )
    st = ctl_post(reg, "/reset", {"acknowledge": True, "expected_stop_generation": st["stop_generation"]})
    note(5, "ClawMetry Reset", {"state": st.get("state"), "message": st.get("message")})
    s = wait_for(lambda s: fsm(s) in ("NORMAL", "CAUTION"), 15)
    note(
        5,
        "re-armed",
        {
            "fsm": fsm(s),
            "zone": s["robot"]["zone"],
            "stop_events": [e["kind"] for e in events_since(t) if e["kind"].startswith("control.")],
        },
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--beats", default="1,2,3,4,5")
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo_d1w_mock-transcript.md"))
    a = ap.parse_args()
    s = state()
    note(
        0,
        "start",
        {
            "zone": s["robot"]["zone"],
            "fsm": fsm(s),
            "link": {k: s["link"].get(k) for k in ("brain_ok", "control_plane_ok")},
        },
    )
    for b in a.beats.split(","):
        globals()[f"beat{b.strip()}"]()
        time.sleep(1.0)
    call("/mock/video", {"t": None})
    out = Path(a.out)
    out.with_suffix(".json").write_text(json.dumps(LOG, indent=1, ensure_ascii=False))
    lines = [
        "# D1 Ultra-W mock demo: transcript",
        "",
        f"Run {time.strftime('%Y-%m-%d %H:%M %Z')}, {len(LOG)} steps. Generated by `scripts/demo_d1w_mock.py`.",
        "",
    ]
    for row in LOG:
        lines.append(f"- **{row['t']:.1f} s, beat {row['beat']}:** {row['what']}")
        if "data" in row:
            lines.append(f"  `{json.dumps(row['data'], ensure_ascii=False)[:600]}`")
    out.write_text("\n".join(lines) + "\n")
    print("transcript:", out)


if __name__ == "__main__":
    main()
