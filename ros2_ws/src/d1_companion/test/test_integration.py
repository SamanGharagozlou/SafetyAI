"""End to end over real sockets, no ROS: HTTP client (the frontend's role) <-> CompanionServer <-> d1_gateway (mock robot)."""
import asyncio
import json
import os
import re
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'd1_gateway'))
from d1_companion.server import CompanionServer  # noqa: E402
from d1_companion.statemachine import Config, S  # noqa: E402
from d1_gateway.core import GatewayCore  # noqa: E402
from d1_gateway.mock_robot import MockRobot  # noqa: E402

PLACES = {
    'home': {'x': 0, 'y': 0, 'yaw': 0, 'label': 'Start point', 'aliases': ['home', 'start'], 'description': 'Where I wait.'},
    'front_2m': {'x': 2, 'y': 0, 'yaw': 0, 'label': 'Front, 2 m ahead', 'aliases': ['front'], 'description': ''},
    'left_2m': {'x': 0, 'y': 2, 'yaw': 1.57, 'label': 'Left, 2 m', 'aliases': ['left'], 'description': ''},
    'far': {'x': 60, 'y': 0, 'yaw': 0, 'label': 'Far away', 'aliases': ['far'], 'description': ''},
}
TOKEN = 'secret-join-token'
COUNTER = [0]


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


async def http(port, method, path, body=None, headers=None, raw_body=None):
    r, w = await asyncio.open_connection('127.0.0.1', port)
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else b'')
    h = {'Host': f'127.0.0.1:{port}', 'Content-Length': str(len(data)), 'Connection': 'close'}
    if data and 'Content-Type' not in (headers or {}):
        h['Content-Type'] = 'application/json'
    h.update(headers or {})
    w.write((f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in h.items()) + '\r\n').encode() + data)
    await w.drain()
    resp = await asyncio.wait_for(r.read(), 10)
    w.close()
    head, _, payload = resp.partition(b'\r\n\r\n')
    status = int(head.split(b' ')[1])
    hdrs = {l.split(b':')[0].decode().lower(): l.split(b':', 1)[1].strip().decode() for l in head.split(b'\r\n')[1:]}
    try:
        return status, json.loads(payload), hdrs
    except ValueError:
        return status, payload, hdrs


class Guest:
    def __init__(self, port, token=None):
        self.port, self.token = port, token

    @property
    def auth(self):
        return {'Authorization': f'Bearer {self.token}'}

    async def call(self, method, path, body=None, **kw):
        return await http(self.port, method, path, body, {**self.auth, **kw.pop('headers', {})}, **kw)

    async def join(self, nick='Ana'):
        st, j, _ = await http(self.port, 'POST', '/api/session', {'token': TOKEN, 'nickname': nick})
        assert st == 201, (st, j)
        self.token = j['accessToken']
        return j

    async def request(self, text, proposed=None, place=None, cid=None, source='typed'):
        COUNTER[0] += 1
        cid = cid or f'c-{COUNTER[0]}'
        body = {'text': text, 'source': source, 'proposed_action': proposed, 'client_request_id': cid}
        if place:
            body['place'] = place
        return await self.call('POST', '/api/requests', body, headers={'Idempotency-Key': cid})

    async def wait_status(self, rid, wanted, timeout=15):
        for _ in range(int(timeout / 0.1)):
            st, t, _ = await self.call('GET', f'/api/requests/{rid}')
            check_contract(t)
            if t['status'] in wanted:
                return t
            await asyncio.sleep(0.1)
        raise AssertionError(f'timeout waiting for {wanted}; last {t}')


def check_contract(t):
    """The frontend's decoder (requestApiAdapter.ts decodeTask) rules."""
    assert re.match(r'^[a-zA-Z0-9_-]{1,100}$', t['request_id']) and re.match(r'^[a-zA-Z0-9-]{1,100}$', t['client_request_id'])
    assert t['status'] in ('queued', 'processing', 'awaiting_approval', 'executing', 'completed', 'denied', 'failed')
    assert t['action'] is None or t['action'] in ('GREET', 'ROBOT_INFO', 'HELP', 'GO_TO_PLACE', 'STOP_ROBOT', 'END_SESSION')
    assert isinstance(t['revision'], int) and t['revision'] >= 0 and t['demo'] is False
    assert t.get('reason') in (None, 'unsupported_request', 'approval_denied', 'execution_failed', 'robot_unavailable')
    if t['status'] == 'completed':
        assert t['action'] is not None and isinstance(t['reply'], str) and t['reply'].strip() and len(t['reply']) <= 2000
    else:
        assert 'reply' not in t


