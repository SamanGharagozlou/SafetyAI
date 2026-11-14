"""Wire protocol of the robot gateway (JSON text frames over one WebSocket). No ROS imports: usable by tests/mocks.

Every message is an object with a string `type`. Frontend -> robot ("commands") carry a client-chosen `id` that every
answer about it repeats, so a frontend can match `goal_reached` to the goal it sent. Robot -> frontend messages carry `ts` (unix s).

  commands   hello, goal, cancel, intent, stop, estop, ping
  events     hello_ok, error, pong, state,
             goal_received, goal_accepted, goal_rejected, goal_progress, goal_reached, goal_failed, goal_canceled,
             intent_received, intent_started, intent_done, intent_failed, robot_event
See docs/robot_gateway.md.
"""
import math
import time

PROTOCOL_VERSION = 1

COMMANDS = ('hello', 'goal', 'cancel', 'intent', 'stop', 'estop', 'ping')

# intent name -> what the robot does. `args` are intent-specific and optional.
INTENTS = {
    'say_hi': 'wave/shake hand (and say args.text if given)',
    'shake_hand': 'shake hand',
    'stand_up': 'stand up',
    'lie_down': 'lie down',
    'say': 'speak args.text (published on /say for the TTS node)',
}

KEEP_NULL = ('pose', 'battery', 'active_goal')     # present in every `state`, null when unknown

GOAL_FINAL = ('goal_reached', 'goal_failed', 'goal_canceled', 'goal_rejected')


def now():
    return round(time.time(), 3)


def event(type_, **kw):
    d = {'type': type_, 'ts': now()}
    d.update({k: v for k, v in kw.items() if v is not None or k in KEEP_NULL})
    return d


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(msg):
    """Return (None, normalized) or (error string, None). Never raises."""
    if not isinstance(msg, dict) or not isinstance(msg.get('type'), str):
        return 'message must be a JSON object with a string "type"', None
    t = msg['type']
    if t not in COMMANDS:
        return f'unknown type {t!r}; expected one of {", ".join(COMMANDS)}', None
    if t in ('goal', 'intent') and not (isinstance(msg.get('id'), str) and 0 < len(msg['id']) <= 64):
        return f'{t} needs a string "id" (1-64 chars) so answers can be matched', None
    if t == 'goal':
        if 'place' in msg:
            if not isinstance(msg['place'], str):
                return '"place" must be a string', None
        else:
            tgt = msg.get('target')
            if not isinstance(tgt, dict) or not (_num(tgt.get('x')) and _num(tgt.get('y'))):
                return 'goal needs "place" or "target": {"x","y"[,"yaw"]}', None
            if 'yaw' in tgt and not _num(tgt['yaw']):
                return 'target.yaw must be a number (rad)', None
    if t == 'intent':
        if msg.get('name') not in INTENTS:
            return f'unknown intent {msg.get("name")!r}; expected one of {", ".join(INTENTS)}', None
        if 'args' in msg and not isinstance(msg['args'], dict):
            return '"args" must be an object', None
    if t == 'cancel' and 'id' in msg and not isinstance(msg['id'], str):
        return '"id" must be a string', None
    return None, msg
