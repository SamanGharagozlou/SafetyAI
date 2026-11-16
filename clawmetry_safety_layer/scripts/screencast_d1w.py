"""Three-pane recording of the D1W mock demo: mission-control console | twin (top-down, from the edge's state) |
ClawMetry's Physical AI page. Headless Chrome renders the two web pages (DevTools protocol, no desktop capture, so
other windows on this machine never end up in the video); the twin pane is drawn here; ffmpeg encodes H.264.
No camera frame is drawn anywhere (the walkthrough shows guests; docs/PRIVACY.md).

    .venv/bin/python scripts/screencast_d1w.py --out docs/demo_d1w_mock.mp4 -- .venv/bin/python scripts/demo_d1w_mock.py
    (records until the command after -- exits, then 4 s more)

Needs: google-chrome, ffmpeg, the `websockets` package (present in this venv; dev-only tool, not an edge dependency).
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import yaml
from websockets.sync.client import connect

from leukerbad_g1 import settings

W, H = 640, 1080  # each pane
FPS = 4
COLORS = {"NORMAL": (80, 185, 63), "CAUTION": (34, 153, 210), "HOLD": (73, 81, 248), "RECOVER": (247, 113, 163)}


class Tab:
    def __init__(self, port: int, url: str, w: int, h: int):
        with urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"), timeout=10
        ) as r:
            info = json.loads(r.read())
        self.ws = connect(info["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024, open_timeout=10)
        self.n = 0
        self.lock = threading.Lock()
        self.cmd("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
        self.url = url

    def cmd(self, method, **params):
        with self.lock:
            self.n += 1
            mid = self.n
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                m = json.loads(self.ws.recv(timeout=20))
                if m.get("id") == mid:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error']}")
                    return m.get("result", {})

    def go(self, url=None):
        self.cmd("Page.navigate", url=url or self.url)

    def shot(self) -> np.ndarray:
        r = self.cmd("Page.captureScreenshot", format="jpeg", quality=80)
        img = cv2.imdecode(np.frombuffer(base64.b64decode(r["data"]), np.uint8), cv2.IMREAD_COLOR)
        return img


def robot_listed(dashboard: str, robot: str) -> bool:
    base = dashboard.split("/robotics")[0]
    try:
        with urllib.request.urlopen(base + "/api/robotics/runs?limit=200", timeout=5) as r:
            runs = json.loads(r.read()).get("runs") or []
        return any(x.get("robot_id") == robot for x in runs)
    except Exception:  # noqa: BLE001
        return False


def edge_state() -> dict | None:
    url = (os.environ.get("EDGE_URL") or settings.get("EDGE_URL", "http://127.0.0.1:8766")).rstrip("/") + "/state"
    try:
        req = urllib.request.Request(url, headers={"x-api-key": settings.get("EDGE_API_KEY")})
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def twin_pane(st: dict | None, zones: dict, trail: list) -> np.ndarray:
    img = np.full((H, W, 3), (22, 17, 14), np.uint8)
    cv2.putText(img, "Twin (D1 Ultra-W, mock)", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 230, 230), 2)
    pts = [p for z in zones.values() for p in z["polygon"]]
    x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
    x1, y1 = max(p[0] for p in pts), max(p[1] for p in pts)
    # rotate the map: venue x runs down the pane, y runs left->right (portrait pane, wide concourse)
    sc = min((W - 60) / (y1 - y0), (H - 360) / (x1 - x0))

    def tp(x, y):
        return int(30 + (y - y0) * sc), int(80 + (x - x0) * sc)

    for name, z in zones.items():
        poly = np.array([tp(*p) for p in z["polygon"]], np.int32)
        bad = z.get("restricted") or z.get("camera_off")
        cv2.fillPoly(img, [poly], (30, 20, 70) if bad else (30, 55, 25))
        cv2.polylines(img, [poly], True, (70, 70, 70), 1)
        cx, cy = tp(*z.get("centre", z["polygon"][0]))
        cv2.putText(img, name, (cx - 40, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (150, 150, 150), 1)
    if st:
        r = st["robot"]
        trail.append(tp(r["x"], r["y"]))
        del trail[:-400]
        for a, b in zip(trail, trail[1:], strict=False):
            cv2.line(img, a, b, (120, 120, 120), 1)
        fs = st["safety"]["state"]
        c = COLORS.get(fs, (255, 255, 255))
        px, py = tp(r["x"], r["y"])
        cv2.circle(img, (px, py), 10, c, -1)
        hx, hy = tp(r["x"] + 0.6 * math.cos(r["yaw"]), r["y"] + 0.6 * math.sin(r["yaw"]))
        cv2.line(img, (px, py), (hx, hy), (255, 255, 255), 2)
        y = H - 260
        cv2.rectangle(img, (20, y - 40), (W - 20, y), c, -1)
        cv2.putText(
            img,
            f"FSM {fs}   cap {st['safety']['speed_cap'] if st['safety']['speed_cap'] is not None else 'policy'}",
            (30, y - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (10, 10, 10),
            2,
        )
        lines = [
            f"zone {r['zone']}   mode {r['mode']}",
            f"people <2 m {st['observation']['people_within_2m']}   nearest {st['observation']['nearest_person_m']}",
            f"boundary ahead {st['observation']['boundary_ahead_m']}   no-go {st['observation']['no_go_ahead']}",
        ]
        lat = st["safety"].get("latched")
        if lat:
            lines.append(f"latched {lat['source']}: {lat['reason']}"[:60])
        if st["safety"].get("awaiting_approval"):
            lines.append("waiting for Approve")
        v = (st.get("verdicts") or [None])[-1]
        if v:
            lines.append(f"Cosmos: stop={v.get('stop')} leaves={v.get('leaves_go_zone')} {v.get('latency_s')} s"[:60])
        for i, ln in enumerate(lines):
            cv2.putText(img, ln, (24, y + 32 + 30 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1)
    return img


JOURNAL_KINDS = {
    "fsm.transition": ("from", "to", "source", "reason"),
    "breach_predicted": ("source", "basis", "zone"),
    "person_near": ("level", "distance_m"),
    "hazard": ("source", "floor_here", "floor_ahead", "hazards"),
    "judge_verdict": ("stop", "leaves_go_zone", "people_within_2m", "latency_s"),
    "recover": ("phase", "moved_m", "detail"),
    "approval": ("tool", "decision", "by"),
    "guide": ("phase", "poi"),
    "concierge.turn": ("source", "reply"),
    "control.stop_requested": ("reason",),
    "control.stop_result": ("state", "physical_stop_confirmed"),
    "control.reset": (),
    "turn.outcome": ("skill", "outcome", "message"),
    "mock.slip": ("slip_m",),
}


def journal_pane(path: Path | None, robot: str) -> np.ndarray:
    """Our own ClawMetry robot-session journal (clawmetry.robotics.v1), newest at the bottom. Used when the local
    ClawMetry dashboard does not list this robot (on this Spark :8900 is a teammate's ClawMetry, reading only their
    journal directory)."""
    img = np.full((H, W, 3), (24, 20, 18), np.uint8)
    cv2.putText(img, "ClawMetry journal", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (230, 230, 230), 2)
    cv2.putText(img, f"{robot}  (clawmetry.robotics.v1)", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    rows = []
    if path is not None and path.exists():
        with open(path, "rb") as f:
            f.seek(max(0, path.stat().st_size - 400_000))
            for line in f.read().decode(errors="replace").splitlines()[1:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                k = r.get("kind")
                if k in JOURNAL_KINDS:
                    d = r.get("data") or {}
                    txt = " ".join(f"{x}={str(d.get(x))[:38]}" for x in JOURNAL_KINDS[k] if d.get(x) is not None)
                    t = time.strftime("%H:%M:%S", time.localtime(int(r.get("wall_time_ns", "0")) / 1e9))
                    rows.append((t, k, txt))
    y = 110
    colors = {
        "fsm.transition": (120, 200, 255),
        "breach_predicted": (90, 90, 255),
        "hazard": (80, 160, 255),
        "judge_verdict": (200, 170, 120),
        "approval": (160, 230, 160),
        "control.stop_requested": (90, 90, 255),
    }
    for t, k, txt in rows[-30:]:
        cv2.putText(img, f"{t} {k}", (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors.get(k, (210, 210, 210)), 1)
        cv2.putText(img, txt[:78], (30, y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (170, 170, 170), 1)
        y += 32
    return img


def fit(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    s = min(W / w, H / h)
    r = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out = np.zeros((H, W, 3), np.uint8)
    out[: r.shape[0], : r.shape[1]] = r
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "demo_d1w_mock.mp4"))
    ap.add_argument("--brain", default="http://127.0.0.1:8780/")
    ap.add_argument("--clawmetry", default="http://127.0.0.1:8900/robotics")
    ap.add_argument("--robot", default="leukerbad-agibot_d1w_mock", help="ClawMetry session to show")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    prof = tempfile.mkdtemp(prefix="d1w-cast-")
    port = 9333
    ch = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={prof}",
            "--no-first-run",
            "--disable-gpu",
            "--hide-scrollbars",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        con = Tab(port, a.brain, 960, 1620)
        con.cmd("Network.enable")
        con.cmd(
            "Network.setCookie",
            name="brain_key",
            value=settings.get("BRAIN_API_KEY"),
            domain="127.0.0.1",
            path="/",
            httpOnly=True,
        )
        con.go()
        cm = Tab(port, a.clawmetry, 960, 1620)
        cm.go()
        time.sleep(3)
        zones = yaml.safe_load((ROOT / "policies/zones_therme.yaml").read_text())["zones"]
        ff = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{3 * W}x{H}",
                "-r",
                str(FPS),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-crf",
                "23",
                "-movflags",
                "+faststart",
                a.out,
            ],
            stdin=subprocess.PIPE,
        )
        proc = subprocess.Popen(cmd, cwd=ROOT) if cmd else None
        trail: list = []
        t_end = None
        n = 0
        cm_last = None
        last_pick = 0.0
        listed = None
        st_cache = None
        t0 = time.time()
        while True:
            tick = time.time()
            if proc is not None and proc.poll() is not None and t_end is None:
                t_end = tick + 4.0
            if (t_end and tick > t_end) or (proc is None and tick - t0 > 60):
                break
            try:
                left = fit(con.shot())
            except Exception:  # noqa: BLE001
                left = np.zeros((H, W, 3), np.uint8)
            st_cache = edge_state()
            mid = twin_pane(st_cache, zones, trail)
            if tick - last_pick > 8.0:  # select our robot's session in ClawMetry's list once it has synced
                last_pick = tick
                try:
                    cm.cmd(
                        "Runtime.evaluate",
                        expression="(()=>{const want='"
                        + a.robot
                        + "';const els=[...document.querySelectorAll('a,button,li,div')]"
                        ".filter(e=>e.children.length<6&&(e.innerText||'').trim().startsWith(want));"
                        "const el=els.sort((x,y)=>(x.innerText||'').length-(y.innerText||'').length)[0];"
                        "if(el&&!el.dataset.picked){el.click();el.dataset.picked=1;return 'clicked'}return 'none'})()",
                    )
                except Exception:  # noqa: BLE001
                    pass
            if n % 2 == 0 or cm_last is None:  # ClawMetry pane at half rate (its sync lags anyway)
                if listed is None and tick - t0 > 2:
                    listed = robot_listed(a.clawmetry, a.robot)
                    print(f"ClawMetry dashboard lists {a.robot}: {listed}" + ("" if listed else " -> journal pane"))
                if listed:
                    try:
                        cm_last = fit(cm.shot())
                    except Exception:  # noqa: BLE001
                        cm_last = np.zeros((H, W, 3), np.uint8)
                else:
                    st0 = st_cache or {}
                    rid = (st0.get("clawmetry") or {}).get("run_id")
                    jp = Path.home() / ".clawmetry" / "robotics" / "journal" / f"{rid}.jsonl" if rid else None
                    cm_last = journal_pane(jp, a.robot)
            frame = np.hstack([left, mid, cm_last]).tobytes()
            due = int((time.time() - t0) * FPS) + 1  # real time: repeat the frame when capturing ran slow
            while n < due:
                ff.stdin.write(frame)
                n += 1
            time.sleep(max(0.0, 1.0 / FPS - (time.time() - tick)))
        ff.stdin.close()
        ff.wait()
        print(f"wrote {a.out}: {n} frames, {n / FPS:.0f} s")
        if proc is not None:
            sys.exit(proc.returncode or 0)
    finally:
        ch.terminate()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    main()
