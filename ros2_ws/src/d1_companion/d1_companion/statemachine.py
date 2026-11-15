"""Companion state machine: one guest session, one robot. Pure Python, no ROS, no network, no real clock (injected), so it is unit tested.

    handle(event) -> [effects]      events come from the guest (phone) or from the robot gateway; effects are what the driver must do
    tick()        -> [effects]      timeouts (call it every ~0.25 s)

States
  IDLE             no guest; robot waits at home
  AWAITING_INPUT   guest paired, robot listens (questions, pick a place)
  PREPARING        robot gets ready before a goal (stands up if lying)
  NAVIGATING       a goal is active (sub-status: sending -> received -> moving)
  ARRIVED          goal reached; offers the next step (another place / home / finish)
  BLOCKED          goal failed or was refused; says why, offers retry / other place / home
  STOPPED          guest pressed stop; robot halted
  RETURNING_HOME   no guest; robot drives back to `home`
  HOLD             robot unavailable (gateway down, SDK link down, damped by staff); nothing moves, guests are told

Safety rules built in: only ONE goal at a time (a new pick re-routes), a damped robot (staff E-STOP) is never stood up automatically,
a guest can always stop, battery below `min_battery` refuses new escorts, the gateway only ever gets goals / stop / named intents.
"""
import enum
import time
import uuid
from collections import deque
from dataclasses import dataclass, field


class S(str, enum.Enum):
    IDLE = 'IDLE'
    AWAITING_INPUT = 'AWAITING_INPUT'
    PREPARING = 'PREPARING'
    NAVIGATING = 'NAVIGATING'
    ARRIVED = 'ARRIVED'
    BLOCKED = 'BLOCKED'
    STOPPED = 'STOPPED'
    RETURNING_HOME = 'RETURNING_HOME'
    HOLD = 'HOLD'


INPUT_STATES = (S.AWAITING_INPUT, S.ARRIVED, S.BLOCKED, S.STOPPED)
MOVING_STATES = (S.PREPARING, S.NAVIGATING)


# ----------------------------------------------------------------------------- events / effects
@dataclass
class Event:
    kind: str
    data: dict = field(default_factory=dict)


def ev(kind, **data):
    return Event(kind, data)


# guest:  join(session, nickname) select(dest) say(text) stop end
# robot:  goal_received/goal_accepted/goal_progress/goal_reached/goal_failed/goal_canceled/goal_rejected (id, reason, distance)
#         intent_done/intent_failed (id, reason)   robot_state(state=<gateway state dict>)   gateway_up / gateway_down


@dataclass
class Effect:
    kind: str           # send_goal | stop_robot | intent | say | session_closed | join_rejected | log
    data: dict = field(default_factory=dict)


def fx(kind, **data):
    return Effect(kind, data)


@dataclass
class Config:
    home: str = 'home'
    min_battery: float = 0.20          # fraction; below it no new escorts (the SDK refuses commands under 20 % anyway)
    goal_receive_s: float = 3.0        # gateway must answer a goal with goal_received within this
    goal_accept_s: float = 15.0        # ... and Nav2 must accept/reject within this
    goal_total_s: float = 180.0        # a single escort may take at most this long
    prepare_s: float = 20.0            # stand-up must finish within this
    input_prompt_s: float = 60.0       # silence in an input state -> "still there?"
    input_timeout_s: float = 120.0     # more silence -> session ends
    greet_gesture: bool = False        # also play a hello gesture (shake_hand) on join
    farewell_gesture: bool = False


