#!/usr/bin/env python3
"""ClawMetry physical-AI collector: one process, zero dependencies.

    python3 server.py --upstream http://127.0.0.1:5175 --body agibot_d1w

It is four things at once, because at a venue four processes is three too many:

  THE TAP        POST /vapi is a drop-in replacement for the robot's tool
                 endpoint. Point Vapi (or the console) here instead of at the
                 robot: every call is recorded, predicted, gated and then
                 forwarded upstream unchanged. This is the only place in the
                 whole system that sees WHO ASKED and WHAT WAS INTENDED, and
                 it is on hardware you own.
  THE VOICE SINK POST /vapi/events takes Vapi's own server messages - final
                 transcripts, call status, the end-of-call report - so WHO
                 ASKED is the guest's recorded words and not an inference drawn
                 from a tool call. This half is the voice vendor's self-report
                 about a conversation we cannot otherwise see, so it is stamped
                 basis=vapi_self_report and carries their clock in src_ts. The
                 robot's side of the story stays on hardware you own; the two
                 are bound together in the chain, not merged into one voice.
  THE COLLECTORS background pollers for the bridge (confirmed physical state)
                 and the camera guard (people, breaches), plus POST /ingest
                 for anything else - ROS, approvals, a second robot.
  THE LEDGER     hash-chained, append-only, fsynced (ledger.py).
  THE DASHBOARD  GET / - one page, live over SSE.

THE TAP FORWARDS EVEN WHEN IT CANNOT GATE. An observer that breaks the robot
when it fails is an observer nobody installs twice. The one exception is an
explicit deny, which is the whole point of the gate.

Read-only with respect to the robot, by construction: the tap never originates
a command, it only passes through or refuses. Compromising this process cannot
make the robot do anything a caller did not already ask for. That is deliberate
- fleet-wide oversight that can also actuate is a fleet-wide remote control.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from correlate import Correlator, Policy
from ledger import Ledger
import compliance as compliance_mod
import policy as policy_mod
import venue as venue_mod
from frame import FrameGrabber
from predict import BrainJudge, CosmosJudge, kinematic_predict, verify

HERE = Path(__file__).resolve().parent
UNGATED = {"stop_robot", "robot_status", "get_state", "pause"}

# How long a guest's sentence can still be said to have caused an action.
# Long, because an escort takes minutes; a command older than this is simply
# recorded as unattributed rather than pinned on whatever was said last.
SPEECH_WINDOW_S = 120.0

# The robot's own HTTP surface is wider than /vapi. d1-vapi-server also serves
# GET /health, /status, /call and POST /move, /gait, /gesture, /command, /stop
# - publicly, behind the same token - and the OPERATOR CONSOLE drives the robot
# through those, not through the voice tools. A tap that only watches /vapi
# therefore watches half the system: before this, keyboard teleop reached the
# ledger only afterwards, as unattributed_motion. So the tap stands in front of
# the whole surface: read-only paths are forwarded verbatim, the ones that move
# the robot go through the same predict -> judge -> gate -> record pipeline as a
# tool call, under the name of the tool that does the same thing, and anything
# unknown is refused rather than quietly tunnelled.
PASSTHROUGH_GET = {"/health", "/status", "/call"}
REST_AS_TOOL = {"/move": "move_robot", "/command": "robot_command",
                "/gait": "set_gait", "/gesture": "do_gesture",
                "/stop": "stop_robot"}

# Voice events that are real but not evidence: interim words, speech-start
# flags, per-token model output. Counted so a quiet ledger is not mistaken for
# a quiet room, stored nowhere - a ledger holding every interim word is a
# transcript, and a transcript of spa guests is the thing we must not keep.
VOICE_CHATTER = {"speech-update", "conversation-update", "user-interrupted",
                 "hang", "model-output", "voice-input", "transfer-destination-request",
                 "language-change-detected", "phone-call-control"}


def _centroid(poly: list) -> tuple:
    """A point inside a zone, for asking what speed applies there. Good enough
    for a convex floor plan; a zone shaped badly enough to break it is a zone
    that needs measuring on site anyway."""
    if not poly:
        return (None, None)
    return (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))


class Hub:
    """Ledger + correlator + collectors + subscribers."""

    def __init__(self, args):
        self.args = args
        self.led = Ledger(Path(args.ledger), robot_id=args.robot_id)
        self.cor = Correlator(Policy(speed_cap_mps=args.speed_cap))
        # Prefer eaby's brain relay over vLLM direct: it carries his prompt and
        # verdict schema and files the verdict in his system too, so the two
        # dashboards cannot disagree about the same robot.
        self.judge = (BrainJudge(args.brain_url, args.brain_key)
                      if args.brain_url and args.brain_key else CosmosJudge())
        # The operator's guardrail file is the source of truth when present:
        # zones, speed caps, camera-off areas and the child rules all come from
        # the document the venue signs, not from code defaults.
        self.frames = FrameGrabber(args.rtsp)
        self.venue = venue_mod.load(args.venue) if args.venue else None
        self.comp = compliance_mod.load(args.compliance) if args.compliance else None
        self.zones = self._venue_zones() if self.venue else self._load_zones(args.zones)
        self._call_id = ""
        self.subs: list[queue.Queue] = []
        self.last_state: dict = {}
        self.breaches: list[dict] = []
        self.sources: dict = {}
        self._n = 0
        self.last_said: dict | None = None   # newest guest transcript, for attribution
        self._voice_dropped = 0              # partials and chatter: counted, not stored
        self._apply_venue()
        # Warm the frame cache only now. The camera-off zones have to be known
        # BEFORE a background thread starts opening the camera, or the opening
        # seconds of every run are grabbed under no privacy policy at all. The
        # previous ordering called start() further up, before self.frames
        # existed at all, so --judge died on an AttributeError at boot.
        if args.judge:
            self.frames.start()          # keep a warm frame; judge latency only
        self._replay()
        self.cor.expect("bridge")
        if args.guard:
            self.cor.expect("guard")
        self.led.append("integrity", actor="system", data={
            "event": "collector_started", "body": args.body,
            "upstream": args.upstream, "guard": args.guard or None,
            "cosmos": self.judge.configured,
            "venue": self.venue.name if self.venue else None,
            "venue_signed": bool(self.venue and self.venue.signed)})
        # The operator's own warnings go into the record, so an unsigned or
        # unmeasured venue is on the ledger and not only on a console someone
        # scrolled past.
        for w in (self.venue.warnings if self.venue else []):
            self.led.append("integrity", actor="system",
                            data={"event": "venue_warning", "detail": w})

    def _replay(self):
        """Rebuild the in-memory view from an existing ledger.

        Without this a restart shows an intact chain of 800 events beside an
        empty breach feed, which reads as "nothing has gone wrong" - the exact
        reassuring-blank this collector exists to abolish. Breaches are
        re-shown, never re-appended: the record is append-only and replaying
        it must not forge new entries.
        """
        for ev in self.led.read():
            d = ev.get("data") or {}
            if ev["kind"] == "breach":
                self.breaches.insert(0, d | {"seq": ev["seq"], "ts": ev["ts"]})
            elif ev["kind"] == "state":
                self.last_state = d
            src = d.get("source") or ev.get("actor")
            if src:
                self.sources[src] = {"last_ts": ev["ts"], "kind": ev["kind"]}
        del self.breaches[200:]

    def _apply_venue(self):
        """Push the venue file's numbers into everything that enforces them.

        Called at boot AND after an operator edit, so there is exactly one
        place where a guardrail becomes behaviour. A reload that updated the
        dashboard but not the correlator would show limits the robot is not
        actually keeping, which is worse than showing nothing.
        """
        if not self.venue:
            return
        pp = self.venue.person_policy()
        self.cor.pol.speed_cap_mps = self.venue.max_speed
        self.cor.pol.person_min_m = pp["hold_within_m"]
        self.cor.pol.child_freeze_m = pp["child_freeze_within_m"]
        self.cor.pol.child_height_under_m = pp["child_height_under_m"]
        self.cor.pol.latch_s = pp["latch_s"]
        self.cor.pol.stale_s = pp["stale_frame_s"]
        self.frames.off = self.venue.camera_off_zones()
        self.zones = self._venue_zones()

    # -- the operator's guardrails, editable ------------------------------

    def _venue_raw(self) -> dict:
        import yaml
        return yaml.safe_load(Path(self.venue.path).read_text()) or {}

    def venue_config(self) -> dict:
        """Everything the guardrails view needs to render itself."""
        if not self.venue:
            return {"configured": False,
                    "hint": "start with --venue clawmetry/venue/leukerbad_therme.yaml"}
        raw = self._venue_raw()
        v = self.venue
        zones = []
        for n, z in v.zones.items():
            zones.append({
                "name": n, "label": z.label, "polygon": z.polygon,
                "speed_cap": z.speed_cap, "restricted": z.restricted,
                "camera_off": z.camera_off, "requires_approval": z.requires_approval,
                "verified": z.verified, "reason": z.reason,
                # What the robot will ACTUALLY do here, after every clamp. The
                # gap between this and speed_cap is the whole point of the view.
                "effective_speed": v.speed_cap_at(*_centroid(z.polygon))
                                   if z.polygon else v.unverified_speed,
                "is_destination": n in v.destinations,
            })
        return {
            "configured": True, "path": v.path, "name": v.name, "area": v.area,
            "signed": v.signed, "signed_off_by": v.signed_off_by,
            "signed_off_date": v.signed_off_date,
            "limits": raw.get("limits") or {}, "people": raw.get("people") or {},
            "zones": sorted(zones, key=lambda z: z["name"]),
            "destinations": list(v.destinations),
            "warnings": list(v.warnings),
            "schema": policy_mod.editable_schema(),
        }

    def venue_plan(self, edits: list) -> dict:
        if not self.venue:
            return {"ok": False, "errors": ["no venue file is loaded"], "changes": []}
        return policy_mod.build_plan(self._venue_raw(), edits or []).as_dict()

    def venue_apply(self, edits: list, operator: str, reason: str = "",
                    confirm_loosen: bool = False) -> tuple[int, dict]:
        """Write the operator's change, record it, and reload. In that order.

        The record goes in BEFORE the reload so that a change which crashes the
        reload is still on the ledger. An edit that broke the collector and left
        no trace would be the one edit nobody could investigate.
        """
        if not self.venue:
            return 400, {"ok": False, "error": "no venue file is loaded"}
        operator = (operator or "").strip()
        if len(operator) < 3:
            return 400, {"ok": False, "error": "name the operator making this change; "
                                               "a guardrail change with no name on it is "
                                               "not accountable to anyone"}
        plan = policy_mod.build_plan(self._venue_raw(), edits or [])
        if plan.errors:
            return 400, {"ok": False, "errors": plan.errors}
        if not plan.changes:
            return 200, {"ok": True, "changed": 0, "note": "nothing to change"}
        if plan.loosens and not confirm_loosen:
            return 409, {"ok": False, "needs_confirmation": True,
                         "error": "this change gives the robot more freedom than it has "
                                  "now; re-send with confirm_loosen to accept that",
                         **plan.as_dict()}
        try:
            backup, _ = policy_mod.write_plan(self.venue.path, plan)
        except (OSError, KeyError, ValueError) as e:
            return 500, {"ok": False, "error": f"could not write the guardrail file: {e}"}
        ev = self.emit("policy", actor="operator", data={
            "event": "venue_edit", "by": operator, "reason": reason,
            "file": self.venue.path, "backup": backup,
            "loosened": plan.loosens,
            "changes": [c.as_dict() for c in plan.changes],
            "source": "operator_console"})
        try:
            self.venue = venue_mod.load(self.venue.path)
            self._apply_venue()
        except Exception as e:                      # noqa: BLE001 - must not die here
            self.emit("integrity", data={
                "event": "venue_reload_failed", "detail": str(e),
                "hint": f"the file was written and is recorded at seq {ev['seq']}; "
                        f"the previous version is at {backup}"})
            return 500, {"ok": False, "seq": ev["seq"],
                         "error": f"written and recorded, but reloading it failed: {e}"}
        for w in self.venue.warnings:
            self.emit("integrity", data={"event": "venue_warning", "detail": w})
        return 200, {"ok": True, "seq": ev["seq"], "changed": len(plan.changes),
                     "loosened": plan.loosens, "backup": backup,
                     "venue": self.venue_config()}

    def _venue_zones(self) -> list:
        """The venue's zones in the shape the predictor wants."""
        return [{"zone": n, "label": z.label, "polygon": z.polygon,
                 "restricted": z.restricted, "reason": z.reason}
                for n, z in self.venue.zones.items() if z.polygon]

    @staticmethod
    def _load_zones(path):
        if not path or not Path(path).exists():
            return []
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            return []

    # -- the one place events enter -------------------------------------

    def emit(self, kind, actor="system", data=None, corr="", src_ts=None) -> dict:
        ev = self.led.append(kind, actor=actor, data=data or {}, corr=corr, src_ts=src_ts)
        if kind == "state":
            self.last_state = ev["data"]
        src = (data or {}).get("source") or actor
        self.sources[src] = {"last_ts": ev["ts"], "kind": kind}
        for b in self.cor.feed(ev):
            self._record_breach(b)
        self._fanout(ev)
        return ev

    def _record_breach(self, b: dict):
        # The regulatory label and the reporting clock go INTO the record, not
        # onto a dashboard beside it. A deadline that exists only in a UI is a
        # deadline nobody can prove was known.
        if self.comp:
            lab = self.comp.label(b.get("rule", ""))
            b = {**b, "compliance": {
                "labels": lab["labels"], "reportable": lab["reportable"],
                "obligations": [{"citation": o["citation"], "audience": o["audience"],
                                 "report_by": o["report_by"],
                                 "report_by_text": o["report_by_text"],
                                 "confidence": o["confidence"]}
                                for o in lab["obligations"]]}}
        ev = self.led.append("breach", actor="system", data=b, corr=b.get("corr", ""))
        self.breaches.insert(0, ev["data"] | {"seq": ev["seq"], "ts": ev["ts"]})
        del self.breaches[200:]
        self._fanout(ev)

    def _fanout(self, ev):
        for q in list(self.subs):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=500)
        self.subs.append(q)
        return q

    def unsubscribe(self, q):
        if q in self.subs:
            self.subs.remove(q)

    def corr_id(self) -> str:
        self._n += 1
        return f"act_{int(time.time())}_{self._n}"

    # -- the tap ---------------------------------------------------------

    def handle_tool(self, name: str, args_: dict, raw: dict, call_id: str = "",
                    forward=None, source: str = "vapi_tap", deny=None,
                    enforced: bool = True) -> dict:
        """Record -> predict -> gate -> forward -> score. Returns the upstream reply.

        `forward` and `deny` let a caller that is not Vapi reuse this exact
        pipeline with its own wire shapes - the governing decision must not
        depend on which door the command came through.

        `enforced=False` means the tap is WATCHING, not standing in the path:
        the edge already ran the command and told us afterwards. The gate still
        forms an opinion, because the opinion is worth recording, but it is
        written down as an opinion. A ledger that said "allowed" about a
        command it could not have stopped would claim an authority it did not
        have, and that is the first claim an auditor should test.
        """
        corr = self.corr_id()
        # WHO ASKED. The tap sees the tool call; only Vapi's transcript sees
        # the sentence that caused it. With the voice sink wired, bind the two
        # inside the chain: the utterance is re-entered against this action's
        # corr with a pointer back to the original event, so the evidence for
        # "a guest asked for this" is one hop and not a by-hand comparison of
        # two timestamps months later.
        said = self.last_said
        age = round(time.time() - said["ts"], 1) if said else None
        asked = {}
        if said and age is not None and age <= SPEECH_WINDOW_S:
            asked = {"asked": said["text"], "asked_age_s": age,
                     "asked_seq": said["seq"], "call_id": said.get("call_id", "")}
            self.emit("utterance", actor="guest", corr=corr,
                      data={"text": said["text"], "basis": "vapi_transcript",
                            "linked_seq": said["seq"], "age_s": age, "replayed": True,
                            "call_id": said.get("call_id", "")})
        self.emit("intent", actor="agent", corr=corr,
                  data={"tool": name, "args": args_, "source": source,
                        "speech_seen": self.last_said is not None, **asked})

        decision, reason = "allow", ""
        pred = None
        if name not in UNGATED:
            pred = kinematic_predict(name, args_, self.last_state, self.args.body, self.zones)
            self.emit("prediction", actor="system", corr=corr, data=pred.as_dict())
            if pred.predicted_breach:
                decision, reason = "deny", pred.reason
            # Cosmos judges behind the kinematic gate, never in front of it.
            # Only translating actions get a world-model opinion. The
            # kinematic predictor already knows the distance; a gesture is
            # 0 m and the judge's "is the path ahead clear" question simply
            # does not apply to it. Asking anyway denied a nod in a room with
            # eight people in it, and cost 8 s doing so.
            translates = pred is not None and pred.distance_m > 0.01
            if self.judge.configured and self.args.judge and not translates:
                self.emit("prediction", actor="system", corr=corr,
                          data={"basis": "cosmos", "available": False,
                                "skipped": True, "predicted_breach": False,
                                "reason": f"{name} does not translate the robot "
                                          f"(0 m): no world-model question to ask"})
            if self.judge.configured and self.args.judge and translates:
                jpeg = (self.frames.grab(self.last_state.get("zone", ""))
                        if isinstance(self.judge, BrainJudge) else b"")
                v = (self.judge.judge(name, args_, self._scene(), jpeg)
                     if isinstance(self.judge, BrainJudge)
                     else self.judge.judge(name, args_, self._scene()))
                if not jpeg and isinstance(self.judge, BrainJudge):
                    v = {"available": False, "reason": self.frames.last_error or "no frame"}
                self.emit("prediction", actor="system", corr=corr,
                          data={"basis": "cosmos", **v,
                                "predicted_breach": bool(v.get("unsafe"))})
                if v.get("available") and v.get("unsafe"):
                    decision, reason = "deny", v.get("reason", "world model refused")

        self.emit("decision", actor="gate", corr=corr,
                  data={"decision": decision, "reason": reason, "tool": name,
                        "enforced": enforced})
        if decision == "deny" and not enforced:
            # It already happened. Saying "denied" here would be a lie, and
            # returning early would lose the record of what the robot actually
            # did - the only part of this we still have.
            self.emit("integrity", corr=corr, data={
                "event": "gate_could_not_enforce", "tool": name, "reason": reason,
                "detail": "the gate would have refused this, but the tap is not in "
                          "the path: the edge ran it and reported afterwards"})
        elif decision == "deny":
            if deny:
                return deny(reason)
            return {"results": [{"toolCallId": call_id,
                                 "result": f"Refused: {reason}"}]}

        t0 = time.time()
        self._call_id = call_id
        before = dict(self.last_state)
        self.emit("command", actor="agent", corr=corr,
                  data={"tool": name, "args": args_, "source": source})
        reply, err = (forward(raw) if forward else self._forward(raw))
        dt = time.time() - t0

        after = self._poll_bridge_once() or dict(self.last_state)
        pose = ([after.get("x"), after.get("y"), after.get("yaw", 0.0)]
                if after.get("has_pose") else None)
        # Score against the prediction we actually made. An earlier version
        # scored against the START pose with a 0 s duration, which turned every
        # successful action into a large fake deviation.
        scored = (verify(pred.as_dict(), {"end_pose": pose, "duration_s": dt}, self.args.body)
                  if pred else {"deviation_z": 0.0, "basis": "none", "duration_scored": False})
        self.emit("outcome", actor="robot", corr=corr,
                  data={"tool": name, "duration_s": round(dt, 2), "end_pose": pose,
                        "error": err, **scored})
        return reply

    # -- the robot's own REST surface, governed ---------------------------

    def handle_observed_tool(self, name: str, args_: dict, call_id: str = "",
                             result: str = "", code: int = 200,
                             source: str = "tee_from_edge") -> dict:
        """A tool call the EDGE already executed, reported to us afterwards.

        This exists because the public funnel points at d1-vapi-server, not at
        this tap. Rather than open a second public door, the edge tees a copy
        of every call here over loopback. The tap cannot refuse anything on
        this path, and says so in the record - see `enforced` above.
        """
        err = None if code < 400 else f"the robot returned HTTP {code}: {result}"
        return self.handle_tool(
            name, args_, {}, call_id=call_id, source=source, enforced=False,
            forward=lambda _raw: ({"results": [{"toolCallId": call_id,
                                                "result": result}]}, err))

    def handle_rest(self, path: str, body: dict) -> tuple[int, dict]:
        """A direct POST to the robot's REST surface, through the same gate.

        The authority is a console rather than a guest; everything else about
        the decision is identical, and it lands in the same ledger with
        source=rest_direct so the record says which door was used.
        """
        up = {}

        def fwd(_raw):
            reply, err, code = self._forward_path(path, body)
            up["code"] = code                 # the robot's own status code, kept
            return reply, err

        reply = self.handle_tool(REST_AS_TOOL[path], dict(body), {"rest_path": path},
                                 call_id="", source="rest_direct", forward=fwd,
                                 deny=lambda why: {"__refused__": why})
        if "__refused__" in reply:
            return 403, {"ok": False, "error": reply["__refused__"],
                         "refused_by": "clawmetry", "path": path}
        return up.get("code", 200), reply

    def _forward_path(self, path: str, body: dict):
        """POST to the robot verbatim. Returns (body, error, status).

        A drop-in has to be transparent about failure too: the robot's own
        status code and message go back to the caller unchanged, so a console
        showing "cannot nod while not standing" keeps showing exactly that.
        """
        data = json.dumps(body).encode()
        headers = {"content-type": "application/json", "x-clawmetry-forwarded": "1"}
        if self.args.token:
            headers["authorization"] = f"Bearer {self.args.token}"
        try:
            req = urllib.request.Request(f"{self.args.upstream}{path}", data=data,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.args.timeout) as r:
                return json.loads(r.read() or b"{}"), None, r.status
        except urllib.error.HTTPError as e:
            raw = e.read() or b""
            try:
                up = json.loads(raw)
            except ValueError:
                up = {"ok": False, "error": raw.decode(errors="replace")[:200]}
            return up, f"HTTP {e.code}", e.code
        except Exception as e:
            return ({"ok": False, "error": f"robot endpoint unreachable: {e}"},
                    str(e), 502)

    def forward_readonly(self, path: str) -> tuple[int, bytes, str]:
        """GET something that cannot move the robot, verbatim.

        Recorded only on failure: logging a 2 Hz console poll as an event
        would bury the ledger in noise, while a robot endpoint that has
        started refusing is exactly what nobody notices.
        """
        headers = {}
        if self.args.token:
            headers["authorization"] = f"Bearer {self.args.token}"
        try:
            req = urllib.request.Request(f"{self.args.upstream}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=10.0) as r:
                return r.status, r.read(), r.headers.get("content-type", "application/json")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"{}"), "application/json"
        except Exception as e:
            self.emit("integrity", actor="system",
                      data={"event": "passthrough_failed", "path": path, "error": str(e)})
            return 502, json.dumps({"ok": False, "error": str(e)}).encode(), "application/json"

    # -- the voice sink ---------------------------------------------------

    def handle_vapi_event(self, payload: dict) -> dict:
        """Vapi's server messages: the STT and LLM layer, in the vendor's words.

        Stamped basis=vapi_self_report with their clock in src_ts, so the
        ledger can show how far their clock sits from ours instead of
        pretending there is a single timeline. Nothing here is treated as
        instruction: it is a record of what a guest and a model said.
        """
        m = payload.get("message") or payload
        typ = str(m.get("type") or "")
        call = m.get("call") or {}
        call_id = call.get("id") or m.get("callId") or ""
        src_ts = m.get("timestamp")
        if isinstance(src_ts, (int, float)) and src_ts > 1e11:
            src_ts = src_ts / 1000.0          # Vapi stamps in milliseconds
        elif not isinstance(src_ts, (int, float)):
            src_ts = None

        if typ.startswith("transcript"):
            # Subscribing to finals only gives the type
            # transcript[transcriptType="final"], where the field itself may be
            # absent. Reading just the field would then drop every transcript
            # on the configuration we actually want to run.
            final = ('transcriptType="final"' in typ
                     or str(m.get("transcriptType") or "") == "final")
            if not final:
                self._voice_dropped += 1
                return {"ok": True, "stored": False, "reason": "partial"}
            text = str(m.get("transcript") or "").strip()
            if not text:
                self._voice_dropped += 1
                return {"ok": True, "stored": False, "reason": "empty"}
            role = str(m.get("role") or "")
            who = "guest" if role == "user" else "assistant"
            kept = self._maybe_redact(text)
            ev = self.emit("utterance", actor=who, src_ts=src_ts,
                           data={"type": "transcript", "text": kept, "role": role,
                                 "basis": "vapi_self_report", "call_id": call_id})
            if who == "guest":
                self.last_said = {"text": kept, "ts": ev["ts"],
                                  "seq": ev["seq"], "call_id": call_id}
            return {"ok": True, "stored": True, "seq": ev["seq"]}

        if typ == "status-update":
            ev = self.emit("utterance", actor="vapi", src_ts=src_ts,
                           data={"type": "status-update", "status": m.get("status"),
                                 "ended_reason": m.get("endedReason"),
                                 "basis": "vapi_self_report", "call_id": call_id})
            return {"ok": True, "stored": True, "seq": ev["seq"]}

        if typ == "end-of-call-report":
            # NO AUDIO, EVER. This report carries recording URLs. The venue
            # forbids photography inside and we do not hold a pointer to a
            # recording of a guest either: transcript text and timings only,
            # and the fact that audio was offered and dropped is itself
            # recorded so the omission is visible rather than assumed.
            art = m.get("artifact") or {}
            tr = str(m.get("transcript") or art.get("transcript") or "")
            # THE VOICE STACK'S OWN TIMING. Vapi exposes no customer OTLP
            # endpoint - observabilityPlan takes provider "langfuse" and
            # nothing else, and that host must be public HTTPS, so it cannot
            # be pointed at this Orin. Per-turn stage latencies arrive here
            # instead, in milliseconds, measured on Vapi's servers: this is
            # the STT -> endpointing -> LLM -> TTS breakdown, from the vendor,
            # for the stretch of the pipeline we cannot instrument ourselves.
            pm = art.get("performanceMetrics") or {}
            stages = {k: pm.get(v) for k, v in (
                ("stt_ms", "transcriberLatencyAverage"),
                ("endpointing_ms", "endpointingLatencyAverage"),
                ("llm_ms", "modelLatencyAverage"),
                ("tts_ms", "voiceLatencyAverage"),
                ("turn_ms", "turnLatencyAverage")) if pm.get(v) is not None}
            if pm:
                stages["turns_measured"] = len(pm.get("turnLatencies") or [])
                stages["user_interrupted"] = pm.get("numUserInterrupted")
            ev = self.emit("utterance", actor="vapi", src_ts=src_ts,
                           data={"type": "end-of-call-report",
                                 "ended_reason": m.get("endedReason"),
                                 "duration_s": m.get("durationSeconds"),
                                 "summary": self._maybe_redact(str(m.get("summary") or "")),
                                 "transcript": self._maybe_redact(tr[:20000]),
                                 "transcript_chars": len(tr),
                                 "stage_latency_ms": stages or None,
                                 "audio_dropped": bool(m.get("recordingUrl")
                                                       or m.get("stereoRecordingUrl")
                                                       or art.get("recordingUrl")
                                                       or art.get("stereoRecordingUrl")),
                                 "basis": "vapi_self_report", "call_id": call_id})
            return {"ok": True, "stored": True, "seq": ev["seq"]}

        if typ in VOICE_CHATTER:
            self._voice_dropped += 1
            return {"ok": True, "stored": False, "reason": "chatter"}

        # An unrecognised message type is reported, not swallowed. Reading a
        # shape we did not expect as though we understood it is exactly how a
        # world-model stop verdict once arrived at the gate as an approval.
        ev = self.emit("integrity", actor="system",
                       data={"event": "voice_event_unrecognised", "type": typ,
                             "keys": sorted(str(k) for k in m.keys())[:20],
                             "call_id": call_id})
        return {"ok": True, "stored": True, "seq": ev["seq"], "recognised": False}

    def _maybe_redact(self, text: str) -> str:
        """--redact-transcripts: keep the shape of what was said, not the words."""
        if not getattr(self.args, "redact_transcripts", False) or not text:
            return text
        return (f"[redacted {len(text)} chars "
                f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:12]}]")

    def _scene(self) -> dict:
        s = self.last_state
        return {k: s.get(k) for k in
                ("nearest_person_m", "people_within_2m", "child_near", "zone", "moving")}

    def _forward(self, raw: dict):
        body = json.dumps(raw).encode()
        # Tells the edge this already came THROUGH the tap, so its tee stays
        # quiet. Without it, repointing the funnel at 8901 would record every
        # command twice - once governed, once as an unenforced observation.
        headers = {"content-type": "application/json", "x-clawmetry-forwarded": "1"}
        if self.args.token:
            headers["authorization"] = f"Bearer {self.args.token}"
        try:
            req = urllib.request.Request(f"{self.args.upstream}/vapi", data=body,
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.args.timeout) as r:
                return json.loads(r.read() or b"{}"), None
        except urllib.error.HTTPError as e:
            # THE ROBOT ANSWERED - with a refusal. Calling that "unreachable"
            # is a lie told to the guest as well as the ledger: a 400 here is
            # usually the robot saying it cannot do that from this posture,
            # and that is the sentence the guest should hear.
            body_bytes = e.read() or b""
            try:
                up = json.loads(body_bytes)
            except ValueError:
                up = {}
            if isinstance(up, dict) and up.get("results"):
                return up, f"HTTP {e.code}"
            detail = ((up.get("error") or up.get("detail")) if isinstance(up, dict) else None) \
                or body_bytes.decode(errors="replace")[:200] or f"HTTP {e.code}"
            return ({"results": [{"toolCallId": self._call_id,
                                  "result": f"The robot refused: {detail}"}]},
                    f"HTTP {e.code}: {detail}")
        except Exception as e:
            # Forward failures are recorded, never swallowed, and never
            # reported upstream as success.
            return ({"results": [{"toolCallId": self._call_id,
                                  "result": f"Robot endpoint unreachable: {e}"}]}, str(e))

    # -- collectors -------------------------------------------------------

    def _poll_bridge_once(self) -> dict | None:
        tok = self.args.state_token
        headers = {"authorization": f"Bearer {tok}"} if tok else {}
        try:
            req = urllib.request.Request(f"{self.args.state_url}/status", headers=headers)
            with urllib.request.urlopen(req, timeout=3.0) as r:
                s = json.loads(r.read())
            self._poll_err = 0
        except Exception as e:
            # A collector that cannot collect is a finding, not a blank. Report
            # the first failure and then every 30th, so a dead feed is loud once
            # and does not drown the ledger.
            self._poll_err = getattr(self, "_poll_err", 0) + 1
            if self._poll_err == 1 or self._poll_err % 30 == 0:
                self.emit("integrity", actor="system",
                          data={"event": "collector_failing", "collector": "bridge",
                                "target": f"{self.args.state_url}/status",
                                "consecutive": self._poll_err, "error": str(e)})
            return None
        has_pose = "x" in s and "y" in s
        d = {"source": "bridge", "connected": bool(s.get("connected")),
             "moving": bool(s.get("moving")), "ctrl_mode": s.get("ctrl_mode"),
             "battery_pct": s.get("battery_pct"), "gait": s.get("gait"),
             "has_pose": has_pose, "speed_mps": s.get("speed_mps")}
        if has_pose:
            d |= {"x": s.get("x"), "y": s.get("y"), "yaw": s.get("yaw", 0.0)}
        self.emit("state", actor="robot", data=d, src_ts=s.get("ts"))
        return d

    def run_collectors(self):
        threading.Thread(target=self._loop_bridge, daemon=True).start()
        if self.args.guard:
            threading.Thread(target=self._loop_guard, daemon=True).start()
        threading.Thread(target=self._loop_tick, daemon=True).start()

    def _loop_bridge(self):
        while True:
            self._poll_bridge_once()
            time.sleep(self.args.state_hz and 1.0 / self.args.state_hz or 1.0)

    def _loop_guard(self):
        while True:
            try:
                with urllib.request.urlopen(f"{self.args.guard}/guard", timeout=2.0) as r:
                    g = json.loads(r.read())
                self.emit("state", actor="robot", data={
                    "source": "guard", "state": g.get("state"), "zone": g.get("zone"),
                    "nearest_person_m": g.get("nearest_m"), "people": g.get("people"),
                    "child_near": g.get("child_near"), "latched": g.get("latched"),
                    "stale": g.get("stale"), "moving": False})
            except Exception:
                pass
            time.sleep(1.0)

    def _loop_tick(self):
        while True:
            for b in self.cor.tick(time.time()):
                self._record_breach(b)
            time.sleep(0.5)

    # -- snapshot ---------------------------------------------------------

    def evidence(self) -> dict:
        """What the running system can actually demonstrate, right now."""
        return {
            "ledger_chained": self.led.verify()["ok"],
            "ledger_signed": False,          # chaining proves consistency, not origin
            "venue_signed": bool(self.venue and self.venue.signed),
            "zones_measured": bool(self.venue and self.venue.zones
                                   and all(z.is_verified for z in self.venue.zones.values())),
            "approval_path": False,          # ClawMetry Approve/Deny not wired to this tap yet
            "ai_disclosure": False,          # the assistant says it, nothing here proves it
            "child_detector_tested": False,  # the rule is enforced; the sensing is unproven
            "performance_level": False,      # no PL/SIL determination exists
            "risk_assessment": False,
            "dpia": False,
            "clocks_running": bool(self.comp),
        }

    def snapshot(self) -> dict:
        now = time.time()
        return {
            "ts": round(now, 3),
            "run_id": self.led.run_id, "robot_id": self.led.robot_id,
            "body": self.args.body,
            "chain": self.led.verify(),
            "sources": {k: {**v, "age_s": round(now - v["last_ts"], 1)}
                        for k, v in self.sources.items()},
            "robot": self.last_state,
            "open_actions": [{"corr": o.corr, "tool": o.tool, "decision": o.decision,
                              "moved": o.moved, "age_s": round(now - o.t_intent, 1)}
                             for o in self.cor.open.values()],
            "breaches": self.breaches[:40],
            "counts": self._counts(),
            "cosmos": {"configured": self.judge.configured, "enabled": bool(self.args.judge)},
            "compliance": ({"configured": True,
                            "reportable_open": len([c for c in
                                self.comp.evidence_pack(self.led.read(), self.evidence())["clocks"]
                                if not c["overdue"]]),
                            "overdue": len(self.comp.evidence_pack(
                                self.led.read(), self.evidence())["overdue"])}
                           if self.comp else {"configured": False}),
            "venue": ({"name": self.venue.name, "area": self.venue.area,
                       "signed": self.venue.signed, "signed_by": self.venue.signed_off_by,
                       "zones": len(self.venue.zones),
                       "restricted": sum(1 for z in self.venue.zones.values() if z.restricted),
                       "warnings": self.venue.warnings} if self.venue else None),
            "upstream": self.args.upstream,
        }

    def _counts(self) -> dict:
        c: dict = {}
        for ev in self.led.read():
            c[ev["kind"]] = c.get(ev["kind"], 0) + 1
        return c