async def with_stack(fn, robot=None, **kw):
    gw = GatewayCore(robot or MockRobot(speed=4.0, intent_s=0.2), PLACES, port=0, state_hz=20)
    await gw.serve()
    cfg = Config(**{k: v for k, v in kw.items() if k in Config.__dataclass_fields__})
    extra = {k: v for k, v in kw.items() if k not in Config.__dataclass_fields__}
    comp = CompanionServer(PLACES, cfg, f'ws://127.0.0.1:{gw.port}', join_token=TOKEN, admin_token='staff', port=0, **extra)
    await comp.start()
    for _ in range(100):
        if comp.gw.up and comp.sm.robot:
            break
        await asyncio.sleep(0.05)
    try:
        await fn(comp.port, comp, gw)
    finally:
        await comp.stop()
        await gw.close()


def test_qr_token_and_auth_are_required():
    async def t(port, comp, gw):
        st, j, _ = await http(port, 'POST', '/api/session', {'token': 'wrong'})
        assert st == 403 and j['error'] == 'invalid_qr' and comp.sm.session is None
        for method, path in (('GET', '/api/places'), ('GET', '/api/state'), ('POST', '/api/requests'), ('POST', '/api/robot/stop'),
                             ('GET', '/api/requests/req-1'), ('DELETE', '/api/session')):
            st, j, _ = await http(port, method, path, {} if method == 'POST' else None)
            assert st == 401 and j['error'] == 'session_expired', (method, path, st)
        st, j, _ = await http(port, 'GET', '/api/places', headers={'Authorization': 'Bearer guess'})
        assert st == 401
    run(with_stack(t))


def test_session_busy_resume_and_no_map_is_ever_served():
    async def t(port, comp, gw):
        a = Guest(port)
        j = await a.join()
        assert isinstance(j['accessToken'], str) and len(j['accessToken']) >= 24 and j['expiresAt'] > 1_700_000_000_000
        assert {p['name'] for p in j['places']} == set(PLACES) and all(set(p) == {'name', 'label', 'description', 'x', 'y', 'home'} for p in j['places'])
        assert 'map' not in json.dumps(j).lower().replace('name', '')                       # layout only
        st, j2, _ = await http(port, 'POST', '/api/session', {'token': TOKEN, 'nickname': 'Bob'})
        assert st == 409 and j2['error'] == 'robot_busy'
        st, j3, _ = await http(port, 'POST', '/api/session', {}, {'Authorization': f'Bearer {a.token}'})        # same guest reloads the page
        assert st == 200 and j3['accessToken'] == a.token
        for path in ('/map.png', '/map.png?s=' + a.token, '/api/map', '/map.pgm'):
            st, _, _ = await a.call('GET', path)
            assert st == 404, path
        st, pl, _ = await a.call('GET', '/api/places')
        assert st == 200 and len(pl['places']) == len(PLACES)
    run(with_stack(t))


