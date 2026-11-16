"""Spans + metrics for every skill call, exported to ClawMetry's built-in OTLP
receiver (/v1/traces, /v1/metrics on :8900). Degrades to no-op if the
OpenTelemetry packages are missing or the endpoint is unreachable.

If baseline.json exists (scripts/collect_baseline.py), each span also gets
skill.anomaly_z = |duration - mean| / std for that skill.

Prediction deviation (skills/skills.py: predicted vs actual end pose and duration of the turn) feeds the same score:
skill.deviation_z = max(position_error / pos_sigma, |duration_error| / dur_sigma), with sigmas from baseline.json
(`deviation_pos_std`, `deviation_dur_std` per skill) or defaults (0.10 m; 15 % of the predicted duration, >= 0.5 s).
skill.anomaly_z is then the larger of the duration z and the deviation z, so a turn that went somewhere other than
predicted is flagged even when its duration looked normal.

Metrics: OTEL_METRICS=0 skips the metrics exporter entirely. If ClawMetry answers /v1/metrics with 501 (receiver
without a metrics endpoint), the first 501 shuts the metric reader down and logs one line; traces are unaffected.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENDPOINT = os.environ.get("CLAWMETRY_OTLP", "http://localhost:8900").rstrip("/")
SERVICE = "leukerbad-g1"

_tracer = None
_meter = None
_gauges: dict[str, float] = {}
_baseline: dict = {}
_metrics_disabled = threading.Event()

METRICS_501_MSG = "[otel] ClawMetry metrics endpoint not implemented; metrics disabled, traces unaffected"


class _Metrics501Filter(logging.Filter):
    """On the metrics exporter's logger: the first "Failed to export metrics batch code: 501" shuts the reader down
    (in a side thread: the record comes from the reader's own export thread) and prints one line; that record and
    any later metrics export failures are dropped."""

    def __init__(self, reader):
        super().__init__()
        self._reader = reader

    def filter(self, record: logging.LogRecord) -> bool:
        if _metrics_disabled.is_set():
            return False
        args = record.args if isinstance(record.args, tuple) else ()
        if "Failed to export" in str(record.msg) and 501 in args:
            _metrics_disabled.set()
            print(METRICS_501_MSG)
            threading.Thread(target=self._shutdown, name="otel-metrics-off", daemon=True).start()
            return False
        return True

    def _shutdown(self):
        try:
            self._reader.shutdown()
        except Exception:  # noqa: BLE001
            pass


def metrics_enabled() -> bool:
    return os.environ.get("OTEL_METRICS", "1").strip().lower() not in ("0", "false", "no", "off")


def _load_baseline():
    global _baseline
    p = ROOT / "baseline.json"
    if p.exists():
        try:
            _baseline = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            _baseline = {}


def init():
    global _tracer
    if _tracer is not None:
        return
    _load_baseline()
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        res = Resource.create({"service.name": SERVICE, "robot.body": os.environ.get("ROBOT_BODY", "unknown")})
        tp = TracerProvider(resource=res)
        tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{ENDPOINT}/v1/traces")))
        trace.set_tracer_provider(tp)
        _tracer = trace.get_tracer(SERVICE)

        if metrics_enabled():
            _init_metrics(res)
            print(f"[otel] exporting to {ENDPOINT}/v1/traces and /v1/metrics")
        else:
            print(f"[otel] exporting to {ENDPOINT}/v1/traces (metrics off: OTEL_METRICS=0)")
    except Exception as e:  # noqa: BLE001
        print(f"[otel] disabled ({e.__class__.__name__}: {e})")
        _tracer = False


def _init_metrics(res):
    global _meter
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http import metric_exporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    reader = PeriodicExportingMetricReader(
        metric_exporter.OTLPMetricExporter(endpoint=f"{ENDPOINT}/v1/metrics"), export_interval_millis=5000
    )
    logging.getLogger(metric_exporter.__name__).addFilter(_Metrics501Filter(reader))
    # Own exit hook: skip the provider shutdown once the reader is already down (avoids "Can't shutdown multiple times").
    mp = MeterProvider(resource=res, metric_readers=[reader], shutdown_on_exit=False)
    atexit.register(lambda: None if _metrics_disabled.is_set() else mp.shutdown())
    metrics.set_meter_provider(mp)
    _meter = metrics.get_meter(SERVICE)
    for name in ("robot_speed_mps", "robot_battery_pct", "robot_balance_ok", "robot_zone_idx"):
        _meter.create_observable_gauge(name, callbacks=[_cb(name)])


def _cb(name):
    def cb(options):
        from opentelemetry.metrics import Observation

        yield Observation(_gauges.get(name, 0.0))

    return cb


def gauge(name: str, value: float):
    _gauges[name] = float(value)


@contextmanager
def skill_span(name: str, **attrs):
    """Wrap a skill call. Yields a dict the skill fills with result attributes."""
    init()
    t0 = time.time()
    out: dict = {}
    if _tracer:
        with _tracer.start_as_current_span(f"skill.{name}") as span:
            for k, v in attrs.items():
                span.set_attribute(f"skill.arg.{k}", str(v))
            try:
                yield out
            finally:
                dur = (time.time() - t0) * 1000
                span.set_attribute("skill.duration_ms", dur)
                for k, v in out.items():
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(f"skill.{k}", v)
                z = anomaly_z(name, dur / 1000)
                dz = deviation_z(
                    name, out.get("deviation_pos_m"), out.get("deviation_dur_s"), out.get("deviation_pred_s")
                )
                if dz is not None:
                    span.set_attribute("skill.deviation_z", dz)
                    z = dz if z is None else max(z, dz)
                if z is not None:
                    span.set_attribute("skill.anomaly_z", z)
                    if z > 3:
                        span.add_event("anomaly", {"message": f"{name} took {z:.1f} sigma from baseline"})
    else:
        yield out


def anomaly_z(skill: str, duration_s: float) -> float | None:
    b = _baseline.get(skill)
    if not b or not b.get("std"):
        return None
    return abs(duration_s - b["mean"]) / b["std"]


DEV_POS_SIGMA_M = 0.10


def deviation_z(skill: str, pos_err: float | None, dur_err: float | None, pred_s: float | None = None) -> float | None:
    if pos_err is None and dur_err is None:
        return None
    b = _baseline.get(skill) or {}
    ps = float(b.get("deviation_pos_std") or DEV_POS_SIGMA_M)
    ds = float(b.get("deviation_dur_std") or max(0.5, 0.15 * float(pred_s or 0.0)))
    return round(max(abs(pos_err or 0.0) / ps, abs(dur_err or 0.0) / ds), 3)
