"""Person appearance embedders for re-identification (ROS-free)."""
import cv2
import numpy as np

H_BINS = 16
S_BINS = 8
OSNET_DIM = 512


def _l2_rows(x):
    n = np.linalg.norm(x, axis=1, keepdims=True)
    return (x / np.maximum(n, 1e-12)).astype(np.float32)


class HistogramEmbedder:
    """HSV hue/saturation histogram of the upper and lower half of each crop."""

    name = 'histogram'
    dim = 2 * H_BINS * S_BINS

    def _half(self, hsv):
        h = cv2.calcHist([hsv], [0, 1], None, [H_BINS, S_BINS], [0, 180, 0, 256])
        return h.flatten()

    def embed(self, image_bgr, boxes_xyxy):
        boxes = np.asarray(boxes_xyxy, np.float32).reshape(-1, 4)
        out = np.zeros((len(boxes), self.dim), np.float32)
        ih, iw = image_bgr.shape[:2]
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            x1, x2 = int(max(0, x1)), int(min(iw, x2))
            y1, y2 = int(max(0, y1)), int(min(ih, y2))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            hsv = cv2.cvtColor(image_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
            mid = hsv.shape[0] // 2
            out[i] = np.concatenate([self._half(hsv[:mid]), self._half(hsv[mid:])])
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        # zero rows (degenerate crops) stay zero
        return (out / np.maximum(norms, 1e-12)).astype(np.float32)


class OsnetEmbedder:
    """OSNet x0_25 (MSMT17) via boxmot; imported lazily so boxmot stays optional."""

    name = 'osnet'
    dim = OSNET_DIM

    def __init__(self, device='cpu', threads=None):
        if threads:
            import torch
            # all-core torch next to YOLO + RViz made one embed take ~6 s instead of ~20 ms
            torch.set_num_threads(int(threads))
        from boxmot.reid.core.reid import ReID
        self._m = ReID(device=device)

    def embed(self, image_bgr, boxes_xyxy):
        boxes = np.asarray(boxes_xyxy, np.float32).reshape(-1, 4)
        if len(boxes) == 0:
            return np.zeros((0, self.dim), np.float32)
        feats = np.asarray(self._m(image_bgr, boxes.astype(np.float32)), np.float32)
        return _l2_rows(feats)


def make_embedder(kind, device, log=print, threads=None):
    if kind == 'osnet':
        try:
            return OsnetEmbedder(device=device, threads=threads)
        except Exception as e:  # missing boxmot, weights download, device errors
            log(f'OSNet embedder unavailable ({e!r}); falling back to histogram')
            return HistogramEmbedder()
    return HistogramEmbedder()
