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
    reacquire_period_s: float = 0.5
    reacquire_max_candidates: int = 3
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
    check: str | None = None     # 'verify' | 'suspect' | 'reacquire' when this update embedded


def _embed_rows(embed, tracks):
    """Embeddings for tracks, or None when unavailable or not one row per track."""
    rows = embed(tracks)
    if rows is None or len(rows) != len(tracks):
        return None
    return np.asarray(rows, dtype=np.float32)


def _area(track) -> float:
    b = track.box
    return float((b[2] - b[0]) * (b[3] - b[1]))


def _proximity(track, w, h):
    """Big and centred wins: the guest is the person nearest the camera axis."""
    if w * h <= 0:  # no frame size yet: proximity is undefined
        return 0.0
    area_frac = _area(track) / float(w * h)
    cx = float(track.box[0] + track.box[2]) / 2.0 / w
    return area_frac * max(0.0, 1.0 - 2.0 * abs(cx - 0.5))


def unknown_output(state, tracks) -> LockOutput:
    """Output for an update that had to be skipped: no target, everyone unknown."""
    return LockOutput(state, None, None, {t.id: 'unknown' for t in tracks if t.visible})


class TargetLock:
    def __init__(self, params=None, auto_lock=False):
        self.p = params or LockParams()
        self._state = State.IDLE
        self._target_id = None
        self._auto_pending = auto_lock
        self._reason = None
        self._pending_event = None
        self._check = None
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
        self._last_embed = None
        self._seen_person = False

    def _clear_track_vars(self):
        self._last_verify = 0.0
        self._reject_count = 0
        self._sim = None
        self._lost_since = 0.0
        self._last_reacquire = None
        self._hits = {}
        self._suspect = False
        self._suspect_fails = 0
        self._last_suspect_check = None
        self._not_target_ids = set()  # ids proven to be someone else, never re-acquired

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
        self._check = None
        visible = [t for t in tracks if t.visible]
        cands = [t for t in visible if t.confirmed]
        if frame_w * frame_h <= 0:
            cands = []  # without a frame size nobody can be scored, so nobody is locked

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
        return LockOutput(self._state, target, cand_id, labels, similarity, event,
                          self._reason, self._check)

    # -- similarity -----------------------------------------------------------
    def _sims(self, rows, seeds_only):
        """Max cosine similarity of each row against the seeds (or the whole gallery)."""
        g = self._gallery[:self._n_seeds] if seeds_only else self._gallery
        if not g:
            return np.zeros(len(rows), np.float32)
        return (np.asarray(rows, np.float32) @ np.stack(g).T).max(axis=1)

    def _learn(self, e):
        self._gallery.append(e)
        # seeds are never evicted; a gallery_size below seed_size must not empty the list
        while len(self._gallery) > max(self.p.gallery_size, self._n_seeds):
            del self._gallery[self._n_seeds]

    # -- states ---------------------------------------------------------------
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
            self._gallery, self._n_seeds = [], 0
        # rate limit on the clock, not candidate identity: flicker must not embed every frame
        if best is not None and self._n_seeds < p.seed_size and (
                self._last_embed is None
                or now - self._last_embed >= p.lock_window_s / p.seed_size):
            self._last_embed = now
            rows = _embed_rows(embed, [best])
            if rows is not None:
                self._gallery.append(rows[0])
                self._n_seeds += 1
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
        if not target.visible:
            return None
        others = [t for t in visible if t.id != target.id]
        overlapping = {t.id for t in others if iou(t.box, target.box) > p.overlap_iou}
        # anyone seen clearly beside the target is provably not the guest
        self._not_target_ids.update(t.id for t in others if t.id not in overlapping)
        if overlapping:
            # the tracker can hand our id to the occluder: re-check once they separate
            if not self._suspect:
                self._suspect, self._last_suspect_check = True, None
            return None  # a crop holding two people says nothing about identity
        if self._suspect:
            return self._verify_suspect(now, target, embed)
        if now - self._last_verify < p.verify_period_s:
            return None
        return self._verify_periodic(now, target, embed)

    def _verify_suspect(self, now, target, embed):
        """After an overlap only the seeds can tell the guest from the occluder."""
        p = self.p
        if (self._last_suspect_check is not None
                and now - self._last_suspect_check < p.reacquire_period_s):
            return None
        rows = _embed_rows(embed, [target])
        if rows is None:
            return None
        self._last_suspect_check, self._check = now, 'suspect'
        e = rows[0]
        self._sim = sim = float(self._sims(e[None], True)[0])
        if sim >= p.sim_accept:
            self._suspect, self._suspect_fails, self._reject_count = False, 0, 0
            self._last_verify = now
            self._learn(e)
            return None
        self._suspect_fails += 1
        if self._suspect_fails >= p.reject_checks:
            return self._to_lost(now, 'appearance_mismatch')
        return None

    def _verify_periodic(self, now, target, embed):
        p = self.p
        rows = _embed_rows(embed, [target])
        if rows is None:
            return None
        self._last_verify, self._check = now, 'verify'
        e = rows[0]
        # keep/reject may use the gallery (tolerates pose change); learning may not drift
        self._sim = sim = float(self._sims(e[None], False)[0])
        if sim >= p.sim_accept:
            self._reject_count = 0
            if float(self._sims(e[None], True)[0]) >= p.sim_accept:
                self._learn(e)
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
        self._hits = {}
        self._suspect, self._suspect_fails, self._last_suspect_check = False, 0, None
        if reason == 'appearance_mismatch':
            # after a swap the ids mean nothing except: this one is not the guest
            self._not_target_ids = {self._target_id}
        return 'lost'

    def _lost(self, now, cands, embed):
        p = self.p
        if now - self._lost_since > p.lost_timeout_s:
            self._state, self._reason = State.IDLE, 'lost_timeout'
            self._target_id, self._gallery, self._n_seeds = None, [], 0
            self._not_target_ids = set()
            return 'lost_timeout'
        if (self._last_reacquire is not None
                and now - self._last_reacquire < p.reacquire_period_s):
            return None
        cands = [t for t in cands if t.id not in self._not_target_ids]
        if not cands:
            self._hits, self._sim = {}, None
            return None
        if len(cands) > p.reacquire_max_candidates:
            # bounded embedding budget: the biggest boxes are the people nearest the camera
            cands = sorted(cands, key=_area, reverse=True)[:p.reacquire_max_candidates]
        rows = _embed_rows(embed, cands)
        if rows is None:
            return None
        self._last_reacquire, self._check = now, 'reacquire'
        sims = self._sims(rows, True)  # granting identity always decides on the seeds
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
