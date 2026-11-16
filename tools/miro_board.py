#!/usr/bin/env python3
"""Build the SafetyAI architecture board on Miro, or as a draggable SVG.

    python3 tools/miro_board.py --dry-run                      # validate, print the plan
    python3 tools/miro_board.py --svg docs/technical/img/architecture-board.svg
    MIRO_TOKEN=... python3 tools/miro_board.py --push \\
        --board 'uXjVHmehDj0='                                 # create it natively

WHY ONE LAYOUT, TWO RENDERERS
    The board and the SVG are generated from the same NODES/EDGES tables, so a
    diagram that was dragged in as a picture and one that was built natively
    cannot drift apart. The SVG exists because it needs no credential: drop it
    on the board and it is there in ten seconds. The API path exists because an
    image is not editable and a board that cannot be rearranged in a workshop
    is a screenshot with extra steps.

WHY NO DEPENDENCIES
    Same rule as clawmetry/ and tools/robot-console: this has to run on a venue
    laptop with no install step. urllib only.

TOKEN HANDLING
    MIRO_TOKEN from the environment, never a flag - every argument is visible
    to every user on the box through `ps`. The token is never printed, and the
    failure message never echoes it. See scripts/audit_secrets.py for the same
    convention applied repo-wide.

    Get one at https://developers.miro.com -> your app -> "Install app and get
    OAuth token". It needs the `boards:write` scope, and the app must be
    installed on the team that owns the board.

STATUS: --dry-run and --svg are verified. --push has NOT been run against the
    Miro API from this repository (no token was available), so treat the first
    real run as a test: it creates items and creates nothing destructive, and
    `--push` refuses to run twice against the same board without --again.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.miro.com/v2"

# --------------------------------------------------------------------------
# palette - one colour per authority, used identically in both renderers.
# "who owns this box" is the only thing colour encodes here, because that is
# the distinction the whole architecture turns on.
# --------------------------------------------------------------------------
KIND = {
    # kind:        (miro fill,     svg fill,  svg stroke, svg text)
    "cloud":       ("light_pink",  "#fde8e8", "#d94c4c", "#7a1f1f"),  # not ours: a CLAIM
    "ours":        ("light_green", "#e6f6ec", "#2f8f4e", "#14532d"),  # ground truth
    "gate":        ("light_yellow", "#fff4d6", "#d19b00", "#6b4e00"),  # the decision point
    "robot":       ("light_blue",  "#e4f0fb", "#2b6cb0", "#153e75"),  # the machine
    "evidence":    ("violet",      "#efe8fb", "#6b46c1", "#3c2a6b"),  # the record
    "human":       ("gray",        "#eef0f2", "#5b6670", "#2d3339"),  # a person
    "danger":      ("red",         "#fbe0e0", "#c53030", "#742a2a"),  # a gap / a hazard
}

FRAME_W, FRAME_H = 3000, 1500


def N(id, frame, x, y, w, h, kind, title, body=""):
    return dict(id=id, frame=frame, x=x, y=y, w=w, h=h, kind=kind, title=title, body=body)


# --------------------------------------------------------------------------
# FRAME 1 - the governed command path, and where the trust boundary sits.
# This is the one diagram that explains the project. Everything to the left of
# the dashed line is something somebody else told us; everything to the right
# was measured on hardware we own.
# --------------------------------------------------------------------------
F1 = "f1"
F2 = "f2"
F3 = "f3"

FRAMES = [
    dict(id=F1, title="1 · The governed command path — everything left of the dashed line is a CLAIM"),
    dict(id=F2, title="2 · What changes when a child is within 2 m"),
    dict(id=F3, title="3 · Defence in depth against a hijacked or prompt-injected agent"),
]

NODES = [
    # --- frame 1: upstream, not observable -------------------------------
    N("guest", F1, -1150, -520, 380, 150, "human", "Guest",
      "speaks to the dog · scans the QR · can always press Stop"),
    N("stt", F1, -1150, -300, 380, 130, "cloud", "Deepgram nova-3",
      "speech to text · language: multi"),
    N("llm", F1, -1150, -120, 380, 130, "cloud", "Claude (Sonnet)",
      "13 typed tools · temperature 0.4"),
    N("tts", F1, -1150, 60, 380, 130, "cloud", "ElevenLabs turbo",
      "speed 0.88 · stability 0.45"),
    N("vapinote", F1, -1150, 260, 380, 170, "cloud", "Vapi cloud",
      "SELF-REPORT ONLY. No OpenTelemetry; the only instrumentation is "
      "artifact.performanceMetrics on the webhook."),

    # --- frame 1: the tap -------------------------------------------------
    N("tap", F1, -480, -300, 440, 520, "gate", "THE TAP  :8901",
      "1 record intent\n2 predict (kinematic, ~0 ms)\n3 judge (Cosmos, best effort)\n"
      "4 GATE: allow / deny\n5 forward unchanged\n6 score the outcome\n\n"
      "Binds 127.0.0.1. Unknown routes are refused, not tunnelled."),
    N("predict", F1, -480, 330, 440, 160, "gate", "Kinematic predictor",
      "0.1373 m/s · 4.11 s overhead · 4.1 cm undershoot — MEASURED, not guessed. "
      "Validation walk: 0.13σ"),
    N("cosmos", F1, -480, 560, 440, 150, "cloud", "Cosmos-Reason2 (judge)",
      "~4 s, off-box. Never gates a stop. unavailable ≠ approved"),

    # --- frame 1: downstream, ground truth --------------------------------
    N("edge", F1, 160, -300, 380, 130, "ours", "d1-vapi-server :8090",
      "the public edge"),
    N("agent", F1, 160, -120, 380, 130, "ours", "d1-ros-agent :8091",
      "owns the robot · loopback · no token"),
    N("bridge", F1, 160, 60, 380, 150, "ours", "agibot_d1_bridge",
      "the ONE SDK client · velocity caps · 0.5 s watchdog"),
    N("d1", F1, 160, 270, 380, 150, "robot", "AgiBot D1 Ultra W",
      "16 kg · SDK UDP 43988 · RTSP :8554 · no LiDAR, no depth"),

    N("guard", F1, 770, 270, 400, 230, "ours", "Camera people-guard",
      "GO / SLOW / HOLD / FREEZE from one monocular camera.\n"
      "Sends stop_robot every tick while unsafe — the layer that interrupts a "
      "walk already in flight."),
    N("ledger", F1, 770, -300, 400, 320, "evidence", "Hash-chained ledger",
      "append-only · sha256 per line · fsynced\n\n"
      "11 event kinds: utterance · intent · prediction · decision · "
      "intervention · command · state · outcome · breach · integrity · policy\n\n"
      "GET /api/verify names the first broken line"),
    N("dash", F1, 770, 60, 400, 150, "evidence", "Control plane dashboard",
      "Live · Trace · Guardrails · SSE · 30 ms /api/state"),

    N("venue", F1, 1290, -300, 400, 320, "human", "venue/leukerbad_therme.yaml",
      "THE SPA'S DOCUMENT, signed by the person accountable for guest safety.\n\n"
      "Config can only TIGHTEN.\nAnything unset is FORBIDDEN.\n"
      "Unsigned means CAUTIOUS: 0.15 m/s everywhere and no guiding at all."),
    N("operator", F1, 1290, 100, 400, 190, "human", "Operator",
      "Guardrails tab · console (Tailscale Serve, tailnet only) · E-STOP.\n"
      "Every edit is classified more-freedom / more-restraint and lands on the "
      "ledger BEFORE the reload."),
    N("switch", F1, 1290, 400, 400, 180, "danger", "The switch that decides if any of this is real",
      "funnel 443 → 8901 = GOVERNED\nfunnel 443 → 8090 = BYPASSED\n\n"
      "Bypassed, the tap runs idle — which looks exactly like a quiet robot."),

    # --- frame 2: the child rules ----------------------------------------
    N("c_see", F2, -1150, 1420, 420, 200, "ours", "One camera, no depth",
      "DFOV 122° · VFOV 70°. Range from the pixel row of someone's FEET on a "
      "known floor plane. 2.0 cm/px at 2 m."),
    N("c_blind", F2, -1150, 1680, 420, 230, "danger", "The blind spot IS the problem",
      "A camera 0.40 m up cannot see the floor inside 0.40–0.57 m.\n\n"
      "A two-year-old who toddles up to touch the robot VANISHES from frame.\n"
      "Disappearance at close range does not mean absence. It means CLOSER."),
    N("c_latch", F2, -1150, 1970, 420, 190, "gate", "So the guard LATCHES",
      "adult lost close → presumed present 3 s, decays to HOLD\n"
      "child lost close → presumed present 5 s, decays to FREEZE\n\n"
      "Confidence decays towards danger, not convenience."),

    N("c_detect", F2, -520, 1420, 400, 170, "gate", "Is this a child?",
      "height < 1.25 m\n(a 2-year-old is ~0.85 m; 1.25 clears toddlers without "
      "flagging short adults)"),
    N("c_dist", F2, -520, 1650, 400, 140, "gate", "Within 2 m?",
      "double the adult hold radius, because the cost of being wrong is "
      "categorically different"),
    N("c_freeze", F2, -520, 1870, 400, 170, "danger", "→ FULL STOP",
      "not a slow-down. No walking, no turning, no strafing.\n"
      "A frozen robot still talks, still listens, can still be stopped."),

    N("c_gest", F2, 100, 1420, 440, 330, "gate", "Gestures: an ALLOWLIST, never a denylist",
      "ALLOWED (feet never leave the ground):\nnod · shake (a 14° yaw, 'no') · "
      "tilt · bow · squat · wiggle · look_around · sway_circle\n\n"
      "REFUSED near a child:\ngreet · welcome · shake_hand — all three end in "
      "shakeHand(), which raises a paw to the head height of a two-year-old.\n\n"
      "Anything unclassified is REFUSED. An earlier denylist let greet and "
      "welcome straight through."),
    N("c_stale", F2, 100, 1830, 440, 150, "danger", "No fresh frame for 0.5 s → FREEZE",
      "A dead feed reads as danger, never as a clear path. This stack shipped "
      "the opposite bug once: 200 OK with nulls while the robot lay on the floor."),

    N("c_breach", F2, 700, 1420, 420, 200, "evidence", "child_proximity — CRITICAL",
      "Raised on CONFIRMED PHYSICAL STATE, not on intent. Fires even if the "
      "gate allowed it, even if the guard was down, even if a human did it."),
    N("c_clock", F2, 700, 1680, 420, 190, "evidence", "A clock, written into the ledger",
      "duty manager    15 min\nISO/IEC 42001 auditor   1440 min\n\n"
      "A deadline that exists only in a UI is one nobody can prove was known."),
    N("c_gap", F2, 700, 1930, 420, 200, "danger", "AND THE HONEST PART",
      "child_safety_verified is a BLOCKING GAP.\n"
      "The rule is enforced; the detector behind it has NEVER been tested "
      "against a child. The guard is currently inactive on the Jetson."),

    N("c_owner", F2, 1290, 1420, 400, 290, "human", "The venue sets these numbers",
      "height_under_m: 1.25\nfreeze_within_m: 2.0\nforbid_limb_gestures: true\n"
      "latch_s: 5.0\n\nEditable from the Guardrails tab without SSH. Every "
      "change is classified, named, reasoned and recorded."),

    # --- frame 3: the adversarial layers ---------------------------------
    N("a_threat", F3, -1150, 3120, 420, 230, "danger", "The threat",
      "Untrusted content entering an agent's context is treated as instruction, "
      "and the agent uses its LEGITIMATE tool access against its operator.\n\n"
      "Here the untrusted input IS the product: a guest speaking."),
    N("a_assume", F3, -1150, 3400, 420, 170, "gate", "The assumption",
      "Do not ask whether the model is trustworthy.\nAsk what it can do if it "
      "is not."),

    N("a_l1", F3, -520, 3060, 480, 170, "ours", "L1 · Bounded vocabulary",
      "13 named tools, typed and range-checked. No shell, no code, no arbitrary "
      "HTTP, no new tools. Max displacement per call: 2 m."),
    N("a_l2", F3, -520, 3270, 480, 150, "ours", "L2 · Irreversible actions are absent",
      "damp (drops the robot) is on NO agent-reachable surface. estop is never "
      "exposed to voice or guests."),
    N("a_l3", F3, -520, 3460, 480, 150, "gate", "L3 · The gate refuses before, not after",
      "Predicted consequence, not stated intent. A hijacked agent and a buggy "
      "one get the same refusal."),
    N("a_l4", F3, -520, 3650, 480, 150, "ours", "L4 · One command path",
      "No raw /cmd_vel from voice. Guest taps are injected as SENTENCES, so a "
      "tap and speech go through the same gate."),
    N("a_l5", F3, -520, 3840, 480, 150, "evidence", "L5 · Attribution + tamper evidence",
      "utterance → intent → prediction → decision → command → outcome, "
      "sha256-chained."),

    N("a_fail", F3, 110, 3060, 440, 290, "gate", "Fail closed — four times, each a real bug",
      "1 unrecognised judge verdict shape\n2 unauthenticated collector → 401 all "
      "session\n3 a feed that never started was invisible\n4 the judge "
      "contradicted its own prose\n\n"
      "EVERY ONE turned an absence or a misread into an APPROVAL. That is the "
      "failure mode this layer exists to prevent, and it kept reappearing "
      "inside the layer itself."),
    N("a_stop", F3, 110, 3410, 440, 150, "ours", "Never gate a stop",
      "stop_robot is ungated, always. Esc and Space are STOP even while typing. "
      "Anyone may end a session, no credentials."),
    N("a_obs", F3, 110, 3620, 440, 170, "evidence", "The observer cannot actuate",
      "No endpoint in the control plane commands the robot. Fleet-wide "
      "oversight that can also drive is a fleet-wide remote control."),

    N("a_net", F3, 700, 3060, 420, 290, "ours", "Network and credentials",
      "Everything binds 127.0.0.1.\nConsole on Tailscale SERVE (tailnet), never "
      "Funnel — funnelling is a PORT-level property, so /console under 443 "
      "would be public.\nThe token never reaches the browser.\n"
      "Tokens from env, not argv (ps is world-readable).\n"
      "348 files scanned, 0 findings."),

    N("a_gap", F3, 700, 3420, 420, 330, "danger", "NOT defended — the honest list",
      "• No /cmd_vel arbitration, and DDS is unauthenticated. Anyone on the "
      "subnet can drive the robot past every control on this board. LARGEST HOLE.\n"
      "• The tap only governs while the funnel points at it.\n"
      "• The ledger is not signed: internal consistency, not origin.\n"
      "• Prompt injection WITHIN the allowed envelope is not prevented. The "
      "gate bounds the physics, not the intent."),

    N("a_prin", F3, 1290, 3060, 400, 690, "gate", "The principles, extracted",
      "1 Assume the agent is adversarial\n2 Bound capability, not intent\n"
      "3 Gate on predicted physical consequence\n4 Allowlist, never denylist\n"
      "5 Anything unset is forbidden\n6 Never gate a stop\n"
      "7 Never expose the irreversible\n8 Governance must not actuate\n"
      "9 Absence is a finding\n10 Record intention and action separately, "
      "then correlate\n11 Make the honest claim: 'not enforced' is never "
      "'allowed'; 'not scored' is never 'passed'"),
]

EDGES = [
    # frame 1 - the path
    ("guest", "stt", ""),
    ("stt", "llm", ""),
    ("llm", "tts", ""),
    ("tts", "guest", "audio out"),
    ("llm", "tap", "tool call"),
    ("tap", "predict", "before"),
    ("tap", "cosmos", "behind the gate"),
    ("tap", "edge", "forward, or REFUSE"),
    ("edge", "agent", ""),
    ("agent", "bridge", ""),
    ("bridge", "d1", "SDK, one client"),
    ("d1", "guard", "RTSP"),
    ("guard", "edge", "stop_robot while unsafe"),
    ("agent", "tap", "confirmed state, 2 Hz"),
    ("tap", "ledger", "every event"),
    ("ledger", "dash", ""),
    ("venue", "tap", "the rules"),
    ("operator", "venue", "signs / edits"),

    # frame 2 - the child decision
    ("c_see", "c_detect", ""),
    ("c_blind", "c_latch", ""),
    ("c_latch", "c_dist", "presumed present"),
    ("c_detect", "c_dist", "yes"),
    ("c_dist", "c_freeze", "yes"),
    ("c_dist", "c_gest", "yes"),
    ("c_stale", "c_freeze", ""),
    ("c_freeze", "c_breach", "if it moves anyway"),
    ("c_breach", "c_clock", ""),
    ("c_owner", "c_detect", "sets the numbers"),

    # frame 3 - layers
    ("a_threat", "a_assume", ""),
    ("a_assume", "a_l1", ""),
    ("a_l1", "a_l2", ""),
    ("a_l2", "a_l3", ""),
    ("a_l3", "a_l4", ""),
    ("a_l4", "a_l5", ""),
    ("a_l3", "a_fail", ""),
    ("a_l5", "a_obs", ""),
]


# --------------------------------------------------------------------------
# Frames are SIZED FROM THEIR CONTENTS rather than hand-tuned. A frame typed
# out by hand drifts the moment a box is added, and a frame with a band of
# empty space at the top reads as a layout mistake on a shared board.
# --------------------------------------------------------------------------
PAD_X, PAD_TOP, PAD_BOT = 90, 120, 80       # PAD_TOP leaves room for the title


def size_frames() -> None:
    for f in FRAMES:
        inf = [n for n in NODES if n["frame"] == f["id"]]
        if not inf:
            f.update(x=0, y=0, w=FRAME_W, h=FRAME_H)
            continue
        x0 = min(n["x"] - n["w"] / 2 for n in inf) - PAD_X
        x1 = max(n["x"] + n["w"] / 2 for n in inf) + PAD_X
        y0 = min(n["y"] - n["h"] / 2 for n in inf) - PAD_TOP
        y1 = max(n["y"] + n["h"] / 2 for n in inf) + PAD_BOT
        f.update(x=(x0 + x1) / 2, y=(y0 + y1) / 2, w=x1 - x0, h=y1 - y0)


size_frames()


# --------------------------------------------------------------------------
# validation - run by every mode, so a typo in the tables is a startup error
# rather than half a board.
# --------------------------------------------------------------------------
def validate() -> list[str]:
    errs = []
    ids = {}
    for n in NODES:
        if n["id"] in ids:
            errs.append(f"duplicate node id {n['id']!r}")
        ids[n["id"]] = n
        if n["frame"] not in {f["id"] for f in FRAMES}:
            errs.append(f"{n['id']}: unknown frame {n['frame']!r}")
        if n["kind"] not in KIND:
            errs.append(f"{n['id']}: unknown kind {n['kind']!r}")
    for a, b, _ in EDGES:
        for e in (a, b):
            if e not in ids:
                errs.append(f"edge references missing node {e!r}")
    # Overlap check per frame: two boxes on top of each other is the one layout
    # bug that is invisible in a dry run and obvious on the board.
    for f in FRAMES:
        inf = [n for n in NODES if n["frame"] == f["id"]]
        for i, a in enumerate(inf):
            for b in inf[i + 1:]:
                if (abs(a["x"] - b["x"]) * 2 < a["w"] + b["w"]
                        and abs(a["y"] - b["y"]) * 2 < a["h"] + b["h"]):
                    errs.append(f"overlap: {a['id']} and {b['id']}")
    return errs


# --------------------------------------------------------------------------
# SVG renderer - no credential needed; drag the file onto the board.
# --------------------------------------------------------------------------
def wrap(text: str, width_px: float, size: float) -> list[str]:
    """Greedy wrap at an approximate glyph width. Good enough for a diagram."""
    per = max(1, int(width_px / (size * 0.52)))
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        line = ""
        for word in para.split():
            if line and len(line) + 1 + len(word) > per:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        out.append(line)
    return out


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def to_svg() -> str:
    pad = 80
    xs = [f["x"] - f["w"] / 2 for f in FRAMES] + [n["x"] - n["w"] / 2 for n in NODES]
    ys = [f["y"] - f["h"] / 2 for f in FRAMES] + [n["y"] - n["h"] / 2 for n in NODES]
    xe = [f["x"] + f["w"] / 2 for f in FRAMES] + [n["x"] + n["w"] / 2 for n in NODES]
    ye = [f["y"] + f["h"] / 2 for f in FRAMES] + [n["y"] + n["h"] / 2 for n in NODES]
    minx, miny, maxx, maxy = min(xs) - pad, min(ys) - pad, max(xe) + pad, max(ye) + pad
    W, H = maxx - minx, maxy - miny
    by = {n["id"]: n for n in NODES}
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.0f} {miny:.0f} '
             f'{W:.0f} {H:.0f}" width="{W:.0f}" height="{H:.0f}" '
             f'font-family="Inter, Helvetica, Arial, sans-serif">')
    p.append('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" '
             'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#8a93a0"/></marker></defs>')
    p.append(f'<rect x="{minx}" y="{miny}" width="{W}" height="{H}" fill="#ffffff"/>')

    for f in FRAMES:
        x, y = f["x"] - f["w"] / 2, f["y"] - f["h"] / 2
        p.append(f'<rect x="{x}" y="{y}" width="{f["w"]}" height="{f["h"]}" rx="18" '
                 f'fill="#fafbfc" stroke="#c9d1d9" stroke-width="2"/>')
        p.append(f'<text x="{x + 28}" y="{y + 48}" font-size="30" font-weight="700" '
                 f'fill="#1b2026">{esc(f["title"])}</text>')

    # the trust boundary, frame 1 only - the single most important line here
    f1 = FRAMES[0]
    bx = -800
    p.append(f'<line x1="{bx}" y1="{f1["y"] - f1["h"] / 2 + 70}" x2="{bx}" '
             f'y2="{f1["y"] + f1["h"] / 2 - 30}" stroke="#d94c4c" stroke-width="3" '
             f'stroke-dasharray="14 10"/>')
    p.append(f'<text x="{bx - 14}" y="{f1["y"] - f1["h"] / 2 + 100}" font-size="20" '
             f'font-weight="700" fill="#d94c4c" text-anchor="end">CLAIM ←</text>')
    p.append(f'<text x="{bx + 14}" y="{f1["y"] - f1["h"] / 2 + 100}" font-size="20" '
             f'font-weight="700" fill="#2f8f4e">→ GROUND TRUTH (hardware you own)</text>')

    for a, b, label in EDGES:
        na, nb = by[a], by[b]
        x1, y1, x2, y2 = na["x"], na["y"], nb["x"], nb["y"]
        # leave the boxes from the nearer edge, so arrows do not cross the text
        if abs(x2 - x1) > abs(y2 - y1):
            x1 += (na["w"] / 2) * (1 if x2 > x1 else -1)
            x2 -= (nb["w"] / 2) * (1 if x2 > x1 else -1)
        else:
            y1 += (na["h"] / 2) * (1 if y2 > y1 else -1)
            y2 -= (nb["h"] / 2) * (1 if y2 > y1 else -1)
        p.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
                 f'stroke="#8a93a0" stroke-width="2.5" marker-end="url(#a)"/>')
        if label:
            p.append(f'<text x="{(x1 + x2) / 2:.0f}" y="{(y1 + y2) / 2 - 8:.0f}" '
                     f'font-size="17" fill="#5b6670" text-anchor="middle">{esc(label)}</text>')

    for n in NODES:
        _, fill, stroke, ink = KIND[n["kind"]]
        x, y = n["x"] - n["w"] / 2, n["y"] - n["h"] / 2
        p.append(f'<rect x="{x}" y="{y}" width="{n["w"]}" height="{n["h"]}" rx="12" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="2.5"/>')
        ty = y + 34
        for line in wrap(n["title"], n["w"] - 32, 21):
            p.append(f'<text x="{x + 16}" y="{ty:.0f}" font-size="21" font-weight="700" '
                     f'fill="{ink}">{esc(line)}</text>')
            ty += 26
        ty += 4
        for line in wrap(n["body"], n["w"] - 32, 16):
            if line:
                p.append(f'<text x="{x + 16}" y="{ty:.0f}" font-size="16" '
                         f'fill="{ink}" opacity="0.92">{esc(line)}</text>')
            ty += 20
    p.append("</svg>")
    return "\n".join(p)


# --------------------------------------------------------------------------
# Miro renderer
# --------------------------------------------------------------------------
def call(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"authorization": f"Bearer {token}", "accept": "application/json",
                 **({"content-type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode(errors="replace")[:400]
        # Never echo the token, and name the two causes that account for most
        # 401/403s here so nobody goes hunting the wrong one.
        hint = ""
        if e.code in (401, 403):
            hint = ("\n  -> the token needs scope boards:write, and the app must be "
                    "installed on the TEAM that owns this board")
        if e.code == 404:
            hint = "\n  -> check the board id: it is the part of the URL after /board/"
        raise SystemExit(f"Miro {e.code} on {method} {path}: {detail}{hint}")
    except Exception as e:
        raise SystemExit(f"Miro request failed: {e}")


def push(board: str, token: str, again: bool) -> None:
    # Refuse to double up. Re-running a creator against a board that already has
    # the diagram leaves two of everything stacked, which is worse than an error.
    existing = call("GET", f"/boards/{urllib.parse.quote(board, safe='')}"
                           f"/items?type=frame&limit=50", token)
    titles = {(i.get("data") or {}).get("title", "") for i in existing.get("data", [])}
    mine = [f["title"] for f in FRAMES if f["title"] in titles]
    if mine and not again:
        raise SystemExit(
            f"this board already has {len(mine)} frame(s) from a previous run "
            f"({mine[0]!r}). Delete them, or pass --again to add a second copy.")

    b = urllib.parse.quote(board, safe="")
    print(f"creating {len(FRAMES)} frames, {len(NODES)} shapes, {len(EDGES)} connectors")
    for f in FRAMES:
        call("POST", f"/boards/{b}/frames", token, {
            "data": {"title": f["title"], "format": "custom", "type": "freeform"},
            "position": {"x": f["x"], "y": f["y"]},
            "geometry": {"width": f["w"], "height": f["h"]},
            "style": {"fillColor": "#ffffff"}})
        time.sleep(0.12)                      # the API rate-limits bursts

    ids = {}
    for n in NODES:
        fill, _, _, _ = KIND[n["kind"]]
        html = f"<p><strong>{esc(n['title'])}</strong></p>"
        if n["body"]:
            for para in n["body"].split("\n"):
                html += f"<p>{esc(para)}</p>" if para.strip() else "<p></p>"
        r = call("POST", f"/boards/{b}/shapes", token, {
            "data": {"shape": "round_rectangle", "content": html},
            "position": {"x": n["x"], "y": n["y"]},
            "geometry": {"width": n["w"], "height": n["h"]},
            "style": {"fillColor": fill, "fontSize": "14", "textAlign": "left",
                      "textAlignVertical": "top", "borderWidth": "2.0"}})
        ids[n["id"]] = r["id"]
        print(f"  {n['id']:<10} {r['id']}")
        time.sleep(0.12)

    for a, bb, label in EDGES:
        body = {"startItem": {"id": ids[a]}, "endItem": {"id": ids[bb]},
                "style": {"strokeColor": "#8a93a0", "strokeWidth": "2",
                          "endStrokeCap": "arrow"},
                "shape": "elbowed"}
        if label:
            body["captions"] = [{"content": esc(label)}]
        call("POST", f"/boards/{b}/connectors", token, body)
        time.sleep(0.12)

    print(f"\ndone: https://miro.com/app/board/{board}/")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="validate and print the plan")
    ap.add_argument("--svg", metavar="PATH", help="write the diagram as an SVG")
    ap.add_argument("--push", action="store_true", help="create it on a Miro board")
    ap.add_argument("--board", default=os.environ.get("MIRO_BOARD", ""),
                    help="board id, the part of the URL after /board/")
    ap.add_argument("--again", action="store_true",
                    help="allow a second copy on a board that already has one")
    a = ap.parse_args()

    errs = validate()
    if errs:
        for e in errs:
            print(f"layout error: {e}", file=sys.stderr)
        raise SystemExit(2)

    if a.dry_run or not (a.svg or a.push):
        print(f"layout OK: {len(FRAMES)} frames, {len(NODES)} shapes, {len(EDGES)} connectors")
        for f in FRAMES:
            inf = [n for n in NODES if n["frame"] == f["id"]]
            print(f"\n  {f['title']}")
            for n in inf:
                print(f"    [{n['kind']:<8}] {n['title']}")
        if not (a.svg or a.push):
            print("\n(nothing written; pass --svg PATH or --push --board ID)")

    if a.svg:
        with open(a.svg, "w") as fh:
            fh.write(to_svg())
        print(f"\nwrote {a.svg}")

    if a.push:
        token = os.environ.get("MIRO_TOKEN", "")
        if not token:
            raise SystemExit(
                "MIRO_TOKEN is not set. Get one at https://developers.miro.com "
                "(your app -> Install app and get OAuth token, scope boards:write), "
                "then:  MIRO_TOKEN=... python3 tools/miro_board.py --push --board '<id>'")
        if not a.board:
            raise SystemExit("--board is required (the part of the URL after /board/)")
        push(a.board, token, a.again)


if __name__ == "__main__":
    main()
