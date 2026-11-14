# ClawMetry 🛡️🤖

### Human-Governed AI Safety and Predictive Monitoring for Autonomous Robots

**Developed for the Leukerbad Robotics Hackathon 2026 | Switzerland 🇨🇭**

ClawMetry is an AI-powered robotic governance and monitoring platform designed to make autonomous robotic systems safer, more transparent, and accountable.

As robots become increasingly autonomous, they need more than intelligent decision-making. They need mechanisms to evaluate proposed actions, enforce operational restrictions, involve human supervisors, and provide verifiable records of what actually happened.

**ClawMetry aims to bridge the gap between robotic autonomy and human oversight.**

---

## 🎯 The Problem

Autonomous robots are increasingly deployed in public spaces, logistics, hospitality, healthcare, and industrial environments.

However, several challenges remain:

- How can we prevent robots from executing unauthorized or potentially unsafe actions?
- How can we predict whether a proposed movement may violate operational constraints?
- How can human operators intervene without constantly controlling the robot?
- How can we verify and reconstruct robotic decisions after an incident?
- How can one governance system work across different robotic platforms?

ClawMetry explores these challenges through policy-based control, predictive simulation, human-in-the-loop authorization, and auditable execution monitoring.

## 💡 Our Solution

ClawMetry introduces an independent supervision layer between AI-generated instructions and robotic execution.

The intended workflow is:

1. **Observe:** Collect the robot's current state, telemetry, and environmental context.
2. **Predict:** Evaluate potential future states and risks associated with proposed actions.
3. **Validate:** Check requested actions against operational policies and restrictions.
4. **Authorize:** Require human approval for eligible actions when necessary.
5. **Execute:** Forward permitted commands through an authorized robot interface.
6. **Monitor:** Track reported execution status and detect deviations.
7. **Audit:** Record decisions, interventions, and available execution evidence.

Hard safety restrictions cannot be overridden through ordinary operator approval.

## 🚀 Planned Features

### 1. Autonomous Action Governance

A deterministic policy engine evaluates robot actions before they are dispatched.

Planned capabilities include:

- Action authorization and restriction policies
- Restricted-zone enforcement
- Operator approval workflows
- Time-limited authorizations
- Fail-closed behavior for invalid or unauthorized requests
- Separation between AI recommendations and execution permissions

### 2. Predictive Robot Monitoring

ClawMetry investigates forward-dynamics prediction to evaluate possible consequences of robotic actions.

Technologies under consideration include:

- Lightweight Python trajectory prediction
- MuJoCo physics simulation
- NVIDIA Isaac Sim and Omniverse
- NVIDIA Cosmos world models
- Gemini Robotics for perception and task reasoning

Prediction is treated as decision-support information, not as an independently sufficient safety guarantee.

### 3. Human-in-the-Loop Control Panel

A responsive operator dashboard is planned for desktop and mobile devices.

**Desktop interface:**
- Robot status monitoring
- Simulation and operational visualization
- Policy and intervention management
- Event history and evidence inspection

**Mobile control panel:**
- Real-time intervention alerts
- Approve and Deny actions
- Pause requests
- Incident notifications
- Quick access to execution history

The mobile interface is intended to function as a Progressive Web App (PWA), allowing operators to access the system directly from their phones.

### 4. Auditable Execution Evidence

ClawMetry aims to maintain verifiable records of robotic actions and supervisory decisions.

Planned features include:

- Timestamped execution events
- Policy decision history
- Human authorization records
- Robot telemetry references
- Hash-chained, tamper-evident event logs
- Incident replay and verification

### 5. Robot-Agnostic Integration

ClawMetry is designed around modular robot adapters, allowing the governance layer to be reused across different robotic systems.

Our initial hardware target is the **UniX AI Panther**, a wheeled dual-arm humanoid robot.

The project will initially use simulated robot behavior, with physical hardware integration dependent on verified SDK access, available telemetry, and authorized control interfaces.

---

