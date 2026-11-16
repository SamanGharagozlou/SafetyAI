# SafetyAI — D1 spa companion robot

A companion and escort robot for a thermal spa on the AgiBot D1 Ultra W with a Jetson Orin Nano. Guests scan a QR code,
talk to the robot from their phone, get answers about the facility, and are walked to where they want to go.

- Product requirements: [docs/product_requirements.md](docs/product_requirements.md)
- System architecture: [docs/system_architecture.md](docs/system_architecture.md)
- Features: [ROS 2 camera](docs/feature_ros2_camera.md), [SLAM/localization](docs/feature_slam_localization.md),
  [privacy/GDPR anonymization](docs/feature_privacy_anonymization.md)
- Contributor and agent guide (repo map, rules, all docs): [AGENTS.md](AGENTS.md)

## Pitch deck (Sepazu)

The hackathon pitch is a single HTML file, [pitch_deck/index.html](pitch_deck/index.html): 12 slides, fixed 16:9,
fonts and images bundled, works offline. GitHub shows only its source, so open it one of these ways:

- **View online, nothing to install:**
  [raw.githack.com/SamanGharagozlou/SafetyAI/main/pitch_deck/index.html](https://raw.githack.com/SamanGharagozlou/SafetyAI/main/pitch_deck/index.html)
  (until the branch is merged: [feat/pitch_deck preview](https://raw.githack.com/SamanGharagozlou/SafetyAI/feat/pitch_deck/pitch_deck/index.html))
- **Open locally:** clone the repo and serve it (needed for the embedded demo video):

  ```bash
  python3 -m http.server 8000 --directory pitch_deck   # then open http://localhost:8000
  ```

  Double clicking `pitch_deck/index.html` also works, but the demo slide then links out to
  [the demo on YouTube](https://youtu.be/R41dpTOY1Mg) instead of playing it inline.
  For an offline talk, drop the video at `pitch_deck/assets/demo.mp4` (git ignored) and it plays from disk.

- **PDF backup:** `node pitch_deck/tools/export_pdf.js` writes `pitch_deck/sepazu_pitch.pdf` (needs Playwright).

To present: open it in Chrome or Edge, press F11 for full screen, and click once on the slide.

| Key | Action |
|---|---|
| → / Space / PageDown, ← / PageUp | next / previous slide (a clicker works too) |
| Home / End | first / last slide |
| N | speaker notes with script and timing; they draw over the slide, so hide them before projecting |
| `index.html?slide=5` | open at a given slide |

Slide sources, assets and the editing workflow: [pitch_deck/README.md](pitch_deck/README.md).
`presentation/` holds a separate ClawMetry deck and is not the pitch.

## Perception (human detection)

ROS 2 Humble person detection (YOLO26) on the D1 camera or a webcam. Inference runs on the PC, never on the robot.

```bash
make build                                         # once: Docker image
make up rviz=true                                  # laptop webcam + rviz2
make up source=d1-wifi robot_ip=<robot-ip> rviz=true
make down
```

Details: [docs/perception.md](docs/perception.md). Robot controls: [docs/controls.md](docs/controls.md).
