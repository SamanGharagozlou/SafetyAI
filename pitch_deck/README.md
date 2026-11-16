# Sepazu pitch deck

`index.html` is the deck: 10 slides from [docs/sepazu_pitch_master_brief.md](docs/sepazu_pitch_master_brief.md),
plus a DEMO slide (3b) and a ClawMetry dashboard slide (4b) that continues the safety slide, so the counter
shows 12. Built on [template/pitch_template.html](template/pitch_template.html) with the
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

## Demo video

The DEMO slide (5) plays the demo when it opens and stops it when you leave; click the slide to pause or
resume. The arrow keys and a clicker keep working while it plays. It picks the first source that works:

1. `assets/demo.mp4`, if you put one there. Works offline. Pitch deck media is the one place AGENTS.md allows
   recordings of people in git, so it may be committed (keep it under 100 MB).
2. The YouTube upload [youtu.be/R41dpTOY1Mg](https://youtu.be/R41dpTOY1Mg), embedded. Needs internet **and**
   the deck served over http (the online preview link, or `python3 -m http.server 8000 --directory pitch_deck`).
   Opened by double clicking the file, YouTube refuses to embed (error 153), so the slide shows DEMO with
   "Click to watch the demo on YouTube" and a click opens the video in a new tab.
3. Offline with no local file: the slide shows only DEMO.

## Still open

| Slide | What |
|---|---|
| 12 Ask | Optional `assets/team.jpg` background (contact: Vivek Chand, vivek@clawmetry.com) |

Use underscores in names: the brief bans dashes in visible slide text.

## Assets in use

Web-sized copies (cropped to their slot, EXIF removed) live in `assets/web/`; the originals in `assets/` are
untouched. To redo a crop, change the original and regenerate the copy at the same size.

| Copy | Original | Slide | Treatment |
|---|---|---|---|
| `spa_terrace_robot.jpg` | `WhatsApp Image 2026-11-16 at 12.05.03.jpeg` | 1 | copied as is, full bleed (16:9, no crop) |
| `event_square.jpg` | `robot_event_square.jpg` | not used | earlier opening photo, right panel crop |
| `spa_entrance.jpg` | `IMG_8225.JPG` | 2 | |
| `spa_reception.jpg` | `reception.png` | 3 | rotated 90 degrees clockwise, top of frame |
| `robot_with_guests.jpg` | `PXL_20261115_184822865_exported_30769.jpg` | 4 | |
| `frontend_ui.jpg` | `UI_page_2.jpeg` | 4 | browser bar cropped off (also hides the tailnet URL) |
| `robot_alone.jpg` | `PXL_20261115_183304509.jpg` | 6 left | |
| `kids_petting.jpg` | `PXL_20261115_191557534.jpg` | 6 right | |
| `../clawmetry_logo.svg` | [vivekchand/clawmetry](https://github.com/vivekchand/clawmetry) `desktop/assets/clawmetry-logo-stacked-darkbg.svg`, MIT | 6 | |
| `../Clawmetry.jpeg` | used directly | 7 | |
| `kids_interacting.jpg` | `PXL_20261115_191712948.jpg` | 8 | |
| `spa_reception_robot.jpg` | `PXL_20261115_185752619_exported_27792.jpg` | 8 | |
| `jetson_mounted.jpg` | `jetson_mount.jpg` | 9 | crop on the Jetson |
| `nav_map.png` | `map_trajectory.png` | 9 | crop on the trajectory |
| `escort.jpg` | `escort.jpg` | 9 | crop on the robot |
| `voice.jpg` | `voice_assit.jpg` (same file as `..._30769`) | 9 | square crop |
| `gestures.jpg` | `gestures.jpg` (same file as `..._27792`) | 9 | square crop |
| `tourist_info_point.jpg` | `tourist_info_point.jpg` | 10 | top of frame, right panel |

`PXL_20261115_184822865_exported_29735.jpg` is not used: it is almost the same frame as `..._30769`.

## Before presenting

- Confirm consent for the children on slides 6 and 8, or blur their faces (brief section 5).
- Slide 6 says "Restricted control state". ClawMetry's venue policy defines child rules (freeze within 2 m,
  no limb gestures), but its README says the detector has not been tested against a child. Make sure the
  wording matches what the demo shows. If the layer only alerts, change the right panel caption.
- Run the brief's preflight checklist (section 10) and time the pitch aloud with the real video.

## PDF backup

```bash
node pitch_deck/tools/export_pdf.js
```

This writes `pitch_deck/sepazu_pitch.pdf` (not committed, regenerate after assets land).
