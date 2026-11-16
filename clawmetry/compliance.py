"""Regulatory labelling, reporting clocks, and an honest readiness report.

WHAT THIS IS
    It labels each breach with the obligation it may trigger, starts a clock
    when one fires, and exports an evidence pack an auditor can read.

WHAT THIS IS NOT
    A certificate, or a route to one by itself. ISO/IEC 42001 and 27001
    certificates are issued by accredited certification bodies after an audit.
    CE marking is a manufacturer's declaration. No file in this repository
    confers conformity, and a tool that implied otherwise would be found out by
    the first competent auditor - which is a worse outcome than having no tool.

    What it does instead is the part that actually takes months: produce the
    evidence, and say plainly which controls have none. `readiness()` is
    deliberately written to report gaps rather than a score, because a
    percentage invites rounding up and a gap list does not.

DEADLINES ARE FROM AWARENESS, NOT FROM OCCURRENCE. Both are recorded: the
breach's own timestamp, and when the obligation was raised. If those differ by
a long way, that gap is itself the finding.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# Textual deadlines that are not a fixed duration. Kept as words on purpose:
# writing "as soon as possible" as a number would invent a legal position.
NON_NUMERIC = {
    "without_undue_delay": "without undue delay",
    "as_soon_as_possible": "as soon as possible",
    "at_interaction": "at the moment of interaction",
    "continuous": "continuously",
    "before_deployment": "before deployment",
}


def parse_within(s: str) -> float | None:
    """'72h' -> seconds. Non-numeric deadlines return None, never 0."""
    if not s or s in NON_NUMERIC:
        return None
    s = str(s).strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s[-1] in mult:
        try:
            return float(s[:-1]) * mult[s[-1]]
        except ValueError:
            return None
    return None


@dataclass
class Obligation:
    key: str
    framework: str = ""
    citation: str = ""
    requires: str = ""
    within: str = ""
    confidence: str = "low"
    audience: str = ""
    satisfied_by: str = ""
    must_verify: bool = False

    @property
    def seconds(self) -> float | None:
        return parse_within(self.within)

    @property
    def deadline_text(self) -> str:
        return NON_NUMERIC.get(self.within, self.within)


@dataclass
class Compliance:
    frameworks: dict = field(default_factory=dict)
    obligations: dict = field(default_factory=dict)
    breaches: dict = field(default_factory=dict)
    path: str = ""

    # -- labelling --------------------------------------------------------

    def label(self, rule: str, now: float | None = None) -> dict:
        """What a breach of `rule` means in compliance terms, and by when.

        An unmapped rule is labelled `unmapped` and NOT silently treated as
        harmless - somebody has to decide what it means.
        """
        now = now or time.time()
        m = self.breaches.get(rule)
        if not m:
            return {"rule": rule, "labels": ["unmapped"], "reportable": None,
                    "obligations": [], "note": "no compliance mapping for this rule yet; "
                                               "unclassified is not the same as harmless"}
        out = []
        for key in m.get("obligations") or []:
            o = self.obligations.get(key)
            if not o:
                continue
            secs = o.seconds
            out.append({
                "key": key, "framework": o.framework, "citation": o.citation,
                "requires": o.requires, "audience": o.audience,
                "deadline": o.deadline_text,
                "report_by": round(now + secs, 3) if secs else None,
                "report_by_text": (_fmt(now + secs) if secs else o.deadline_text),
                "confidence": o.confidence,
                "must_verify": bool(o.must_verify),
                "satisfied_by": o.satisfied_by,
            })
        return {"rule": rule, "labels": m.get("labels") or [],
                "reportable": bool(m.get("reportable")),
                "note": m.get("note", ""), "obligations": out}

    # -- readiness --------------------------------------------------------

    def readiness(self, evidence: dict) -> dict:
        """Gaps, not a score.

        `evidence` is what the running system can actually show, e.g.
        {"ledger_chained": True, "venue_signed": False, ...}
        """
        checks = [
            ("tamper_evident_record", evidence.get("ledger_chained"),
             "sha256-chained ledger, verified on every read",
             "no tamper-evident record of what the robot did"),
            ("record_signed", evidence.get("ledger_signed"),
             "ledger entries signed at origin",
             "the chain proves internal consistency, NOT origin - anyone who can write "
             "the file can write a valid chain"),
            ("operator_policy_signed", evidence.get("venue_signed"),
             "the venue guardrail file is signed by the operator",
             "nobody has signed the guardrail file, so no human has accepted these limits"),
            ("zones_measured", evidence.get("zones_measured"),
             "zone geometry measured on site",
             "zone coordinates came off a floor plan and have never been measured"),
            ("human_oversight", evidence.get("approval_path"),
             "Approve/Deny/Pause reaches a human, and refusals fail closed",
             "no human-in-the-loop path is configured"),
            ("transparency_to_guest", evidence.get("ai_disclosure"),
             "the guest is told they are talking to an AI",
             "no evidence the guest is told they are interacting with an AI system"),
            ("child_safety_verified", evidence.get("child_detector_tested"),
             "the child-proximity path has been tested end to end",
             "the child rule is enforced but the DETECTOR behind it has never been "
             "tested against a child; the policy exists, the sensing is unproven"),
            ("safety_function_rated", evidence.get("performance_level"),
             "safety functions have a determined Performance Level",
             "no PL/SIL rating: a camera, a detector and a timer on a best-effort "
             "network is not a rated safety function and must not be sold as one"),
            ("risk_assessment", evidence.get("risk_assessment"),
             "a documented risk assessment exists",
             "no documented ISO 12100-style risk assessment covering foreseeable misuse"),
            ("dpia", evidence.get("dpia"),
             "a data protection impact assessment exists",
             "no DPIA, and the venue processes images of guests in a place that "
             "forbids photography"),
            ("incident_clock", evidence.get("clocks_running"),
             "reporting clocks start automatically on a reportable breach",
             "no automatic reporting clock"),
        ]
        met = [{"control": c, "evidence": e} for c, ok, e, _ in checks if ok]
        gaps = [{"control": c, "gap": g} for c, ok, _, g in checks if not ok]
        unverified = sorted({f for f, v in self.frameworks.items()
                             if not v.get("verified_by_counsel", False)})
        scoped = sorted({f for f, v in self.frameworks.items() if v.get("scope_warning")})
        return {
            "met": met, "gaps": gaps,
            "blocking_certification": [g for g in gaps if g["control"] in
                                       ("safety_function_rated", "risk_assessment",
                                        "dpia", "operator_policy_signed",
                                        "child_safety_verified")],
            "frameworks_unverified_by_counsel": unverified,
            "frameworks_with_scope_warnings": scoped,
            "statement": (
                "This is an evidence pack, not a certificate. ISO/IEC 42001 and 27001 "
                "certificates are issued by accredited certification bodies after an "
                "audit; CE marking is a manufacturer's declaration. Nothing here "
                "confers conformity. No citation in compliance.yaml has been checked "
                "by a lawyer."),
        }

    def evidence_pack(self, ledger_events: list, evidence: dict,
                      now: float | None = None) -> dict:
        """Everything an auditor would ask for, in one object."""
        now = now or time.time()
        breaches = [e for e in ledger_events if e.get("kind") == "breach"]
        labelled, open_clocks = [], []
        for e in breaches:
            rule = (e.get("data") or {}).get("rule", "")
            lab = self.label(rule, now=e.get("ts", now))
            row = {"seq": e.get("seq"), "ts": e.get("ts"), "rule": rule,
                   "severity": (e.get("data") or {}).get("severity"), **lab}
            labelled.append(row)
            if lab.get("reportable"):
                for o in lab["obligations"]:
                    if o["report_by"]:
                        open_clocks.append({
                            "seq": e.get("seq"), "rule": rule, "obligation": o["key"],
                            "citation": o["citation"], "audience": o["audience"],
                            "report_by": o["report_by"],
                            "overdue": now > o["report_by"],
                            "remaining_s": round(o["report_by"] - now, 1)})
        return {
            "generated": now,
            "counts": {"events": len(ledger_events), "breaches": len(breaches),
                       "reportable": sum(1 for r in labelled if r.get("reportable"))},
            "breaches": labelled[-100:],
            "clocks": sorted(open_clocks, key=lambda c: c["report_by"]),
            "overdue": [c for c in open_clocks if c["overdue"]],
            "readiness": self.readiness(evidence),
        }


def _fmt(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def load(path: str) -> Compliance:
    import yaml
    raw = yaml.safe_load(Path(path).read_text()) or {}
    c = Compliance(frameworks=raw.get("frameworks") or {},
                   breaches=raw.get("breaches") or {}, path=str(path))
    for k, v in (raw.get("obligations") or {}).items():
        v = v or {}
        c.obligations[k] = Obligation(
            key=k, framework=str(v.get("framework") or ""), citation=v.get("citation", ""),
            requires=v.get("requires", ""), within=str(v.get("within", "")),
            confidence=v.get("confidence", "low"), audience=v.get("audience", ""),
            satisfied_by=v.get("satisfied_by", ""), must_verify=bool(v.get("must_verify")))
    return c
