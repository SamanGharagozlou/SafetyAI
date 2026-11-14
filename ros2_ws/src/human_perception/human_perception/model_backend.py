"""
Ultralytics YOLO model loading and inference for the configured backend.

Backends are chosen by configuration only:

* ``pytorch``  - ``*.pt`` weights, CPU / CUDA / MPS via PyTorch (default)
* ``onnx``     - ``*.onnx`` via ONNX Runtime (CPU, or CUDA with onnxruntime-gpu)
* ``openvino`` - ``*_openvino_model/`` directory, Intel CPU/iGPU/NPU
* ``tensorrt`` - ``*.engine`` on NVIDIA GPUs

Exported formats must be produced ahead of time (``yolo export ...``).  This
module never exports engines on its own: a TensorRT engine is tied to the GPU,
driver and TensorRT version it was built on.
"""

import os
from typing import Any, List, Optional

import numpy as np

from .config import DetectorConfig
from .detection import resolve_person_class_ids


class ModelLoadError(RuntimeError):
    """Raised when the model cannot be loaded or is unusable."""


class UltralyticsDetector:
    """Thin adapter: ``predict(bgr_frame) -> ultralytics Results``."""

    def __init__(self, config: DetectorConfig, logger: Optional[Any] = None) -> None:
        self.config = config
        self._log = logger
        self.model_ref = config.resolved_model_reference()
        self.person_ids: List[int] = []
        self.used_class_fallback = False
        self._model = self._load()

    def _info(self, msg: str) -> None:
        if self._log is not None:
            self._log.info(msg)

    def _load(self) -> Any:
        cfg = self.config
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ModelLoadError(
                'ultralytics is not importable from this Python environment. Activate the '
                'workspace venv (see README) or `pip install -r requirements.txt`.') from exc

        if cfg.backend == 'tensorrt' or cfg.device.startswith('cuda') or cfg.device[0].isdigit():
            self._require_cuda()

        ref = self.model_ref
        is_bare_download = not cfg.model_path and cfg.backend == 'pytorch'
        if not os.path.exists(ref):
            if not is_bare_download:
                raise ModelLoadError(
                    f'model file not found: {ref}. Exported models ({cfg.backend}) must be '
                    'created beforehand; see README "Optional deployment backends".')
            os.makedirs(os.path.dirname(ref) or '.', exist_ok=True)
            self._info(f'model {os.path.basename(ref)} not cached; Ultralytics will download '
                       f'it to {os.path.dirname(ref)}')
        try:
            model = YOLO(ref, task='detect')
        except Exception as exc:  # noqa: B902
            raise ModelLoadError(
                f'failed to load model {ref!r} ({type(exc).__name__}: {exc}). If this is a '
                'first run, check network access for the weight download or place the '
                'weights locally and set model_path.') from exc

        names = getattr(model, 'names', None)
        try:
            self.person_ids, self.used_class_fallback = resolve_person_class_ids(names)
        except ValueError as exc:
            raise ModelLoadError(str(exc)) from exc

        # Warm-up so the first real frame does not pay lazy-init/JIT costs.
        dummy = np.zeros((cfg.image_size, cfg.image_size, 3), dtype=np.uint8)
        for _ in range(cfg.warmup_runs):
            try:
                self._predict(model, dummy)
            except Exception as exc:  # noqa: B902
                raise ModelLoadError(
                    f'model warm-up failed on device={cfg.device} backend={cfg.backend}: '
                    f'{type(exc).__name__}: {exc}') from exc
        return model

    def _require_cuda(self) -> None:
        try:
            import torch
            ok = torch.cuda.is_available()
        except Exception:  # noqa: B902
            ok = False
        if not ok:
            raise ModelLoadError(
                f'device={self.config.device} / backend={self.config.backend} needs CUDA, '
                'but torch.cuda.is_available() is False. Use config/detector_cpu.yaml or '
                'install a CUDA-enabled PyTorch build.')

    def _predict(self, model: Any, frame: np.ndarray) -> Any:
        cfg = self.config
        kwargs = dict(conf=cfg.confidence_threshold, iou=cfg.iou_threshold,
                      imgsz=cfg.image_size, device=cfg.device, max_det=cfg.max_detections,
                      classes=self.person_ids or None, verbose=False)
        if cfg.half:  # only pass when requested; recent Ultralytics warns on half=False
            kwargs['half'] = True
        results = model.predict(frame, **kwargs)
        return results[0] if results else None

    def predict(self, frame: np.ndarray) -> Any:
        return self._predict(self._model, frame)

    @property
    def class_names(self) -> Any:
        return getattr(self._model, 'names', None)