def test_escort_request_lifecycle_and_idempotency():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, task, _ = await g.request('take me to the front', 'GO_TO_PLACE', cid='tap-front-1')
        assert st == 201 and task['action'] == 'GO_TO_PLACE' and task['status'] in ('processing', 'executing') and task['place'] == 'front_2m'
        check_contract(task)
        st2, again, _ = await g.request('take me to the front', 'GO_TO_PLACE', cid='tap-front-1')               # network retry: same request, no second goal
        assert st2 == 200 and again['request_id'] == task['request_id']
        st3, conflict, _ = await g.request('take me to the left', 'GO_TO_PLACE', cid='tap-front-1')
        assert st3 == 409 and conflict['error'] == 'conflict'
        done = await g.wait_status(task['request_id'], ('completed', 'failed'))
        assert done['status'] == 'completed' and 'arrived at Front' in done['reply'] and done['revision'] >= 2
        assert abs(gw.robot.pose['x'] - 2.0) < 0.2                                                                # the (mock) robot really got there
        st, s, _ = await g.call('GET', '/api/state')
        assert s['state'] == 'ARRIVED' and s['at'] == 'front_2m' and any('arrived' in m['text'] for m in s['messages'])
        assert set(s['robot']) == {'x', 'y', 'yaw', 'available'}                                                  # nothing internal leaks (battery, mode, ...)
        st, s2, _ = await g.call('GET', f'/api/state?after={s["seq"]}')
        assert s2['messages'] == []
    run(with_stack(t))


def test_tapped_place_text_decides_and_conflicts_are_not_guessed():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, bad, _ = await g.request('take me to the front', 'GO_TO_PLACE', place='left_2m')         # text and tapped place disagree
        assert st == 201 and bad['status'] == 'denied' and bad['reason'] == 'unsupported_request' and gw.robot.pose['x'] == 0
        st, ok, _ = await g.request('Left, 2 m', 'GO_TO_PLACE', place='left_2m')
        assert ok['action'] == 'GO_TO_PLACE' and ok['place'] == 'left_2m'
        st, ok2, _ = await g.request('please', 'GO_TO_PLACE', place='far')                           # explicit place, vague text: the place is used
        assert ok2['place'] == 'far'
        st, nope, _ = await g.request('please', 'GO_TO_PLACE', place='moon')                          # unknown place name is never forwarded
        assert nope['status'] == 'denied'
    run(with_stack(t))


def test_instant_actions_and_honest_unknowns():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join('Maria')
        for text, proposed, action in (('hello robot', None, 'GREET'), ('who are you?', None, 'ROBOT_INFO'), ('what places do you know?', 'HELP', 'HELP')):
            st, task, _ = await g.request(text, proposed)
            check_contract(task)
            assert st == 201 and task['status'] == 'completed' and task['action'] == action, (text, task)
        assert 'Maria' in (await g.request('hello', None))[1]['reply']
        st, unk, _ = await g.request('how hot is the sauna', None)
        check_contract(unk)
        assert unk['status'] == 'denied' and unk['reason'] == 'unsupported_request' and 'message' in unk              # no invented facts
        assert comp.sm.state == S.AWAITING_INPUT
    run(with_stack(t))


def test_stop_is_immediate_and_not_part_of_the_confirmation_flow():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, task, _ = await g.request('take me to the far place', 'GO_TO_PLACE')
        await g.wait_status(task['request_id'], ('executing',))
        st, r, _ = await g.call('POST', '/api/robot/stop')
        assert st == 200 and r['state']['state'] == 'STOPPED'
        t2 = await g.wait_status(task['request_id'], ('failed',))
        assert t2['reason'] == 'execution_failed' and 'Stopped' in t2['message'] and 'reply' not in t2
        await asyncio.sleep(0.4)
        assert comp.sm.state == S.STOPPED
        st, task, _ = await g.request('take me to the front', 'GO_TO_PLACE')                                       # and the guest can continue
        await g.wait_status(task['request_id'], ('completed',))
        st, stop, _ = await g.request('stop', None)                                                                # stop by text also works
        assert stop['action'] == 'STOP_ROBOT' and stop['status'] == 'completed'
    run(with_stack(t))


def test_robot_unavailable_is_reported_not_attempted():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, task, _ = await g.request('take me to the front', 'GO_TO_PLACE')
        check_contract(task)
        assert task['status'] == 'failed' and task['reason'] == 'robot_unavailable' and 'not placed' in task['message'] and gw.robot.pose['x'] == 0
    run(with_stack(t, robot=MockRobot(speed=4.0, localized=False)))


def test_gateway_loss_during_escort_fails_the_request():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, task, _ = await g.request('take me to the far place', 'GO_TO_PLACE')
        await g.wait_status(task['request_id'], ('executing',))
        await gw.close()
        t2 = await g.wait_status(task['request_id'], ('failed',))
        assert t2['reason'] == 'robot_unavailable'
    run(with_stack(t))


