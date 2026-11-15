import cv2
import numpy as np

BANNER_H = 32
FONT = cv2.FONT_HERSHEY_SIMPLEX
# kind -> (BGR colour, box thickness); drawn in this order so the target ends up on top
STYLE = {
    'unknown': ((160, 160, 160), 1),
    'candidate': ((0, 220, 255), 3),
    'target': ((0, 200, 0), 4),
}
ARROW_GAIN = 100.0  # px per (m/s) or (rad/s)


def _label(kind, track, out):
    if kind == 'target':
        text = f'TARGET #{track.id}'
        if out.similarity is not None:
            text += f' sim {out.similarity:.2f}'
        return text
    if kind == 'candidate':
        return f'LOCKING #{track.id}'
    return f'unknown #{track.id}'


def _draw_label(img, text, x, y, colour):
    h, w = img.shape[:2]
    (tw, th), base = cv2.getTextSize(text, FONT, 0.5, 1)
    x = min(max(x, 0), max(w - tw, 0))
    y = min(max(y, BANNER_H + th + base), h - base - 1)
    cv2.rectangle(img, (x, y - th - base), (x + tw, y + base), (30, 30, 30), -1)
    cv2.putText(img, text, (x, y), FONT, 0.5, colour, 1, cv2.LINE_AA)


def draw(frame_bgr, tracks, out, vx, wz) -> np.ndarray:
    """Annotated copy of the frame: target green, candidate yellow, others grey."""
    img = frame_bgr.copy()
    h, w = img.shape[:2]

    labelled = [(out.labels.get(t.id, 'unknown'), t) for t in tracks if t.visible]
    for kind in ('unknown', 'candidate', 'target'):
        colour, thick = STYLE[kind]
        for k, t in labelled:
            if k != kind:
                continue
            x1, y1, x2, y2 = (int(round(float(v))) for v in t.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), colour, thick)
            _draw_label(img, _label(kind, t, out), x1, y1 - 4, colour)

    cv2.rectangle(img, (0, 0), (w - 1, BANNER_H), (30, 30, 30), -1)
    text = str(out.state.value)
    if out.target is not None:
        text += f'  target #{out.target.id}'
    text += f'  vx {vx:+.2f} wz {wz:+.2f} (dry-run)'
    cv2.putText(img, text, (8, 22), FONT, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

    if vx or wz:
        # positive wz turns left, so the arrow points left on screen
        x0, y0 = w // 2, BANNER_H
        x1 = int(round(min(max(x0 - wz * ARROW_GAIN, 0), w - 1)))
        y1 = int(round(min(max(y0 + vx * ARROW_GAIN, y0 + 1), h - 1)))
        cv2.arrowedLine(img, (x0, y0), (x1, y1), (0, 200, 0), 3, tipLength=0.3)
    return img
