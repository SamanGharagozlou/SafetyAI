from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from d1_follow.tracker import Track, iou


class State(str, Enum):
    IDLE = 'IDLE'
    LOCKING = 'LOCKING'
    TRACKING = 'TRACKING'
    LOST = 'LOST'


@dataclass
class LockParams:
    lock_window_s: float = 0.5
    lock_margin: float = 1.3
    lock_timeout_s: float = 5.0
    seed_size: int = 3
    gallery_size: int = 10
    verify_period_s: float = 1.0
    sim_accept: float = 0.70
    sim_reject: float = 0.50
    reject_checks: int = 2
    reacquire_period_s: float = 0.3
    reacquire_hits: int = 3
    reacquire_margin: float = 0.10
    lost_timeout_s: float = 30.0
    overlap_iou: float = 0.1


@dataclass
class LockOutput:
    state: State
    target: Track | None
    candidate_id: int | None
    labels: dict = field(default_factory=dict)
    similarity: float | None = None
    event: str | None = None
    reason: str | None = None


def _proximity(track, w, h):
    """Big and centred wins: the guest is the person nearest the camera axis."""
    b = track.box
    area_frac = float((b[2] - b[0]) * (b[3] - b[1])) / float(w * h)
    cx = float(b[0] + b[2]) / 2.0 / w
    return area_frac * max(0.0, 1.0 - 2.0 * abs(cx - 0.5))


