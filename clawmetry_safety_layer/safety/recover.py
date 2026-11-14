"""recover_to_go_zone: after a go-zone breach (or a stop just outside it), back the robot into the go zone.

stop -> reverse along its own -heading at 0.15 m/s until it is inside the go zone with a 0.3 m margin, at most
1.5 m / 8 s -> stop and hold. Before moving, the straight line behind the robot is checked: if no point within
1.5 m reaches the go zone with margin, or the way back crosses a forbidden zone, nothing moves and staff are asked.
Resuming afterwards needs approval (safety/fsm.py). Runs through the command gate, so the predictor still checks
every reverse command (it can only refuse, never extend the motion).
"""

from __future__ import annotations

import math
import time


def plan_reverse(state, go, policy, zones, margin: float, max_m: float, step: float = 0.05) -> tuple[float | None, str]:
    """Distance to back up (m) to reach the go zone with margin, or (None, why)."""
    c, s = math.cos(state.yaw), math.sin(state.yaw)
    if go.inside_with_margin(state.x, state.y, margin):
        return 0.0, "already inside with margin"
    d = 0.0
    while d <= max_m + 1e-9:
        x, y = state.x - c * d, state.y - s * d
        z = zones.zone_of(x, y)
        if d > 0 and policy.forbidden_zone(z) and z != zones.zone_of(state.x, state.y):
            return None, f"backing up would cross {z}"
        if go.inside_with_margin(x, y, margin):
            return d, "ok"
        d += step
    return None, f"the go zone is not within {max_m} m straight behind the robot"


def recover_to_go_zone(
    skills, go, speed: float = 0.15, margin: float = 0.3, max_m: float = 1.5, max_s: float = 8.0
) -> dict:
    sk = skills
    b = sk.bridge
    s = sk.gate.stop_and_settle()
    dist, why = plan_reverse(s, go, sk.policy, sk.zones, margin, max_m)
    sk.emit(
        "recover",
        phase="plan",
        distance_m=None if dist is None else round(dist, 2),
        detail=why,
        start=[round(s.x, 2), round(s.y, 2)],
    )
    if dist is None:
        return {"ok": False, "detail": why, "moved_m": 0.0}
    if dist == 0.0:
        return {"ok": True, "detail": why, "moved_m": 0.0}
    x0, y0 = s.x, s.y
    t0 = time.time()
    moved = 0.0
    ok = False
    try:
        while time.time() - t0 < max_s:
            st = b.state()
            moved = math.hypot(st.x - x0, st.y - y0)
            if go.inside_with_margin(st.x, st.y, margin):
                ok = True
                break
            if moved >= max_m:
                break
            sk.gate.move_velocity(-speed, 0.0, 0.0, until_m=min(max_m - moved, dist - moved + 0.05))
            time.sleep(0.05)
    except Exception as e:  # noqa: BLE001  (a refused reverse command ends recovery; the robot stays stopped)
        sk.gate.stop_and_settle()
        sk.emit("recover", phase="aborted", detail=str(e)[:200], moved_m=round(moved, 2))
        return {"ok": False, "detail": f"aborted: {e}", "moved_m": moved}
    st = sk.gate.stop_and_settle()
    ok = ok or go.inside_with_margin(st.x, st.y, margin)
    detail = (
        "inside the go zone with margin" if ok else f"not back inside after {moved:.2f} m / {time.time() - t0:.1f} s"
    )
    sk.emit(
        "recover", phase="done", ok=ok, moved_m=round(moved, 2), detail=detail, end=[round(st.x, 2), round(st.y, 2)]
    )
    return {"ok": ok, "detail": detail, "moved_m": moved}