class Companion:
    def __init__(self, places, config=None, clock=time.monotonic, nlu=None, answerer=None):
        """places: {name: {x, y, yaw, label, aliases, description}}.  nlu(text, places) -> (kind, dest|None).  answerer(text) -> str."""
        self.places, self.cfg, self.clock = dict(places), config or Config(), clock
        from d1_companion import nlu as _nlu
        self.nlu = nlu or _nlu.parse
        self.answerer = answerer or (lambda text: _nlu.default_answer(text, self.places))
        self.state = S.IDLE
        self.session = None                 # {'id','nickname','lang','started'}
        self.dest = None                    # destination name of the active / last goal
        self.goal_id = None
        self.goal_status = None             # sending | received | moving
        self.distance = None
        self.at = self.cfg.home if self.cfg.home in self.places else None      # place the robot stands at (None = in between)
        self.robot = {}                     # latest gateway `state`
        self.gateway_up = False
        self.reason = ''                    # why BLOCKED / HOLD / refused
        self.message = 'Ready.'
        self.pending_dest = None            # destination waiting for the robot to stand up (PREPARING)
        self.last_dest = None               # for "retry"
        self._deadline = {}                 # name -> time
        self._prompted = False
        self._hold_from = None
        self._stopping = set()              # goal ids we stopped on purpose; their cancel events are expected
        self._returning_goal = None
        self.tag = None                     # caller's tag of the current 'go' (e.g. a request id), so its outcome can be reported
        self.outcomes = {}                  # tag -> {'kind': reached|failed|stopped|rerouted|refused|already_there, 'message', 'reason'}
        self.log = deque(maxlen=200)

    # ------------------------------------------------------------------ helpers
    def _t(self):
        return self.clock()

    def _set(self, state, message=None):
        if state != self.state:
            self.log.append((round(self._t(), 2), 'state', self.state.value, state.value))
        self.state = state
        if message is not None:
            self.message = message
        if state in INPUT_STATES:
            self._arm('input_prompt', self.cfg.input_prompt_s)
            self._arm('input_timeout', self.cfg.input_timeout_s)
            self._prompted = False
        else:
            self._disarm('input_prompt', 'input_timeout')

    def _arm(self, name, seconds):
        self._deadline[name] = self._t() + seconds

    def _disarm(self, *names):
        for n in names:
            self._deadline.pop(n, None)

    def _say(self, text, session=None):
        self.message = text
        return fx('say', text=text, session=session)

    def _finish(self, kind, message, reason=''):
        """Record how the current tagged 'go' ended (once)."""
        if self.tag is not None:
            self.outcomes[self.tag] = {'kind': kind, 'message': message, 'reason': reason}
            while len(self.outcomes) > 100:
                self.outcomes.pop(next(iter(self.outcomes)))
            self.tag = None

    def label(self, name):
        return (self.places.get(name) or {}).get('label') or name

    def at_home(self):
        return self.at == self.cfg.home

    def _new_goal_id(self, dest):
        return f'{dest}-{uuid.uuid4().hex[:6]}'

    # ------------------------------------------------------------------ robot health
    def hold_reason(self):
        """Hard reasons nothing may move (None = fine)."""
        if not self.gateway_up:
            return 'the robot control link is down'
        r = self.robot
        if r.get('connected') is False:
            return 'the robot lost its connection'
        if r.get('mode') == 'damping':
            return 'the robot was stopped by staff'
        return None

    def escort_refusal(self):
        """Reasons not to start a NEW escort (None = ok)."""
        h = self.hold_reason()
        if h:
            return h
        r = self.robot
        if r.get('nav_ready') is False:
            return 'navigation is not running'
        loc = r.get('localization')
        if loc is not None and not loc.get('ready'):
            return 'the robot is not placed on the map yet'
        b = r.get('battery')
        if b is not None and b < self.cfg.min_battery:
            return 'the battery is too low for another escort'
        return None

    # ------------------------------------------------------------------ main entry points
    def handle(self, e):
        before = self.state
        out = []
        fn = getattr(self, 'on_' + e.kind, None)
        if fn is None:
            self.log.append((round(self._t(), 2), 'ignored', e.kind, ''))
            return out
        out = fn(e.data) or []
        if self.state != before or e.kind not in ('goal_progress', 'robot_state'):
            self.log.append((round(self._t(), 2), e.kind, before.value, self.state.value))
        return out

    def tick(self):
        out, now = [], self._t()
        due = [n for n, d in self._deadline.items() if now >= d]
        for n in due:
            self._deadline.pop(n, None)
            out += getattr(self, 'timeout_' + n)() or []
        return out

    # ------------------------------------------------------------------ guest events
    def on_join(self, d):
        why = self.hold_reason()
        if self.state == S.HOLD or why:
            return [fx('join_rejected', reason=f'The robot is not available right now ({why or self.reason}). Please ask a staff member.')]
        if self.session is not None:
            return [fx('join_rejected', reason='The robot is already with another guest.')]
        out = []
        if self.state == S.RETURNING_HOME:             # a guest arrives while the robot drives home: stop and take them
            out.append(fx('stop_robot'))
            self._returning_goal = None
            self.goal_id, self.goal_status = None, None
        self.session = {'id': d.get('session') or uuid.uuid4().hex[:12], 'nickname': d.get('nickname') or 'Guest',
                        'lang': d.get('lang') or 'en', 'started': self._t()}
        self._set(S.AWAITING_INPUT)
        names = [self.label(n) for n in self.places if n != self.cfg.home]
        out.append(self._say(f"Hello {self.session['nickname']}! I'm your robot companion. "
                             + (f"I can take you to: {', '.join(names)}. " if names else '') + 'Where would you like to go?'))
        if self.cfg.greet_gesture:
            out.append(fx('intent', id='greet-' + uuid.uuid4().hex[:6], name='shake_hand', args={}))
        return out

    def on_select(self, d):
        if self.session is None:
            return []
        return self._go(d.get('dest'), d.get('tag'))

    def on_say(self, d):
        if self.session is None:
            return []
        text = (d.get('text') or '').strip()
        if not text:
            return []
        kind, dest = self.nlu(text, self.places)
        if kind == 'select':
            return self._go(dest)
        if kind == 'home':
            return self._go(self.cfg.home)
        if kind == 'stop':
            return self.on_stop({})
        if kind == 'end':
            return self.on_end({})
        if kind == 'retry':
            return self._go(self.last_dest) if self.last_dest else [self._say('There is nothing to retry yet. Where would you like to go?')]
        self._touch()
        return [self._say(self.answerer(text))]

    def on_stop(self, d):
        if self.session is None and self.state != S.RETURNING_HOME:
            return []
        if self.state in MOVING_STATES or self.state == S.RETURNING_HOME:
            if self.goal_id:
                self._stopping.add(self.goal_id)
            self.pending_dest, self.goal_id, self.goal_status, self.distance = None, None, None, None
            self._disarm('goal_receive', 'goal_accept', 'goal_total', 'prepare')
            self._finish('stopped', 'Stopped as requested.')
            if self.state == S.RETURNING_HOME:
                self._set(S.IDLE, 'Stopped.')
                return [fx('stop_robot'), self._say('Stopped.')]
            self._set(S.STOPPED)
            return [fx('stop_robot'), self._say('Okay, I have stopped. Tell me where you would like to go, or say home.')]
        return [self._say('I am not moving.')]

    def on_end(self, d, reason='Goodbye! It was nice to walk with you.'):
        if self.session is None:
            return []
        reason = (d or {}).get('reason') or reason
        out = []
        self._finish('stopped', 'The session ended.')
        if self.state in MOVING_STATES and self.goal_id:
            self._stopping.add(self.goal_id)
        sid = self.session['id']
        self.session = None
        self.pending_dest, self.goal_id, self.goal_status, self.distance = None, None, None, None
        self._disarm('goal_receive', 'goal_accept', 'goal_total', 'prepare')
        out.append(self._say(reason, session=sid))
        if self.cfg.farewell_gesture:
            out.append(fx('intent', id='bye-' + uuid.uuid4().hex[:6], name='shake_hand', args={}))
        out.append(fx('session_closed', session=sid, reason=reason))
        if self.state == S.HOLD:
            self._set(S.HOLD)
            return out
        if self.at_home() and self.state not in MOVING_STATES:
            self._set(S.IDLE, 'Ready.')
        else:
            out += self._return_home()
        return out

    # ------------------------------------------------------------------ going somewhere
    def _go(self, dest, tag=None):
        self._touch()

        def refused(kind, text, reason=''):
            if tag is not None:
                self.outcomes[tag] = {'kind': kind, 'message': text, 'reason': reason}
            return [self._say(text)]
        if dest not in self.places:
            return refused('refused', "Sorry, I don't know that place.", 'unsupported_request')
        if self.state == S.HOLD:
            return refused('refused', f'I cannot move right now: {self.reason}.', 'robot_unavailable')
        if self.state in INPUT_STATES and self.at == dest and self.state != S.BLOCKED:
            return refused('already_there', f"We are already at {self.label(dest)}.")
        if self.state == S.NAVIGATING and self.dest == dest and self.goal_status in ('sending', 'received', 'moving'):
            return refused('already_there', f"We are already heading to {self.label(dest)}.")
        if self.state == S.PREPARING:
            if self.tag is not None:
                self._finish('rerouted', 'A new destination was chosen.')
            self.tag, self.pending_dest = tag, dest
            return [self._say(f'Okay, {self.label(dest)} next. One moment, getting up.')]
        self.last_dest = dest
        why = self.escort_refusal()
        if why:
            self.reason = why
            self._set(S.BLOCKED if self.state != S.HOLD else S.HOLD)
            return refused('refused', f"I can't start right now: {why}. A staff member can help.", 'robot_unavailable')
        if self.tag is not None and self.state in MOVING_STATES:
            self._finish('rerouted', 'A new destination was chosen.')
        self.tag = tag
        if self.robot.get('mode') == 'lying':
            self.pending_dest = dest
            self._set(S.PREPARING, 'One moment, getting up.')
            self._arm('prepare', self.cfg.prepare_s)
            return [self._say('One moment, getting up.'), fx('intent', id='up-' + uuid.uuid4().hex[:6], name='stand_up', args={})]
        return self._send_goal(dest)

    def _send_goal(self, dest, returning=False):
        gid = self._new_goal_id(dest)
        if self.goal_id and self.state in MOVING_STATES:          # re-route: the gateway preempts the old goal
            self._stopping.add(self.goal_id)
        self.dest, self.goal_id, self.goal_status, self.distance = dest, gid, 'sending', None
        self.pending_dest = None
        self._disarm('prepare')
        self._arm('goal_receive', self.cfg.goal_receive_s)
        self._arm('goal_total', self.cfg.goal_total_s)
        self._disarm('goal_accept')
        if returning:
            self._returning_goal = gid
            self._set(S.RETURNING_HOME, 'Heading back to the start point.')
            return [fx('send_goal', id=gid, place=dest)]
        self._set(S.NAVIGATING, f'Follow me, we are going to {self.label(dest)}.')
        return [fx('send_goal', id=gid, place=dest), fx('say', text=self.message)]

    def _return_home(self):
        if self.cfg.home not in self.places:
            self._set(S.IDLE, 'Ready.')
            return []
        return self._send_goal(self.cfg.home, returning=True)

    def _touch(self):
        if self.state in INPUT_STATES:
            self._arm('input_prompt', self.cfg.input_prompt_s)
            self._arm('input_timeout', self.cfg.input_timeout_s)
            self._prompted = False

    # ------------------------------------------------------------------ robot events
    def _mine(self, d):
        return d.get('id') is not None and d.get('id') == self.goal_id

    def on_goal_received(self, d):
        if self._mine(d) and self.goal_status == 'sending':
            self.goal_status = 'received'
            self._disarm('goal_receive')
            self._arm('goal_accept', self.cfg.goal_accept_s)
        return []

    def on_goal_accepted(self, d):
        if self._mine(d) and self.goal_status in ('sending', 'received'):
            self.goal_status = 'moving'
            self._disarm('goal_receive', 'goal_accept')
        return []

    def on_goal_progress(self, d):
        if self._mine(d):
            self.distance = d.get('distance')
            if self.goal_status != 'moving':
                self.goal_status = 'moving'
                self._disarm('goal_receive', 'goal_accept')
        return []

    def on_goal_reached(self, d):
        if not self._mine(d):
            return []
        gid, dest = self.goal_id, self.dest
        self.goal_id, self.goal_status, self.distance, self.at = None, None, None, dest
        self._disarm('goal_receive', 'goal_accept', 'goal_total')
        if self.state == S.RETURNING_HOME and gid == self._returning_goal:
            self._returning_goal = None
            self._set(S.IDLE, 'Ready.')
            return []
        if self.state != S.NAVIGATING:
            return []
        others = [self.label(n) for n in self.places if n not in (dest, self.cfg.home)]
        text = f'We have arrived at {self.label(dest)}.'
        if dest == self.cfg.home:
            text += ' Is there anything else I can help you with, or shall we finish?'
        else:
            text += ' Where next?' + (f" I can take you to {', '.join(others)}, or back to the start." if others else ' I can take you back to the start.')
        self._set(S.ARRIVED, text)
        self._finish('reached', text)
        return [fx('say', text=text)]

    def _goal_lost(self, d, kind):
        if not self._mine(d):
            if d.get('id') in self._stopping:                      # expected result of our own stop / re-route
                self._stopping.discard(d.get('id'))
            return []
        reason = d.get('reason') or kind
        gid, dest = self.goal_id, self.dest
        self.goal_id, self.goal_status, self.distance = None, None, None
        self._disarm('goal_receive', 'goal_accept', 'goal_total')
        if self.state == S.RETURNING_HOME:
            self._returning_goal = None
            self._set(S.IDLE, 'Ready.')
            return [fx('log', text=f'return home failed: {reason}')]
        if self.state != S.NAVIGATING:
            return []
        self.reason = reason
        self.at = None if kind != 'goal_rejected' else self.at
        self._set(S.BLOCKED)
        text = f"I couldn't get to {self.label(dest)}: {reason}. I can try again, take you somewhere else, or go back to the start."
        self._finish('failed', text, 'robot_unavailable' if kind == 'goal_rejected' else 'execution_failed')
        return [self._say(text), fx('stop_robot')]

    def on_goal_failed(self, d):
        return self._goal_lost(d, 'goal_failed')

    def on_goal_canceled(self, d):
        return self._goal_lost(d, 'goal_canceled')

    def on_goal_rejected(self, d):
        return self._goal_lost(d, 'goal_rejected')

    def on_intent_done(self, d):
        if self.state == S.PREPARING and self.pending_dest and str(d.get('id', '')).startswith('up-'):
            return self._send_goal(self.pending_dest)
        return []

    def on_intent_failed(self, d):
        if self.state == S.PREPARING and str(d.get('id', '')).startswith('up-'):
            self.pending_dest = None
            self.reason = d.get('reason') or 'the robot could not stand up'
            self._disarm('prepare')
            self._set(S.BLOCKED)
            text = f"I could not stand up: {self.reason}. A staff member can help."
            self._finish('failed', text, 'robot_unavailable')
            return [self._say(text)]
        return []

    def on_robot_state(self, d):
        self.robot = d.get('state') or {}
        out = []
        h = self.hold_reason()
        if h and self.state != S.HOLD:
            out += self._enter_hold(h)
        elif not h and self.state == S.HOLD:
            out += self._leave_hold()
        return out

    def on_gateway_up(self, d):
        self.gateway_up = True
        return []

    def on_gateway_down(self, d):
        self.gateway_up = False
        if self.state != S.HOLD:
            return self._enter_hold(self.hold_reason() or 'the robot control link is down')
        return []

    def _enter_hold(self, why):
        self.reason = why
        self._hold_from = self.state
        out = []
        if self.state in MOVING_STATES or self.state == S.RETURNING_HOME:
            if self.goal_id:
                self._stopping.add(self.goal_id)
            out.append(fx('stop_robot'))
        self.pending_dest, self.goal_id, self.goal_status, self.distance = None, None, None, None
        self._disarm('goal_receive', 'goal_accept', 'goal_total', 'prepare')
        self._set(S.HOLD)
        self._finish('failed', f'I had to pause: {why}.', 'robot_unavailable')
        if self.session:
            out.append(self._say(f'I have to pause: {why}. Please wait, or ask a staff member.'))
        return out

    def _leave_hold(self):
        if self.session:
            self._set(S.AWAITING_INPUT)
            return [self._say('I am ready again. Where would you like to go?')]
        self._set(S.IDLE, 'Ready.')
        return []

    # ------------------------------------------------------------------ timeouts
    def timeout_goal_receive(self):
        if self.state in (S.NAVIGATING, S.RETURNING_HOME) and self.goal_status == 'sending':
            return self._goal_lost({'id': self.goal_id, 'reason': 'the robot did not answer'}, 'goal_failed')

    def timeout_goal_accept(self):
        if self.state in (S.NAVIGATING, S.RETURNING_HOME) and self.goal_status == 'received':
            return self._goal_lost({'id': self.goal_id, 'reason': 'navigation did not start'}, 'goal_failed')

    def timeout_goal_total(self):
        if self.state in (S.NAVIGATING, S.RETURNING_HOME) and self.goal_id:
            out = list(self._goal_lost({'id': self.goal_id, 'reason': 'it is taking too long'}, 'goal_failed') or [])
            return out if any(e.kind == 'stop_robot' for e in out) else [fx('stop_robot')] + out

    def timeout_prepare(self):
        if self.state == S.PREPARING:
            return self.on_intent_failed({'id': 'up-timeout', 'reason': 'it took too long'})

    def timeout_input_prompt(self):
        if self.session and self.state in INPUT_STATES and not self._prompted:
            self._prompted = True
            return [self._say('Are you still there? Tell me where you would like to go, or say finish.')]

    def timeout_input_timeout(self):
        if self.session and self.state in INPUT_STATES:
            return self.on_end({}, reason='I have not heard from you for a while, so I will end our session. Goodbye!')

    # ------------------------------------------------------------------ view for the phone
    def options(self):
        opts = []
        if self.session is None or self.state == S.HOLD:
            return opts
        if self.state in INPUT_STATES:
            for n in self.places:
                if n == self.at and self.state != S.BLOCKED:
                    continue
                opts.append({'kind': 'place', 'dest': n, 'label': self.label(n)})
            if self.state == S.BLOCKED and self.last_dest:
                opts.insert(0, {'kind': 'retry', 'dest': self.last_dest, 'label': 'Try again'})
            opts.append({'kind': 'end', 'label': 'Finish'})
        elif self.state in MOVING_STATES:
            opts.append({'kind': 'stop', 'label': 'Stop'})
        return opts

    def snapshot(self):
        r = self.robot or {}
        return {'state': self.state.value, 'goal_status': self.goal_status, 'destination': self.dest if self.state in MOVING_STATES else None,
                'last_destination': self.dest, 'at': self.at, 'distance_remaining': self.distance, 'message': self.message,
                'reason': self.reason if self.state in (S.BLOCKED, S.HOLD) else '', 'options': self.options(),
                'session': None if self.session is None else {'id': self.session['id'], 'nickname': self.session['nickname']},
                'robot': {'pose': r.get('pose'), 'battery': r.get('battery'), 'mode': r.get('mode'), 'connected': r.get('connected'),
                          'localization': (r.get('localization') or {}).get('state'), 'gateway_up': self.gateway_up}}