class Handler(BaseHTTPRequestHandler):
    hub: Hub = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _authed(self) -> bool:
        """True when no inbound token is set, or the caller presents it.

        In a real deployment the tap has to be reachable from the internet,
        because Vapi's cloud is what calls it - and it holds guest speech and a
        gate in front of a robot. Three ways in, because there are three kinds
        of caller: a bearer header (our own collectors and Vapi's tool calls,
        which already send the robot's token), X-Vapi-Secret (what Vapi sends
        with server messages), and ?k= for a browser, which is then handed a
        cookie so the key stops appearing in every later URL.
        """
        want = self.hub.args.inbound_token
        if not want:
            return True
        auth = self.headers.get("authorization") or ""
        cand = [auth[7:] if auth[:7].lower() == "bearer " else auth,
                self.headers.get("x-vapi-secret") or ""]
        cand += urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("k", [])
        for part in (self.headers.get("cookie") or "").split(";"):
            kv = part.strip().split("=", 1)
            if len(kv) == 2 and kv[0] == "cm_k":
                cand.append(kv[1])
        return any(hmac.compare_digest(c, want) for c in cand if c)

    def do_GET(self):
        p = self.path.split("?")[0]
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if p == "/":
            body = (HERE / "dashboard.html").read_bytes()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            if self.hub.args.inbound_token and "k=" in self.path:
                # The page's own fetches cannot carry a header, so hand the
                # browser a cookie and keep the key out of /api/... URLs.
                self.send_header("set-cookie", f"cm_k={self.hub.args.inbound_token}; "
                                               "Path=/; HttpOnly; SameSite=Lax")
            self.end_headers()
            return self.wfile.write(body)
        if p == "/api/state":
            return self._json(200, self.hub.snapshot())
        if p == "/api/ledger":
            return self._json(200, {"events": self.hub.led.read(limit=300)})
        if p == "/api/verify":
            return self._json(200, self.hub.led.verify())
        if p == "/api/compliance":
            if not self.hub.comp:
                return self._json(200, {"configured": False,
                                        "hint": "start with --compliance clawmetry/compliance.yaml"})
            pack = self.hub.comp.evidence_pack(self.hub.led.read(), self.hub.evidence())
            return self._json(200, {"configured": True, **pack})
        if p == "/api/venue":
            return self._json(200, self.hub.venue_config())
        if p == "/api/events":
            return self._sse()
        if p in PASSTHROUGH_GET:
            code, body, ctype = self.hub.forward_readonly(self.path)
            return self._send(code, body, ctype)
        return self._json(404, {"error": "not found"})

    def _sse(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-store")
        self.send_header("connection", "keep-alive")
        self.end_headers()
        q = self.hub.subscribe()
        try:
            # Replay the ACTIONS, not the telemetry. State samples arrive at
            # 2 Hz, so a flat "last 30 events" is 15 seconds of heartbeat and
            # the ledger panel opens empty on a run full of real decisions.
            # Keep every correlated event plus the newest state for liveness.
            hist = self.hub.led.read()
            acts = [e for e in hist if e["kind"] != "state"][-80:]
            latest_state = next((e for e in reversed(hist) if e["kind"] == "state"), None)
            for ev in acts + ([latest_state] if latest_state else []):
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=1.0)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")   # silence must look alive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.hub.unsubscribe(q)

    def do_POST(self):
        p = self.path.split("?")[0]
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        n = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})

        if p == "/vapi":
            calls = (((payload.get("message") or {}).get("toolCallList")) or [])
            results = []
            for c in calls:
                a = c.get("arguments") or c.get("function", {}).get("arguments") or {}
                if isinstance(a, str):
                    try:
                        a = json.loads(a)
                    except ValueError:
                        a = {}
                name = c.get("name") or c.get("function", {}).get("name") or ""
                out = self.hub.handle_tool(name, a, payload, c.get("id", ""))
                results.extend(out.get("results") or [])
            return self._json(200, {"results": results})

        if p in REST_AS_TOOL:
            code, out = self.hub.handle_rest(p, payload if isinstance(payload, dict) else {})
            return self._json(code, out)

        if p == "/status":
            code, body, ctype = self.hub.forward_readonly("/status")
            return self._send(code, body, ctype)

        if p in ("/vapi/events", "/vapi-events"):
            return self._json(200, self.hub.handle_vapi_event(payload))

        if p == "/ingest":
            ev = self.hub.emit(payload.get("kind", "integrity"),
                               actor=payload.get("actor", "system"),
                               data=payload.get("data") or {},
                               corr=payload.get("corr", ""),
                               src_ts=payload.get("src_ts"))
            return self._json(200, {"ok": True, "seq": ev["seq"]})

        if p == "/observe/tool":
            # The edge tees its traffic here. Loopback only - see the bind
            # address; nothing about this path is reachable from outside.
            return self._json(200, self.hub.handle_observed_tool(
                payload.get("name", ""), payload.get("args") or {},
                call_id=payload.get("call_id", ""),
                result=str(payload.get("result", ""))[:2000],
                code=int(payload.get("code", 200) or 200),
                source=payload.get("source", "tee_from_edge")))

        if p == "/api/venue/plan":
            # Dry run: says what each edit does to the robot and never writes.
            return self._json(200, self.hub.venue_plan(payload.get("edits") or []))

        if p == "/api/venue/apply":
            code, out = self.hub.venue_apply(
                payload.get("edits") or [], payload.get("operator", ""),
                reason=payload.get("reason", ""),
                confirm_loosen=bool(payload.get("confirm_loosen")))
            return self._json(code, out)

        if p == "/intervention":
            # WHO INTERVENED: a human tapped Approve / Deny / Pause.
            self.hub.emit("intervention", actor="staff", corr=payload.get("corr", ""),
                          data={"decision": payload.get("decision"),
                                "by": payload.get("by", "operator"),
                                "reason": payload.get("reason", "")})
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--upstream", default="http://127.0.0.1:5175",
                    help="the robot tool endpoint this tap forwards to")
    ap.add_argument("--state-url", default="",
                    help="where to poll confirmed state; defaults to --upstream. "
                         "On the Jetson this is the ros-agent (:8091), which needs no "
                         "token - the edge's GET /status 401s even WITH the right one, "
                         "because its auth check runs before routing.")
    ap.add_argument("--state-token", default="", help="bearer for --state-url, if it needs one")
    ap.add_argument("--guard", default="", help="tools/robot-guard base url")
    ap.add_argument("--body", default="agibot_d1w", choices=["agibot_d1w", "unitree_g1", "fake"])
    ap.add_argument("--robot-id", default="leukerbad-d1w")
    ap.add_argument("--ledger", default="./clawmetry-ledger.jsonl")
    ap.add_argument("--zones", default="", help="JSON list of zone boxes (legacy)")
    ap.add_argument("--compliance", default="",
                    help="regulatory label + reporting-clock registry, e.g. "
                         "clawmetry/compliance.yaml. A drafting aid, not legal advice.")
    ap.add_argument("--venue", default="",
                    help="the operator's guardrail file, e.g. "
                         "clawmetry/venue/leukerbad_therme.yaml. Overrides --zones "
                         "and supplies the speed, people and child rules.")
    ap.add_argument("--token", default="", help="bearer for the upstream robot")
    ap.add_argument("--speed-cap", type=float, default=0.4)
    ap.add_argument("--state-hz", type=float, default=2.0)
    ap.add_argument("--timeout", type=float, default=40.0)
    ap.add_argument("--brain-url", default="", help="eaby's brain relay, e.g. http://spark:8780")
    ap.add_argument("--brain-key", default="", help="BRAIN_API_KEY (prefer --env-file)")
    ap.add_argument("--rtsp", default="", help="robot camera, for the world-model judge")
    ap.add_argument("--env-file", default="", help="read BRAIN_URL/BRAIN_API_KEY/ROBOT_RTSP "
                                                   "from a .env so keys stay off the command "
                                                   "line and out of `ps`")
    ap.add_argument("--judge", action="store_true", help="enable the Cosmos world-model judge")
    ap.add_argument("--inbound-token", default="",
                    help="require this bearer (or X-Vapi-Secret, or ?k=) on every "
                         "request. Set it whenever the tap is reachable from off "
                         "the host: it holds guest speech and a gate in front of a "
                         "robot. Prefer CLAWMETRY_INBOUND_TOKEN - argv is world-readable")
    ap.add_argument("--redact-transcripts", action="store_true",
                    help="store the length and a hash of what was said, not the words")
    a = ap.parse_args()
    if a.env_file:
        # Keys via file, never argv: anything on the command line is visible to
        # every user on the box through `ps`.
        for line in open(a.env_file):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "BRAIN_URL" and not a.brain_url:
                    a.brain_url = v
                elif k == "BRAIN_API_KEY" and not a.brain_key:
                    a.brain_key = v
                elif k == "ROBOT_RTSP" and not a.rtsp:
                    a.rtsp = v
    # Tokens from the environment by preference: every argument is visible to
    # every user on the box through `ps`.
    a.inbound_token = a.inbound_token or os.environ.get("CLAWMETRY_INBOUND_TOKEN", "")
    a.token = a.token or os.environ.get("CLAWMETRY_UPSTREAM_TOKEN", "")
    if not a.state_url:
        a.state_url = a.upstream
        a.state_token = a.token

    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        raise SystemExit(f"cannot bind {a.host}:{a.port} ({e}). Another collector is "
                         f"probably still running - it would keep serving while this "
                         f"one wrote a ledger nobody reads.")
    hub = Hub(a)
    hub.run_collectors()
    Handler.hub = hub
    print(f"clawmetry collector  http://{a.host}:{a.port}")
    print(f"  body={a.body}  tools->{a.upstream}  state->{a.state_url}  guard={a.guard or 'none'}")
    print(f"  cosmos={'on' if (a.judge and hub.judge.configured) else 'off'}  ledger={a.ledger}")
    print(f"  voice sink POST /vapi/events  transcripts="
          f"{'hashed only' if a.redact_transcripts else 'stored'}")
    if a.inbound_token:
        print("  inbound auth=on (bearer / X-Vapi-Secret / ?k=)")
    elif a.host != "127.0.0.1":
        print(f"  ! inbound auth=OFF and bound to {a.host}: anyone who can reach "
              f"this port can read guest speech and refuse robot commands")
    if hub.venue:
        print(f"  venue={hub.venue.name} ({len(hub.venue.zones)} zones, "
              f"{'SIGNED by ' + hub.venue.signed_off_by if hub.venue.signed else 'NOT SIGNED OFF'})")
        for w in hub.venue.warnings:
            print(f"    ! {w}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        hub.led.append("integrity", data={"event": "collector_stopped"})
        print("\nstopped; chain:", hub.led.verify())


if __name__ == "__main__":
    main()
