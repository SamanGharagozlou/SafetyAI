"""Local speech for the robot's say(): piper (if installed with a voice model) or espeak-ng, non-blocking. The team's
voice stack gets the same text on the /say ROS topic (bridge.say); this is the fallback / in-room speaker.
Neither installed -> silent (logged once). Config `voice: {engine: auto|piper|espeak-ng|none, piper_model: path}`."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading


class Voice:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        want = os.environ.get("VOICE_ENGINE") or cfg.get("engine", "auto")
        self.model = os.environ.get("PIPER_MODEL") or cfg.get("piper_model")
        self.engine = None
        if want in ("auto", "piper") and shutil.which("piper") and self.model:
            self.engine = "piper"
        elif want in ("auto", "espeak-ng") and shutil.which("espeak-ng"):
            self.engine = "espeak-ng"
        self.spoken: list[str] = []
        self._warned = False

    def speak(self, text: str, lang: str = "en") -> bool:
        self.spoken.append(text)
        del self.spoken[:-50]
        if self.engine is None:
            if not self._warned:
                print("[voice] no local TTS (piper with a model, or espeak-ng): /say topic only")
                self._warned = True
            return False
        threading.Thread(target=self._run, args=(text, lang), daemon=True, name="voice").start()
        return True

    def _run(self, text: str, lang: str):
        try:
            if self.engine == "espeak-ng":
                subprocess.run(
                    ["espeak-ng", "-v", {"de": "de", "fr": "fr", "it": "it"}.get(lang, "en"), text],
                    timeout=30,
                    check=False,
                )
            elif self.engine == "piper":
                p = subprocess.run(
                    ["piper", "--model", str(self.model), "--output-raw"],
                    input=text.encode(),
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if shutil.which("aplay"):
                    subprocess.run(
                        ["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"],
                        input=p.stdout,
                        timeout=60,
                        check=False,
                    )
        except Exception as e:  # noqa: BLE001
            print(f"[voice] {self.engine} failed: {e}")
