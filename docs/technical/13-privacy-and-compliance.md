# 13 · Privacy and compliance posture

[← 12 Dashboards](12-dashboards.md) · [index](../README.md) · next: [14 Testing and evidence](14-testing-and-evidence.md)

Source: `clawmetry/compliance.yaml` + `compliance.py`,
[feature_privacy_anonymization.md](../feature_privacy_anonymization.md),
`clawmetry/venue/leukerbad_therme.yaml`.

> **This is not legal advice and this system issues no certificate.** Every
> citation in `compliance.yaml` carries `confidence` and
> `verified_by_counsel: false`. **Nothing in it has been checked by a lawyer.**
> Do not put a deadline from this file in front of a regulator, an insurer or a
> customer until someone qualified has confirmed it applies.

---

## 1. Why a spa is the hard case

- Guests are in **swimwear or bathrobes**. Images of them are highly sensitive
  even with no name attached.
- **Changing rooms, showers and sauna interiors must never be filmed.**
- A camera moving through a publicly accessible area is **systematic
  monitoring**; the spa will likely need a DPIA (Art. 35).
- Face images processed to **identify** people are biometric special-category
  data (Art. 9). This project only **detects** faces in order to hide them, and
  never identifies anyone.
- **The venue forbids photography everywhere**, and behind the turnstiles that
  is absolute.

---

## 2. Jurisdiction: Swiss first, EU on top

This is the detail most teams get wrong, and getting it wrong is worse than
saying nothing.

| Framework | Applies | Confidence |
|---|---|---|
| **Swiss revFADP (revDSG**, in force 2023-09-01) | **always** — the venue is in Valais. **The primary law here** | high |
| **GDPR** | **in addition**, to the extent the spa offers services to people in the EU — likely for an alpine spa full of EU tourists, but a question for the spa's counsel | high |
| **EU AI Act** (2024/1689) | only if the system is placed on, or its output used in, the EU market. Switzerland is **not** bound by it | medium |
| **EU Machinery Regulation** 2023/1230 | from 20 Jan 2027, for machinery placed on the EU market | medium |
| **ISO/IEC 42001:2023** (AI management system) | voluntary, certifiable — the natural standard to audit this layer against | medium |

> **revFADP Art. 24 says "as soon as possible" and pointedly does *not* set the
> GDPR's 72 hours.** Copying the 72-hour figure across would be the easy, wrong
> answer.

On the AI Act, the file is deliberately uncommitted:

> A spa concierge robot is probably **not** high-risk under Annex III, which
> would mean the heavy Chapter III duties do not bite. The transparency duty in
> **Art. 50** — telling a person they are interacting with an AI — applies
> regardless and is the one to assume. Whether Art. 73 serious-incident
> reporting applies depends entirely on that classification. **This needs a
> lawyer; do not assume either way.**

Art. 50 is satisfied in the product itself: the persona says it is a robot dog
happily and *"never implies you are a person"*.

### A scope warning that is easy to skip and shouldn't be

ISO 10218 and ISO/TS 15066 carry explicit `scope_warning`s in the file: they
are **industrial** robot standards. A guide robot among undressed spa guests is
a **service robot**, where **ISO 13482** is the nearer fit.

> Claiming ISO 10218 invites an auditor to ask which industrial installation
> you mean.

Similarly, on the TÜV SÜD humanoid robotics safety framework:

> **ITS CONTENTS HAVE NOT BEEN READ** — the page returns 403 to automated
> access, the same as the venue's own site does. Nothing in this file is
> derived from it. Before citing that framework anywhere, read it and map to
> its actual criteria. `status: contents_not_read`

---

## 3. Data inventory

| Data | Source | Processed | Leaves the Orin? | Kept? |
|---|---|---|---|---|
| Raw camera frames | D1 camera | Orin RAM only | **no** | no |
| Anonymized frames | privacy filter *(planned)* | Orin | yes — dashboard, console | only if recording is enabled |
| Person detections (boxes, counts) | `human_perception` | Orin | yes, aggregated | no |
| Re-ID embeddings | `d1_follow` | Orin RAM | **no** | **no** — session-scoped |
| Maps (LiDAR clouds, grids) | MetaCam | Orin / laptop | yes | yes; scan when the area is closed to guests |
| **Voice audio** | guest → Vapi | **Vapi cloud, third country** | **yes** | per Vapi settings — **open question** |
| **Final transcripts** | Vapi → the ledger | Orin | no | **yes, in the ledger**, unless `--redact-transcripts` |
| Session (nickname, language) | robot-face | Orin memory | no | deleted on stop or at 8 min |
| Robot logs | all nodes | Orin | via SSH | rotated; no images, no transcripts |

### The two rows that matter