class TargetLock:
    def __init__(self, params=None, auto_lock=False):
        self.p = params or LockParams()
        self._state = State.IDLE
        self._target_id = None
        self._auto_pending = auto_lock
        self._reason = None
        self._pending_event = None
        self._gallery = []
        self._n_seeds = 0
        self._clear_lock_vars()
        self._clear_track_vars()

    @property
    def state(self):
        return self._state

    @property
    def target_id(self):
        return self._target_id

    def _clear_lock_vars(self):
        self._lock_started = 0.0
        self._cand_id = None
        self._cand_since = 0.0
        self._last_seed = 0.0
        self._seen_person = False

    def _clear_track_vars(self):
        self._last_verify = 0.0
        self._reject_count = 0
        self._sim = None
        self._lost_since = 0.0
        self._last_reacquire = None
        self._hits = {}

    def request_lock(self, now):
        self._state = State.LOCKING
        self._target_id = None
        self._gallery, self._n_seeds = [], 0
        self._reason, self._pending_event = None, None
        self._clear_lock_vars()
        self._clear_track_vars()
        self._lock_started = now

    def release(self):
        self._state = State.IDLE
        self._target_id = None
        self._gallery, self._n_seeds = [], 0
        self._auto_pending = False
        self._reason, self._pending_event = 'released', 'released'
        self._clear_lock_vars()
        self._clear_track_vars()

    def update(self, now, tracks, frame_w, frame_h, embed):
        event, self._pending_event = self._pending_event, None
        visible = [t for t in tracks if t.visible]
        cands = [t for t in visible if t.confirmed]

        if self._state == State.IDLE and self._auto_pending and cands:
            self.request_lock(now)
        if self._state == State.LOCKING:
            event = self._locking(now, cands, frame_w, frame_h, embed) or event
        elif self._state == State.TRACKING:
            event = self._tracking(now, tracks, visible, embed) or event
        elif self._state == State.LOST:
            event = self._lost(now, cands, embed) or event

        target = None
        labels = {t.id: 'unknown' for t in visible}
        if self._state == State.TRACKING:
            target = next((t for t in visible if t.id == self._target_id), None)
            if target is not None:
                labels[target.id] = 'target'
        elif self._state == State.LOCKING and self._cand_id in labels:
            labels[self._cand_id] = 'candidate'
        similarity = self._sim if self._state in (State.TRACKING, State.LOST) else None
        cand_id = self._cand_id if self._state == State.LOCKING else None
        return LockOutput(self._state, target, cand_id, labels, similarity, event, self._reason)

    def _locking(self, now, cands, w, h, embed):
        p = self.p
        self._seen_person = self._seen_person or bool(cands)
        scored = sorted(((_proximity(t, w, h), t) for t in cands), key=lambda s: -s[0])
        best = scored[0][1] if scored else None
        # best must clearly beat the runner-up, otherwise we could lock onto the wrong guest
        if best is not None:
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if not (scored[0][0] > 0 and scored[0][0] >= p.lock_margin * runner):
                best = None
        cand_id = best.id if best is not None else None
        if cand_id != self._cand_id:
            self._cand_id, self._cand_since = cand_id, now
            self._last_seed, self._gallery, self._n_seeds = None, [], 0
        if best is not None and self._n_seeds < p.seed_size and (
                self._last_seed is None or now - self._last_seed >= p.lock_window_s / p.seed_size):
            rows = embed([best])
            if rows is not None and len(rows):
                self._gallery.append(np.asarray(rows[0], dtype=np.float32))
                self._n_seeds += 1
                self._last_seed = now
        if (best is not None and now - self._cand_since >= p.lock_window_s
                and self._n_seeds >= p.seed_size):
            self._state, self._target_id, self._auto_pending = State.TRACKING, best.id, False
            self._reason, self._last_verify = None, now
            self._clear_lock_vars()
            return 'locked'
        if now - self._lock_started > p.lock_timeout_s:
            self._reason = 'ambiguous' if self._seen_person else 'no_person'
            self._state, self._gallery, self._n_seeds = State.IDLE, [], 0
            self._clear_lock_vars()
            return 'lock_failed'
        return None

    def _tracking(self, now, tracks, visible, embed):
        p = self.p
        target = next((t for t in tracks if t.id == self._target_id), None)
        if target is None:
            return self._to_lost(now, 'track_lost')
        if not target.visible or now - self._last_verify < p.verify_period_s:
            return None
        rows = embed([target])
        if rows is None or not len(rows):
            return None
        e = np.asarray(rows[0], dtype=np.float32)
        self._last_verify = now
        self._sim = sim = float(np.max(np.stack(self._gallery) @ e))
        if sim >= p.sim_accept:
            self._reject_count = 0
            # a crop overlapping someone else may contain their pixels: do not learn from it
            if not any(t.id != target.id and iou(t.box, target.box) > p.overlap_iou
                       for t in visible):
                self._gallery.append(e)
                while len(self._gallery) > p.gallery_size:
                    del self._gallery[self._n_seeds]
        elif sim < p.sim_reject:
            self._reject_count += 1
            if self._reject_count >= p.reject_checks:
                return self._to_lost(now, 'appearance_mismatch')
        else:
            self._reject_count = 0
        return None

    def _to_lost(self, now, reason):
        self._state, self._reason = State.LOST, reason
        self._lost_since, self._last_reacquire = now, None
        self._sim, self._hits = None, {}
        return 'lost'

    def _lost(self, now, cands, embed):
        p = self.p
        if now - self._lost_since > p.lost_timeout_s:
            self._state, self._reason = State.IDLE, None
            self._target_id, self._gallery, self._n_seeds = None, [], 0
            return 'lost_timeout'
        if (self._last_reacquire is not None
                and now - self._last_reacquire < p.reacquire_period_s):
            return None
        self._last_reacquire = now
        if not cands:
            self._hits, self._sim = {}, None
            return None
        rows = embed(cands)
        if rows is None:
            return None
        sims = (np.asarray(rows, dtype=np.float32) @ np.stack(self._gallery).T).max(axis=1)
        self._hits = {t.id: self._hits.get(t.id, 0) + 1 if s >= p.sim_accept else 0
                      for t, s in zip(cands, sims)}
        order = np.argsort(-sims)
        best = int(order[0])
        second = float(sims[order[1]]) if len(sims) > 1 else 0.0
        self._sim = float(sims[best])
        bid = cands[best].id
        if self._hits[bid] >= p.reacquire_hits and sims[best] - second >= p.reacquire_margin:
            self._state, self._target_id, self._reason = State.TRACKING, bid, None
            self._reject_count, self._last_verify = 0, now
            return 'reacquired'
        return None
