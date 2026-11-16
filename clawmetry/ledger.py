"""Hash-chained, append-only event ledger: the evidence half of the control plane.

The deck's promise is "a replayable, signed record of intention vs action" and
"the audit trail records what the robot did, not just what it was told". Both
require a record that cannot be quietly edited after the fact, so every event
carries the hash of the one before it. Deleting, reordering or rewriting any
line breaks the chain at that point and every point after it, and `verify()`
names the first break.

Pure stdlib on purpose. This has to start on a venue laptop, a Jetson or a Mac
with no install step and no network, the same reason tools/robot-console has
zero npm dependencies.

WHY INGEST TIME IS THE PRIMARY TIMESTAMP
Events arrive from Vapi's cloud, a Jetson, a robot and a phone, and their
clocks disagree - the MetaCam in this project ships with a clock that is simply
wrong. So the ledger stamps `ts` itself on arrival from one clock, keeps what
the source claimed as `src_ts`, and records the difference as `skew_s`. The
causal chain is then orderable even when a source lies about the time.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL = 1

# Event kinds, named after the Layered Authority Stack (deck slide 7) so the
# ledger and the pitch use one vocabulary.
KINDS = {
    "utterance",     # WHO ASKED            - guest/staff speech or text
    "intent",        # WHAT WAS INTENDED    - the agent's tool call, before anything moves
    "prediction",    # what we expect to happen, recorded BEFORE the action
    "decision",      # the gate's answer: allow / deny / hold
    "intervention",  # WHO INTERVENED       - a human tapped Approve / Deny / Pause
    "command",       # what was actually sent to the robot
    "state",         # confirmed physical state sample from the bridge
    "outcome",       # WHAT IT DID          - final confirmed state, scored against the prediction
    "breach",        # a rule was violated
    "integrity",     # the ledger's own health: gaps, silence, restarts
    "policy",        # THE RULES THEMSELVES CHANGED - a guardrail was edited or
                     # signed off. Deliberately a first-class kind: an incident
                     # review has to be able to ask what the limits said at the
                     # time, and a config change that is not in the record is
                     # indistinguishable from one that never happened.
}

# WHO. "assistant" is the voice agent speaking, as distinct from "agent"
# deciding to call a tool, and "vapi" is the platform reporting on itself -
# three different authorities that a single "agent" would have blurred.
# "operator" is the person accountable for guest safety who signs the venue
# guardrail file - a different authority from "staff" working alongside the
# robot, and the only one whose name belongs on a policy change.
ACTORS = {"guest", "staff", "operator", "agent", "assistant", "gate", "robot",
          "system", "vapi"}


def _canon(obj: dict) -> bytes:
    """Deterministic bytes for hashing: sorted keys, no spacing drift."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "hash"}
    return hashlib.sha256(_canon(body)).hexdigest()


GENESIS = "0" * 64


@dataclass
class Ledger:
    """Append-only JSONL with a sha256 chain. One file per run."""

    path: Path
    run_id: str = ""
    robot_id: str = "unknown"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _seq: int = 0
    _prev: str = GENESIS
    _verified: tuple = ()           # (when, verdict) - see verify(max_age_s=)
    # Counted here rather than by the caller because there are three write
    # paths into this file and a count kept outside missed one of them.
    counts: dict = field(default_factory=dict)
    _subs: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.run_id:
            self.run_id = f"run_{int(time.time())}_{os.getpid()}"
        if self.path.exists():
            self._resume()

    def _resume(self):
        """Pick up an existing chain so a restart does not silently start a new one."""
        last = None
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
                    try:
                        k = json.loads(line).get("kind")
                    except ValueError:
                        continue
                    self.counts[k] = self.counts.get(k, 0) + 1
        if last:
            ev = json.loads(last)
            self._seq = ev.get("seq", 0)
            self._prev = ev.get("hash", GENESIS)

    # -- writing ---------------------------------------------------------

    def append(self, kind: str, actor: str = "system", data: dict | None = None,
               corr: str = "", src_ts: float | None = None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}; add it to KINDS deliberately")
        if actor not in ACTORS:
            raise ValueError(f"unknown actor {actor!r}")
        now = time.time()
        with self._lock:
            self._seq += 1
            ev = {
                "v": PROTOCOL,
                "seq": self._seq,
                "ts": round(now, 3),
                "src_ts": round(src_ts, 3) if src_ts is not None else None,
                "skew_s": round(now - src_ts, 3) if src_ts is not None else None,
                "kind": kind,
                "actor": actor,
                "run_id": self.run_id,
                "robot_id": self.robot_id,
                "corr": corr,
                "data": data or {},
                "prev": self._prev,
            }
            ev["hash"] = digest(ev)
            self._prev = ev["hash"]
            self.counts[kind] = self.counts.get(kind, 0) + 1
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())   # a crash must not lose the last breach
        for sub in list(self._subs):
            try:
                sub(ev)
            except Exception:           # a broken subscriber never breaks the record
                pass
        return ev

    def subscribe(self, fn):
        self._subs.append(fn)
        return lambda: self._subs.remove(fn)

    # -- reading ---------------------------------------------------------

    def read(self, since_seq: int = 0, limit: int = 0) -> list[dict]:
        out = []
        if not self.path.exists():
            return out
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("seq", 0) > since_seq:
                    out.append(ev)
        return out[-limit:] if limit else out

    def verify(self, max_age_s: float = 0.0) -> dict:
        """Walk the chain. Returns {ok, count, broken_at, reason, checked_age_s}.

        A tampered ledger is worth less than no ledger, because it is trusted.

        WHY THIS IS CACHEABLE AND THE DASHBOARD USES THAT
        This re-reads and re-hashes the whole file, which is the point: it has
        to see the bytes on disk to catch an edit made behind the process. On
        a venue laptop with a few hundred events that is free. On the Orin
        after a day of 2 Hz telemetry the file is ~10 MB, the walk takes
        seconds, and a dashboard polling once a second had four of them
        queued at all times - the robot's own CPU spent governing nothing but
        the page watching it, and the page's header never filled in.

        So a caller that asks repeatedly may accept a slightly stale answer by
        passing `max_age_s`. The answer then carries `checked_age_s` and the
        dashboard shows it, because "the chain was intact 7 seconds ago" is a
        different and weaker claim than "the chain is intact", and the weaker
        one must not be displayed as the stronger. `/api/verify` and anything
        producing evidence pass nothing and get the full walk.
        """
        if max_age_s > 0 and self._verified:
            age = time.time() - self._verified[0]
            if age < max_age_s:
                return {**self._verified[1], "checked_age_s": round(age, 1)}
        prev, count = GENESIS, 0
        for ev in self.read():
            count += 1
            if ev.get("prev") != prev:
                return self._verdict({"ok": False, "count": count, "broken_at": ev.get("seq"),
                                      "reason": "prev hash does not match the previous line"})
            if digest(ev) != ev.get("hash"):
                return self._verdict({"ok": False, "count": count, "broken_at": ev.get("seq"),
                                      "reason": "line contents do not match their own hash"})
            prev = ev["hash"]
        return self._verdict({"ok": True, "count": count, "broken_at": None,
                              "reason": "chain intact"})

    def _verdict(self, r: dict) -> dict:
        self._verified = (time.time(), r)
        return {**r, "checked_age_s": 0.0}