**Voice audio leaves the country.** That is the honest position, and the
persona is instructed to say so rather than reassure:

> *Do not claim nothing leaves the robot — that is not true on this setup.*

**Transcripts are stored by default, and the system knows it is a problem.**
The voice sink keeps final transcripts only — partials, speech-start chatter
and per-token output are counted and dropped, because *a ledger holding every
interim word is a transcript of spa guests, which is the one thing this must
not keep*. `--redact-transcripts` reduces that to a length and a sha256 prefix.

**Audio recordings are never kept.** `recordingUrl` and `stereoRecordingUrl`
are discarded, and `audio_dropped: true` is written into the record so the
omission is **visible rather than assumed.**

---

## 4. The anonymization spec

Status: **spec, not built.** `privacy_filter` is designed and not implemented,
and FR-P4 marks it **must, before any deployment with guests.**

| ID | Requirement |
|---|---|
| PRIV-1 | A `privacy_filter` node on the Orin publishes `/camera/image_anonymized` with every detected face **irreversibly** blurred or filled — solid fill or strong pixelation; *reversible-looking light blur is not enough* |
| PRIV-2 | Mode `face` (default) or `person` (whole-body mask) for sensitive zones |
| PRIV-3 | **Fail closed**: if the detector is not running, too slow, or a frame cannot be processed in time, publish nothing or a fully masked frame. **Never pass a raw frame through** |
| PRIV-4 | Raw `/camera/image_raw` never leaves the Orin, is never recorded, never logged — enforced by localhost-only DDS for raw topics, viewer configs that reference only anonymized topics, **and a test that fails if a bag profile includes `image_raw`** |
| PRIV-5 | **Camera-off zones**: inside a marked zone the output is fully masked and recording is paused |
| PRIV-6 | No face recognition, no re-identification, **no face embeddings stored**. Tracking ids short-lived and in memory only |
| PRIV-7 | Face recall **≥ 95 %** on a spa-like test set (frontal + profile, 0.5–5 m, wet hair, steam, glasses) at the publish rate. **A miss is a privacy incident.** Masks expanded ~20 % and held for a few frames to cover detector flicker |
| PRIV-8 | Recording **off by default**; if enabled, anonymized topics only, an operator-visible indicator, automatic deletion after an agreed retention (e.g. 7 days) |
| PRIV-9 | Transparency: signage, a privacy page, a visible "camera active" indicator |
| PRIV-10 | Document what Vapi stores, configure the shortest retention, inform guests before the call, offer the text path |
| PRIV-11 | ≥ 10 Hz at 960×540 on the Orin GPU while detection and Nav2 run |

PRIV-4 is the one worth copying: **a privacy rule that is only a convention
becomes a test.**

### Camera-off is enforced today, in the venue file

Even without the filter, three zones carry `camera_off: true` —
`pool_deck`, `sauna_world`, `relax_level`:

> Not "blur it later" — **no frame is captured there at all, not even for the
> safety judge.**

That is a deliberate trade: the world-model judge is blinded in exactly the
zones the robot is forbidden to enter anyway.

### And there is no camera in the guest flow at all

`tools/robot-face` has none. If face capture is ever added, the stated
defensible version is *a transient re-ID embedding held in RAM for the session,
never stored, never uploaded, and said plainly on screen.*

---

## 5. Consent and transparency, as implemented

| Control | Where |
|---|---|
| Consent checkbox **checked on the server**, not only in the browser | `robot-face` `/api/session` |
| *"My voice is used only to hold this conversation and is not kept afterwards"* | the `/join` page |
| *"This phone does not listen and does not play sound. No camera, no photos."* | the `/join` page |
| Session expires at 8 minutes and is wiped | `SESSION_MAX_MS` |
| Anyone may press Stop, no credentials | `/api/session` `action: stop` |
| The robot says it is a robot | the persona |
| An honest answer about recording, humour switched off | the persona |
| Staff can switch it off on request | the persona, and the console |

> *A consent control that can be skipped by replaying a request is not a
> consent control, and this one covers recording a stranger's voice.*

---

## 6. Breach labelling and reporting clocks

Each breach is labelled with the obligation it may trigger, and a **reportable**
one starts a clock written **into the ledger**:

```
child_proximity   spa internal policy                 -> duty manager      15 min left
                  ISO/IEC 42001 incident handling     -> auditor         1440 min left
chain_broken      Art. 33 GDPR + Art. 24 revFADP      -> DPA / FDPIC        72h / asap
```

> A deadline that exists only in a UI is one nobody can prove was known.

### Not every breach is reportable — six of twelve are deliberately not