## 🏗️ Proposed System Architecture

**AI Task Interface → Command Gateway → Policy Engine → Human Authorization → Robot Adapter → Execution Monitoring → Evidence Ledger**

The operator dashboard communicates with the same backend services used by the policy and monitoring components.

The design separates decision-making, policy enforcement, physical control, and evidence collection.

## 🛠️ Technology Stack

The following technologies are planned or being evaluated.

| Component | Technology |
|---|---|
| Backend | Python, FastAPI |
| Frontend | React, Vite |
| Mobile interface | Responsive Web App / PWA |
| Robot communication | ROS 2, vendor SDK |
| Primary robot | UniX AI Panther |
| Simulation | NVIDIA Isaac Sim, MuJoCo |
| World models | NVIDIA Cosmos, Gemini Robotics |
| Data storage | SQLite |
| Testing | pytest |
| Development | Git, GitHub, VS Code |

The final stack will be determined by implementation results and hardware availability.

## 🎬 Hackathon Demonstration

Our target demonstration focuses on a Panther robot operating in a supervised environment containing permitted and restricted zones.

**Scenario 1 — Authorized Operation**

An AI agent proposes a permitted robotic task. ClawMetry evaluates the request, obtains operator approval if necessary, and allows execution through an authorized interface.

**Scenario 2 — Restricted Action**

An AI agent requests movement into a restricted area. ClawMetry identifies the policy violation and blocks the command before dispatch. The operator receives a notification explaining the decision.

**Scenario 3 — Human Intervention**

An operator requests a pause or denies a pending task. ClawMetry records the intervention and displays the available robot-controller and telemetry evidence.

**Scenario 4 — Audit and Verification**

The operator reviews the action history, policy decisions, and supporting records to understand and verify the sequence of events.

Simulation and physical robot demonstrations will be clearly distinguished.

## 🔐 Safety Principles

ClawMetry follows several core design principles:

- Safety-critical restrictions must not depend solely on LLM-generated decisions.
- Unauthorized commands must be rejected before dispatch.
- Human approvals must be authenticated, scoped, and time-limited.
- Missing or stale critical information must not result in automatic authorization.
- Predicted trajectories must not be treated as guaranteed physical outcomes.
- Physical movement requires approved control interfaces and supervision.
- Application-level Pause is not a replacement for a certified hardware emergency stop.
- Audit records must distinguish requested, dispatched, acknowledged, and observed actions.

**ClawMetry is an experimental hackathon prototype and is not a certified robotic safety system.**

## 🗺️ Development Roadmap

- [ ] Core backend and action gateway
- [ ] Deterministic policy engine
- [ ] Simulated Panther environment
- [ ] Desktop monitoring dashboard
- [ ] Mobile-friendly operator control panel
- [ ] Human authorization workflow
- [ ] Tamper-evident evidence ledger
- [ ] Predictive trajectory evaluation
- [ ] Automated policy and integration tests
- [ ] Read-only Panther telemetry integration
- [ ] Advanced simulation and world-model experiments
- [ ] Vendor-approved physical robot demonstration

## 📁 Repository

This repository contains the collaborative development work for the ClawMetry hackathon project.

**Repository:** [SamanGharagozlou/SafetyAI](https://github.com/SamanGharagozlou/SafetyAI)

Implementation instructions, environment configuration, and component-level documentation will be added as development progresses.

## 🌍 Potential Applications

ClawMetry explores robotic governance for:

- Smart cities and public infrastructure
- Hospitality and autonomous service robotics
- Warehousing and logistics
- Industrial automation
- Healthcare and assisted living
- Human-robot collaboration

## 👥 Team

Built collaboratively by the **SafetyAI team** at the Leukerbad Robotics Hackathon 2026.

## 📌 Project Status

**Hackathon prototype — Active Development**

Features and integrations described in this README represent the intended project scope and may not yet be implemented or validated.

---

**ClawMetry — Bringing human oversight, predictive awareness, and accountability to autonomous robotics.**
