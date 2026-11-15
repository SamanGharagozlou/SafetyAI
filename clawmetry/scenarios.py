#!/usr/bin/env python3
"""Drive every detector, so the control plane can be reviewed without a robot.

    node tools/robot-sim/server.mjs &
    python3 clawmetry/server.py --upstream http://127.0.0.1:5175 \
            --zones clawmetry/zones.example.json --port 8901 &
    python3 clawmetry/scenarios.py --url http://127.0.0.1:8901

Each scenario is one thing a reviewer should be able to see happen. Real tool
calls go through the tap; the rest are injected on /ingest, which is the same
door the ROS, guard and approval collectors use - nothing here is a special
test path into the server.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request

URL = ""


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def get(path):
    with urllib.request.urlopen(URL + path, timeout=20) as r:
        return json.loads(r.read())


def tool(name, args=None, call_id="s"):
    return post("/vapi", {"message": {"type": "tool-calls", "toolCallList": [
        {"id": call_id, "name": name, "arguments": args or {}}]}})


def ingest(kind, actor="system", data=None, corr=""):
    return post("/ingest", {"kind": kind, "actor": actor, "data": data or {}, "corr": corr})


def say(n, title, why):
    print(f"\n[{n}] {title}\n     {why}", flush=True)


def result(r):
    txt = (r.get("results") or [{}])[0].get("result", "")
    print(f"     -> {txt[:110]}", flush=True)


def main():
    global URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8901")
    ap.add_argument("--only", default="", help="run one scenario by number")
    a = ap.parse_args()
    URL = a.url.rstrip("/")
    pick = a.only

    def run(n):
        return not pick or pick == str(n)

    print(f"driving {URL}")
    tool("robot_command", {"command": "stand_up"})
    time.sleep(1)

    if run(1):
        say(1, "A normal, authorised action",
            "intent -> prediction -> gate allows -> command -> outcome scored. The quiet case.")
        result(tool("walk_distance", {"distance_cm": 50, "direction": "forward"}, "ok1"))

    if run(2):
        say(2, "Predicted breach: the path enters a forbidden zone",
            "The kinematic model runs BEFORE the robot moves, so a misfire is refused, "
            "not merely recorded. Slide 9.")
        result(tool("walk_distance", {"distance_cm": 300, "direction": "forward"}, "deny1"))

    if run(3):
        say(3, "A stop is never gated",
            "Gating a stop is how you get a robot that cannot be stopped.")
        result(tool("stop_robot", {}, "stop1"))

    if run(4):
        say(4, "Unattributed motion",
            "The robot is moving and no authorised intent explains it: a rogue publisher, "
            "a stuck teleop key, or a compromised agent. One check, all three.")
        ingest("state", "robot", {"source": "bridge", "moving": True, "speed_mps": 0.31})
        time.sleep(0.4)

    if run(5):
        say(5, "A command the gate never saw",
            "Something reached the robot without being adjudicated.")
        ingest("command", "agent", {"tool": "walk_distance", "args": {"distance_cm": 200}},
               corr="ghost-1")
        time.sleep(0.4)

    if run(6):
        say(6, "A denied command executed anyway",
            "The worst case in the set: the gate said no and the robot moved.")
        ingest("intent", "agent", {"tool": "walk_distance",
                                   "args": {"distance_cm": 200, "direction": "forward"}}, corr="defy-1")
        ingest("decision", "gate", {"decision": "deny", "reason": "restricted area"}, corr="defy-1")
        ingest("command", "agent", {"tool": "walk_distance"}, corr="defy-1")
        time.sleep(0.4)

    if run(7):
        say(7, "Narration divergence",
            "The agent accepted a walk and told the guest it was on its way. "
            "Odometry says it never moved. Caught while the call is still in flight.")
        ingest("state", "robot", {"source": "bridge", "moving": False, "speed_mps": 0.0})
        ingest("intent", "agent", {"tool": "guide_to", "args": {"destination": "pools"}}, corr="lie-1")
        ingest("decision", "gate", {"decision": "allow"}, corr="lie-1")
        ingest("command", "agent", {"tool": "guide_to"}, corr="lie-1")
        print("     waiting out the grace period...", flush=True)
        time.sleep(8)

    if run(8):
        say(8, "Prediction deviation",
            "It moved, but not to where the world model said it would. "
            "Sigmas come from measured hardware, not a guess.")
        ingest("intent", "agent", {"tool": "walk_distance",
                                   "args": {"distance_cm": 150, "direction": "forward"}}, corr="drift-1")
        ingest("outcome", "robot", {"tool": "walk_distance", "deviation_z": 9.4,
                                    "duration_s": 12.1, "position_error_m": 0.94,
                                    "end_pose": [0.56, 0.91, 0.0]}, corr="drift-1")
        time.sleep(0.4)

    if run(9):
        say(9, "Policy breach on confirmed state",
            "Moving with someone inside the minimum distance - judged on what the robot "
            "DID, regardless of what any component believed at the time.")
        ingest("intent", "agent", {"tool": "walk_distance",
                                   "args": {"distance_cm": 80, "direction": "forward"}}, corr="close-1")
        ingest("state", "robot", {"source": "bridge", "moving": True, "speed_mps": 0.22,
                                  "nearest_person_m": 0.55})
        time.sleep(0.4)

    if run(10):
        say(10, "A feed that never starts, and one that dies",
            "Absence is a finding. A monitor whose failure looks like calm is worse "
            "than no monitor.")
        ingest("state", "robot", {"source": "guard", "moving": False, "heartbeat": True})
        print("     letting the guard feed go quiet...", flush=True)
        time.sleep(4)

    s = get("/api/state")
    print("\n" + "=" * 68)
    print(f"  chain    : {s['chain']['reason']} ({s['chain']['count']} events)")
    print(f"  breaches : {len(s['breaches'])}")
    for b in s["breaches"][:12]:
        print(f"     [{b['severity']:<8}] {b['rule']:<26} {b['detail'][:58]}")
    print("=" * 68)


if __name__ == "__main__":
    main()