| rule | reportable | why |
|---|---|---|
| `child_proximity` | **yes** | *"the incident this venue would be judged on. Escalate to a human in minutes, not hours, whatever any regulator says."* |
| `denied_command_executed` | **yes** | *"Human oversight was exercised and then ignored. That is the control failing."* |
| `unattributed_motion` | **yes** | *"The machine moved and nothing authorised it."* |
| `gate_bypass` | yes | venue internal |
| `policy_breach` | yes | the operator's own rule was exceeded in confirmed state |
| `predicted_breach` | **no** | *"The guardrail WORKED — something unsafe was refused before it happened. A prevented breach is evidence the control functions, not an incident."* |
| `prediction_deviation` | no | engineering signal; reportable only if it coincides with harm |
| `narration_divergence` | no | labelled against AI Act Art. 50 transparency |
| `silence` | **no** | *a telemetry gap, not a personal data breach — reporting it as one would destroy both the signal and the operator's credibility* |

> **Over-reporting destroys the signal and the operator's credibility.** That
> sentence is in the file, above the rules, and it is the reason the default is
> `reportable: false` and each exception is raised deliberately.

---

## 7. This is not a certificate, and cannot become one

> No software issues conformity. ISO/IEC 42001 and 27001 certificates come from
> accredited certification bodies after an audit; CE marking is a
> manufacturer's declaration.

What `compliance.py` produces is **the evidence pack an assessor asks for**,
and — more usefully — **the gap list that would fail the audit today.**

| Target | We provide | We cannot | Blocking gaps |
|---|---|---|---|
| ISO/IEC 42001 certification | operational control evidence, incident records, reporting clocks, the gap list | issue, self-declare or shortcut the certificate | `risk_assessment`, `operator_policy_signed`, `record_signed` |
| CE marking (Machinery) | operational evidence and incident records | **substitute for a Performance Level determination. Nothing in this stack is a rated safety function** | `safety_function_rated`, `risk_assessment` |
| Third-party robot safety assessment (TÜV, DEKRA, Bureau Veritas) | a reproducible evidence pack: every intention, decision, confirmed outcome and intervention, tamper-evident and replayable | **assess ourselves** | `safety_function_rated`, `child_safety_verified`, `risk_assessment` |

### The five blocking gaps, verbatim from the dashboard

| gap | why |
|---|---|
| `safety_function_rated` | no PL/SIL rating: **a camera, a detector and a timer on a best-effort network is not a rated safety function and must not be sold as one** |
| `child_safety_verified` | the child rule is enforced but the **detector behind it has never been tested against a child**; the policy exists, the sensing is unproven |
| `operator_policy_signed` | nobody has signed the guardrail file, so no human has accepted these limits |
| `risk_assessment` | no documented ISO 12100-style assessment covering foreseeable misuse |
| `dpia` | none, in a venue that forbids photography |

**`readiness()` returns gaps and never a score**, because a percentage invites
rounding up and a gap list does not.

---

## 8. Open questions for the spa

From [product_requirements.md](../product_requirements.md) §7 and
[feature_privacy_anonymization.md](../feature_privacy_anonymization.md) §6 —
these are for the venue, not for us:

1. Who is the **controller** (the spa) and who are the **processors** (us,
   Vapi, hosting)? Does the spa have a DPO and an existing camera policy?
2. Does the spa require a **DPIA**? (Likely yes.)
3. **Vapi processes guest audio outside the EU.** Acceptable for the demo, or
   is an EU-hosted alternative required?
4. Is any recording needed for the hackathon at all? (**Prefer: no.**)
5. Should `person` mode be the default rather than `face` mode in this context?
6. Which areas may the robot use, and what are the ingress / temperature
   limits of the D1 and the Orin for humid areas?
7. **Who signs `venue/leukerbad_therme.yaml`?** Until someone does, the robot
   runs at 0.15 m/s everywhere and refuses to guide.

---

## 9. What an assessor can actually be handed today

A short, honest list:

- A **tamper-evident, replayable record** of every intention, prediction,
  decision, confirmed outcome and human intervention, with the first broken
  line named if anyone edits it.
- A **signed-off-able operator policy** in plain language, with a change
  history that classifies every edit as tightening or loosening, carries the
  editor's name and reason, and sits in the same record.
- A **gap list**, maintained by the system itself, that says what would fail.
- **Reproducible evidence**: 231 tests that run with no robot, no camera and no
  network, plus a scenario driver that exercises all ten detectors through the
  real doors ([14](14-testing-and-evidence.md)).
- Measured hardware behaviour: 0.1373 m/s, 4.11 s overhead, a validation walk
  at 0.13σ.

And what cannot be handed over: a conformity claim, a Performance Level, a
DPIA, a risk assessment, or any evidence whatsoever that the child detector
works on a child.

---

[← 12 Dashboards](12-dashboards.md) · [index](../README.md) · next: [14 Testing and evidence](14-testing-and-evidence.md)
