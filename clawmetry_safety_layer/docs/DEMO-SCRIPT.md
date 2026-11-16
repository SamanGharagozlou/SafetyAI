# Demo script: five beats (D1 Ultra-W, Leukerbad Therme)

* **Live venue run:** the robot runs on the concourse. A presenter plays the guest, one operator holds the controller and
  the e-stop, and Eaby runs mission control from the UK.
* **Rehearsal (mock):** `scripts/run_mock.sh`, then `scripts/demo_d1w_mock.py`. The recording is
  `docs/demo_d1w_mock.mp4` and the transcript `docs/demo_d1w_mock-transcript.md`.
* **Lines:** the robot's lines below are what the concierge said in the 2026-11-14 mock (Anthropic tool loop,
  `claude-opus-5-5`, effort low). Live, the model phrases them itself. With no brain or API key, the edge's local
  fallback gives the canned lines from `concierge/local.py`.

## Beat 1: a price question, then "take me there"

* **Guest:** "How much is a three-hour ticket for an adult?"
* **Robot:** "A three-hour thermal bath ticket costs 33 francs for an adult (16 and over). You can buy it at the Kasse,
  and I can take you there if you'd like."
* **Guest:** "Please take me to the Therme entrance."
* **Robot:** "Of course, follow me. I'm taking you to the Therme entrance now." It drives via the concourse at guide
  pace (0.3 m/s), because the straight line would pass the stairs. On arrival: "Here is the entrance. The turnstiles
  are just ahead; enjoy the water!"
* **ClawMetry shows:**
  * `concierge.turn` events (source `brain`) and the venue lookup and `guide_to` tool calls;
  * the turns `say`, then `navigate_to concourse`, then `navigate_to bath_entrance`, then `say`, each with
    `turn.observe`, `turn.decide`, `turn.command` and `turn.outcome`;
  * Cosmos verdicts with trigger `guide_start` and `every_2m`.

## Beat 2: people near, so the robot slows down

* **Setup:** guests stand about 1.5 m from the robot.
* **What happens:** `person_near` (L2, `slow`) takes the FSM to CAUTION and the speed cap to 0.15 m/s for the walk to
  the shop. When someone comes closer than 1 m, the FSM goes to HOLD and clears by itself after they step back.
* **ClawMetry shows:** `fsm.transition NORMAL → CAUTION (L2: person within 2 m)` and `person_near {distance_m: 1.64}`.
  Cosmos verdicts count `people_within_2m`. A judged "stop" also only means CAUTION.

## Beat 3: "Take me to the pool": policy block, then the operator's decision

* **Guest:** "Can you take me to the pool?"
* **Robot:** "I can't go to the pools myself, because the pool deck is wet and lies beyond the turnstiles. I've asked
  staff whether someone can escort you, and I can take you to the Therme entrance, which is the closest I can go."
* **Before anything moves:** `guide_to(pool_deck)` is refused, and an approval is filed:
  `mcp__g1-skills__guide_to {poi: pool_deck, ask: staff escort}`.
* **Operator:** **Deny**, in ClawMetry's Approvals once the Guard rule exists, otherwise in the console.
* **Robot:** "Staff can't escort you right now. I can take you to the Therme entrance instead."
* **ClawMetry shows:** `guide {phase: refused, nearest: bath_entrance}`, then `approval {decision: pending_console}`,
  then `approval {decision: deny, by: ...}`. With the Guard rule, the approval sits in the Approvals tab instead.

## Beat 4: staged breach toward the pool deck

* **Setup:** the robot is in front of the turnstiles. Live, the operator nudges it forward with the controller at
  walking pace. In the mock, an outside motion source plays the teleop, followed by a staged 1.1 m slip.
* **L1:** measured motion predicts an exit from the go zone within 2 s. The FSM goes to **HOLD** before the line and
  our zero `cmd_vel` stops the robot.
* **L2:** the floor-transition detector flags the no-go floor ahead (a `hazard` event).
* **L3:** a Cosmos verdict on that frame.
* **Recovery:** once the robot is past the line (in the mock, after the staged slip), the operator presses **Reset**.
  The robot backs straight up 0.9 m at 0.15 m/s until it is 0.3 m inside the go zone. The FSM goes to RECOVER and waits
  for approval; the operator presses **Approve** and the FSM returns to NORMAL.
* **Robot (optional line, via the console's Say):** "Sorry, that was too close to the pools. I'm back where I belong."
* **ClawMetry shows:** `breach_predicted {source: L1, basis: measured_motion}`, `fsm.transition NORMAL → HOLD (L1)`,
  `hazard {source: L2, floor_here: terracotta}`, `judge_verdict`, `recover {phase: plan → done, moved_m: 0.9}`,
  `fsm.transition HOLD → RECOVER → NORMAL (approved)`.

## Beat 5: ClawMetry Stop while moving, then Reset

* **Setup:** a guide to the Kasse is running. Eaby presses **Stop** on ClawMetry's Physical AI page.
* **Robot:** zero `cmd_vel` at once; it holds its stance and never lies down. The controller reports
  `physical_stop_confirmed: true` from fresh state.
* **Reset:** the robot re-arms. It is inside the go zone, so no recovery motion is needed.
* **ClawMetry shows:** `control.stop_requested`, `control.stop_result {state: confirmed}`,
  `fsm.transition → HOLD (operator)`, `control.reset`, `fsm.transition → NORMAL`.

## Closing line (presenter)

"Every move you saw was a goal checked by the robot's own skill server, here in the room. The brain in the UK only
advised, and ClawMetry only watched, approved and stopped."
