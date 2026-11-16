# Sepazu pitch deck

`index.html` is the deck: 10 slides from [docs/sepazu_pitch_master_brief.md](docs/sepazu_pitch_master_brief.md),
plus a full-screen demo video slide (slide 3b), so the counter shows 11. Built on
[template/pitch_template.html](template/pitch_template.html) with the
[frontend-slides](https://github.com/zarazhangrui/frontend-slides) approach: one HTML file, a fixed 1920x1080
stage scaled to the window, Montserrat bundled in `assets/fonts/` so it works offline.

Open `index.html` in a browser.

| Key | Action |
|---|---|
| arrows, space, PageUp/PageDown, swipe | next / previous slide |
| Home / End | first / last slide |
| N | speaker notes: script and target time for the current slide |
| E (or hover the top-left corner) | edit text in place, then **Save HTML** and copy the file over `index.html` |
| `index.html?slide=5` | open at a given slide |

The demo video starts when its slide opens and pauses when you leave it. Click it to pause or resume.

## Missing assets (placeholders)

Every image or video slot shows a dashed orange label until its file exists. Drop the file at the path below
and reload. No HTML edit is needed. Use underscores in names: the brief bans dashes in visible slide text.

| Slide | File | What |
|---|---|---|
| 0 Opening | `assets/event_square.jpg` | Real event photo, robots and people in the town square |
| 2 Problem | `assets/spa_reception.jpg` | Spa reception or service area, ideally without the robot |
| 3 Journey | `assets/qr_code.png` | Guest QR code, only if live and safe to show publicly |
| 3b Demo | `assets/demo.mp4` | 45 s, 16:9, captions burned in |
| 4 Safety | `assets/clawmetry_dashboard.png` | Dashboard showing the alert (keeps the CONTROLLED TEST tag) |
| 6 Built | `assets/jetson_mounted.jpg`, `assets/nav_still.jpg`, `assets/escort_still.jpg`, `assets/voice_still.jpg`, `assets/gesture_still.jpg` | Jetson close up and four video stills |
| 7 Roadmap | `assets/tourist_info_point.jpg` | Real tourist information point or visitor centre |
| 9 Ask | `assets/team.jpg` | Team with the robot |

Text placeholders (orange, dashed underline) still to fill: staff quote and attribution, interaction count
(slide 5), pilot / monthly / prepaid prices (slide 8), team email and website (slide 9).

## Photos in use

Web-sized copies (max 1600 to 2200 px) live in `assets/web/`; the originals in `assets/` are untouched.

| Copy | Original | Slide |
|---|---|---|
| `spa_entrance.jpg` | `IMG_8225.JPG` | 1 |
| `robot_with_guests.jpg` | `PXL_20261115_184822865_exported_30769.jpg` | 3 |
| `robot_alone.jpg` | `PXL_20261115_183304509.jpg` | 4 left |
| `kids_petting.jpg` | `PXL_20261115_191557534.jpg` | 4 right |
| `kids_interacting.jpg` | `PXL_20261115_191712948.jpg` | 5 |
| `spa_reception_robot.jpg` | `PXL_20261115_185752619_exported_27792.jpg` | 5 |

`PXL_20261115_184822865_exported_29735.jpg` is not used: it is almost the same frame as `..._30769`.

## Before presenting

- Confirm consent for the children on slides 4 and 5, or blur their faces (brief section 5).
- Slide 4 says "Restricted control state". ClawMetry's venue policy defines child rules (freeze within 2 m,
  no limb gestures), but its README says the detector has not been tested against a child. Make sure the
  wording matches what the demo shows. If the layer only alerts, change the right panel caption.
- Run the brief's preflight checklist (section 10) and time the pitch aloud with the real video.

## PDF backup

```bash
node pitch_deck/tools/export_pdf.js
```

This writes `pitch_deck/sepazu_pitch.pdf` (not committed, regenerate after assets land).