def test_end_session_expiry_and_staff():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        st, task, _ = await g.request('take me to the front', 'GO_TO_PLACE')
        await g.wait_status(task['request_id'], ('completed',))
        st, r, _ = await g.call('DELETE', '/api/session')
        assert st == 200 and 'Goodbye' in r['reply']
        assert (await g.call('GET', '/api/state'))[0] == 401 and comp.sm.session is None and not comp.tasks
        h = Guest(port)
        await h.join('Bob')                                                              # a new guest can join right away (the robot is heading home)
        assert (await http(port, 'POST', '/api/admin/end', headers={'X-Admin-Token': 'nope'}))[0] == 403
        assert (await http(port, 'POST', '/api/admin/end', headers={'X-Admin-Token': 'staff'}))[0] == 200
        assert (await h.call('GET', '/api/state'))[0] == 401
        k = Guest(port)
        await k.join('Eve')
        for _ in range(60):                                                              # session TTL: the token stops working by itself
            if comp.sm.session is None:
                break
            await asyncio.sleep(0.1)
        assert comp.sm.session is None and (await k.call('GET', '/api/state'))[0] == 401
    run(with_stack(t, session_ttl_s=1.0))


def test_hostile_and_malformed_input():
    async def t(port, comp, gw):
        g = Guest(port)
        await g.join()
        cases = [
            ('POST', '/api/requests', {'text': 'x' * 2001, 'source': 'typed', 'proposed_action': None, 'client_request_id': 'a1'}, {}, 400),
            ('POST', '/api/requests', {'text': 'hi', 'source': 'voice', 'proposed_action': None, 'client_request_id': 'a2'}, {}, 400),
            ('POST', '/api/requests', {'text': 'hi', 'source': 'typed', 'proposed_action': 'DAMP', 'client_request_id': 'a3'}, {}, 400),
            ('POST', '/api/requests', {'text': 'hi', 'source': 'typed', 'proposed_action': None, 'client_request_id': 'bad id!'}, {}, 400),
            ('POST', '/api/requests', {'text': '  ', 'source': 'typed', 'proposed_action': None, 'client_request_id': 'a4'}, {}, 400),
            ('POST', '/api/requests', {'text': 'hi', 'source': 'typed', 'proposed_action': None, 'client_request_id': 'a5'}, {'Idempotency-Key': 'other'}, 400),
            ('POST', '/api/requests', {'text': 'hi', 'source': 'typed', 'proposed_action': None, 'client_request_id': 'a6'}, {'Origin': 'https://evil.example'}, 403),
            ('GET', '/api/requests/nope', None, {}, 404), ('GET', '/api/nothing', None, {}, 404), ('PUT', '/api/requests', {}, {}, 404),
        ]
        for method, path, body, hdr, want in cases:
            st, _, _ = await g.call(method, path, body, headers=hdr)
            assert st == want, (method, path, body, st)
        for raw in (b'not json', b'[]', b'{"a":'):
            assert (await g.call('POST', '/api/requests', raw_body=raw))[0] == 400
        assert (await g.call('POST', '/api/requests', raw_body=b'x' * 20000))[0] == 413
        st, _, _ = await http(port, 'POST', '/api/requests', raw_body=b'{}', headers={**g.auth, 'Transfer-Encoding': 'chunked'})
        assert st == 400
        r, w = await asyncio.open_connection('127.0.0.1', port)                                # garbage on the socket must not hurt the server
        w.write(b'\x00\x01 GARBAGE\r\n\r\n')
        await w.drain()
        await asyncio.wait_for(r.read(), 5)
        w.close()
        assert (await g.call('GET', '/api/state'))[0] == 200 and comp.sm.state == S.AWAITING_INPUT
        st, page, hd = await http(port, 'GET', '/')
        assert st == 200 and b'<canvas' in page and 'default-src' in hd['content-security-policy'] and hd['cache-control'] == 'no-store'
        assert 'access-control-allow-origin' not in hd                                                          # no CORS: same-origin proxy only
    run(with_stack(t))
