"""Zero-shot scene class with CLIP ViT-B/32 on the CPU (~100 ms per frame), smoothed over ~1 s.

Optional: `pip install -e '.[scene]'` (open_clip_torch + CPU torch). Without it, classify() returns "unknown" and
the observation carries the degraded flag "scene_unavailable". The labels are what the safety FSM and the judge
trigger on (a new scene class re-asks the Cosmos judge).
"""

from __future__ import annotations

import time
from typing import Any

LABELS = {
    "reception": "a reception desk with a ticket counter in a spa lobby",
    "corridor": "an indoor corridor in a public building",
    "shop_cafe": "a small souvenir shop or cafe counter",
    "bath_entrance": "turnstiles at the entrance of a thermal bath",
    "stairs": "a staircase",
    "wet_pool_deck": "a wet tiled pool deck next to a swimming pool",
    "barefoot_zone": "a relaxation area with loungers where people walk barefoot",
    "staff_area": "a technical room or staff-only door",
    "unknown": "a blurry photo",
}


class SceneClassifier:
    def __init__(self, smooth_s: float = 1.0, model: str = "ViT-B-32", pretrained: str = "laion2b_s34b_b79k"):
        self.smooth_s = smooth_s
        self.model_name, self.pretrained = model, pretrained
        self._m: Any = None
        self.error: str | None = None
        self._probs: dict[str, float] | None = None
        self._t = 0.0

    def available(self) -> bool:
        if self._m is not None:
            return True
        if self.error:
            return False
        try:
            import open_clip
            import torch

            m, _, pre = open_clip.create_model_and_transforms(self.model_name, pretrained=self.pretrained)
            tok = open_clip.get_tokenizer(self.model_name)
            m.eval()
            with torch.no_grad():
                t = m.encode_text(tok([f"a photo of {v}" for v in LABELS.values()]))
                t = t / t.norm(dim=-1, keepdim=True)
            self._m, self._pre, self._txt, self._torch = m, pre, t, torch
            return True
        except Exception as e:  # noqa: BLE001
            self.error = f"{e.__class__.__name__}: {e}"[:200]
            return False

    def probs(self, bgr) -> dict[str, float] | None:
        if not self.available():
            return None
        from PIL import Image

        torch = self._torch
        img = Image.fromarray(bgr[:, :, ::-1])
        with torch.no_grad():
            f = self._m.encode_image(self._pre(img).unsqueeze(0))
            f = f / f.norm(dim=-1, keepdim=True)
            p = (100.0 * f @ self._txt.T).softmax(dim=-1)[0].tolist()
        return dict(zip(LABELS, p, strict=True))

    def classify(self, bgr) -> tuple[str, float]:
        p = self.probs(bgr)
        if p is None:
            return "unknown", 0.0
        return self.update(p)

    def update(self, p: dict[str, float], now: float | None = None) -> tuple[str, float]:
        """Exponential smoothing with a ~smooth_s time constant (testable without CLIP)."""
        now = time.time() if now is None else now
        if self._probs is None:
            self._probs = dict(p)
        else:
            dt = max(0.0, now - self._t)
            a = min(1.0, dt / self.smooth_s) if self.smooth_s > 0 else 1.0
            self._probs = {k: (1 - a) * self._probs.get(k, 0.0) + a * v for k, v in p.items()}
        self._t = now
        k = max(self._probs, key=lambda x: self._probs[x])  # type: ignore[index]
        return k, round(self._probs[k], 3)
