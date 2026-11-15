"""Companion server: HTTP API for the visitor frontend  <->  state machine  <->  robot gateway.

  visitor frontend --HTTP/JSON (Bearer)--> this server --WebSocket--> d1_gateway --ROS 2--> Nav2 / bridge

Follows the contract the frontend prepared in frontend/API_INTEGRATION.md (backend-issued credential, POST /api/requests with an
Idempotency-Key, GET /api/requests/{id}, statuses queued/processing/awaiting_approval/executing/completed/denied/failed) and adds
what that contract lacks for escorting (see docs/companion_api.md; the additions need the frontend's agreement):

  POST   /api/session           {token (from the QR code), nickname?}  -> {accessToken, expiresAt, ...}     (409 when another guest is paired)
  DELETE /api/session                                                  end the session; the robot returns home
  GET    /api/places                                                   layout of the destinations (NO map image: guests never see the floor plan)
  GET    /api/state[?after=N]                                          live state: where the robot is, progress, new robot messages
  POST   /api/requests          {text, source, proposed_action, client_request_id, place?}  -> task      (confirmed by the guest)
  GET    /api/requests/{id}                                            task status; `completed` = arrived (for GO_TO_PLACE)
  POST   /api/robot/stop                                               immediate stop, never behind a confirmation step
  POST   /api/admin/end         header X-Admin-Token                   staff ends the session
  GET    /healthz                                                      health (no secrets)

Security: binds 127.0.0.1 by default (publish with Tailscale Serve, never Funnel); the QR token proves presence at the robot; every guest call needs the
Bearer credential (no cookies, no CORS: the frontend is expected to reach /api through a same-origin proxy, as its CSP demands); the gateway only gets
goals / stop / named intents; the guest's text is never stored beyond the session; one guest session per robot.
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from http import HTTPStatus

from d1_companion import nlu as NLU
from d1_companion import places as P
from d1_companion.gateway_client import GatewayClient
from d1_companion.statemachine import Companion, Config, S, ev

log = logging.getLogger('d1_companion')
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
FRONTEND_ACTIONS = ('GREET', 'ROBOT_INFO', 'HELP')                       # already in the frontend's vocabulary
EXTRA_ACTIONS = ('GO_TO_PLACE', 'STOP_ROBOT', 'END_SESSION')              # proposed additions (need agreement)
RID = re.compile(r'^[a-zA-Z0-9-]{1,100}$')
MAX_BODY, MAX_TEXT = 16384, 2000
TERMINAL = ('completed', 'denied', 'failed')
GREETING = ('hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening', 'hallo', 'guten tag')
INFO = ('who are you', 'what are you', 'your name', 'about you', 'what can you do')
HELP = ('help', 'where', 'places', 'destinations', 'options', 'show', 'what is here', 'what can i')


class HttpError(Exception):
    def __init__(self, status, code, message=''):
        super().__init__(code)
        self.status, self.code, self.message = status, code, message


class Task:
    def __init__(self, request_id, client_id, digest, action, kind):
        self.request_id, self.client_id, self.digest, self.action, self.kind = request_id, client_id, digest, action, kind   # kind: nav | instant
        self.status, self.revision, self.reply, self.reason, self.message = 'queued', 0, None, None, None
        self.dest, self.tag = None, request_id

    def set(self, status, reply=None, reason=None, message=None):
        if (status, reply, reason, message) != (self.status, self.reply, self.reason, self.message):
            self.status, self.reply, self.reason, self.message = status, reply, reason, message
            self.revision += 1

    def done(self):
        return self.status in TERMINAL


class CompanionServer:
    def __init__(self, places, config=None, gateway_url='ws://127.0.0.1:8765', gateway_token='', join_token=None, admin_token='',
                 host='127.0.0.1', port=8780, robot_name='D1', robot_speech=False, session_ttl_s=600.0, clock=time.monotonic, wall=time.time,
                 nlu=None, answerer=None):
        self.sm = Companion(places, config, clock=clock, nlu=nlu, answerer=answerer)
        self.places, self.robot_name, self.wall = places, robot_name, wall
        self.nlu = nlu or NLU.parse
        self.join_token = join_token or secrets.token_urlsafe(9)
        self.admin_token, self.host, self.port, self.robot_speech, self.ttl = admin_token, host, port, robot_speech, session_ttl_s
        self.gw = GatewayClient(gateway_url, gateway_token, on_message=self._on_gateway, on_up=self._gw_up, on_down=self._gw_down)
        self.expires_at = 0.0
        self.outbox, self.seq = deque(maxlen=60), 0           # robot lines for the guest (cleared with the session)
        self.tasks, self.by_client = {}, {}                   # request_id -> Task ; client_request_id -> request_id (this session only)
        self.hits = deque()                                    # request timestamps for rate limiting
        self._server, self._tick_task = None, None

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        self.gw.start()
        self._server = await asyncio.start_server(self._serve, self.host, self.port, limit=1 << 16)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        self._tick_task = asyncio.ensure_future(self._ticker())
        log.info('companion on http://%s:%s', self.host, self.port)

    async def stop(self):
        if self._tick_task:
            self._tick_task.cancel()
        await self.gw.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------ minimal HTTP/1.1 (one request per connection)
    async def _serve(self, reader, writer):
        try:
            status, ctype, body = await self._handle(reader)
        except HttpError as e:
            status, ctype, body = e.status, 'application/json', json.dumps({'error': e.code, 'message': e.message}).encode()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        except Exception:  # noqa: BLE001
            log.exception('request failed')
            status, ctype, body = 500, 'application/json', b'{"error":"internal_error"}'
        head = (f'HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n'
                f'Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: {CSP}\r\n\r\n')
        try:
            writer.write(head.encode() + body)
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def _handle(self, reader):
        line = await asyncio.wait_for(reader.readline(), 10)
        if not line or len(line) > 2048:
            raise HttpError(400, 'bad_request')
        try:
            method, target, ver = line.decode('ascii').strip().split(' ')
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, 'bad_request')
        if not ver.startswith('HTTP/1.'):
            raise HttpError(400, 'bad_request')
        headers, total = {}, 0
        while True:
            h = await asyncio.wait_for(reader.readline(), 10)
            total += len(h)
            if total > 8192:
                raise HttpError(431, 'headers_too_large')
            if h in (b'\r\n', b'\n', b''):
                break
            k, _, v = h.decode('latin-1').partition(':')
            headers[k.strip().lower()] = v.strip()
        if 'transfer-encoding' in headers:
            raise HttpError(400, 'bad_request', 'chunked bodies are not supported')
        cl = headers.get('content-length', '0')
        if not cl.isdigit():
            raise HttpError(400, 'bad_request')
        if int(cl) > MAX_BODY:
            raise HttpError(413, 'body_too_large')
        body = await asyncio.wait_for(reader.readexactly(int(cl)), 10) if int(cl) else b''
        path, _, query = target.partition('?')
        if method not in ('GET', 'HEAD') and headers.get('origin'):        # cross-origin writes are never allowed (same-origin proxy only)
            host = headers.get('host', '')
            if re.sub(r'^https?://', '', headers['origin']) != host:
                raise HttpError(403, 'cross_origin')
        return await self._route(method, path, query, headers, body)

    # ------------------------------------------------------------------ routing
    def _json(self, obj, status=200):
        return status, 'application/json', json.dumps(obj).encode()

    def _auth(self, headers):
        auth = headers.get('authorization', '')
        tok = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
        sess = self.sm.session
        if not sess or not tok or not hmac.compare_digest(tok.encode(), sess['id'].encode()):
            raise HttpError(401, 'session_expired')
        return sess

    def _body(self, body):
        try:
            d = json.loads(body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, 'invalid_request', 'body must be JSON')
        if not isinstance(d, dict):
            raise HttpError(400, 'invalid_request', 'body must be a JSON object')
        return d

    async def _route(self, method, path, query, headers, body):
        if method == 'GET' and path == '/healthz':
            return self._json({'ok': True, 'state': self.sm.state.value, 'gateway': self.gw.up})
        if path == '/api/session' and method == 'POST':
            return await self._create_session(headers, self._body(body))
        if path == '/api/session' and method == 'DELETE':
            self._auth(headers)
            reply = await self._end('Goodbye! It was nice to walk with you.')
            return self._json({'ok': True, 'reply': reply})
        if path == '/api/admin/end' and method == 'POST':
            tok = headers.get('x-admin-token', '')
            if not self.admin_token or not hmac.compare_digest(tok.encode(), self.admin_token.encode()):
                raise HttpError(403, 'forbidden')
            await self._end('The session was ended by staff.')
            return self._json({'ok': True})
        if path.startswith('/api/'):
            sess = self._auth(headers)
            self._rate_limit()
            if path == '/api/places' and method == 'GET':
                return self._json({'places': self._places()})
            if path == '/api/state' and method == 'GET':
                after = dict(kv.split('=', 1) for kv in query.split('&') if '=' in kv).get('after', '0')
                return self._json(self._state(int(after) if after.isdigit() else 0))
            if path == '/api/robot/stop' and method == 'POST':
                await self._feed(ev('stop'))
                return self._json({'ok': True, 'state': self._public_state()})
            if path == '/api/requests' and method == 'POST':
                return await self._create_request(headers, self._body(body))
            m = re.match(r'^/api/requests/([a-zA-Z0-9_-]{1,100})$', path)
            if m and method == 'GET':
                t = self.tasks.get(m.group(1))
                if t is None:
                    raise HttpError(404, 'not_found')
                return self._json(self._task_json(t))
        raise HttpError(404, 'not_found')

    def _rate_limit(self):
        now = time.monotonic()
        while self.hits and now - self.hits[0] > 10:
            self.hits.popleft()
        if len(self.hits) >= 60:
            raise HttpError(429, 'rate_limited')
        self.hits.append(now)

    # ------------------------------------------------------------------ session
    async def _create_session(self, headers, d):
        if self.sm.session and headers.get('authorization'):          # the same guest comes back (page reload): resume
            try:
                sess = self._auth(headers)
                return self._json(self._session_json(sess))
            except HttpError:
                pass
        tok = d.get('token')
        if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.join_token.encode()):
            raise HttpError(403, 'invalid_qr', 'This QR code is not valid (any more). Please scan the code on the robot again.')
        nick = d.get('nickname')
        nick = nick.strip()[:24] if isinstance(nick, str) and nick.strip() else 'Guest'
        lang = d.get('lang') if isinstance(d.get('lang'), str) else 'en'
        effects = self.sm.handle(ev('join', nickname=nick, lang=lang[:8], session=secrets.token_urlsafe(24)))
        rej = [e for e in effects if e.kind == 'join_rejected']
        if rej:
            raise HttpError(409, 'robot_unavailable' if 'not available' in rej[0].data['reason'] else 'robot_busy', rej[0].data['reason'])
        self.expires_at = self.wall() + self.ttl
        self.outbox.clear()
        self.tasks.clear()
        self.by_client.clear()
        await self._run_effects(effects)
        return self._json(self._session_json(self.sm.session), 201)

    def _session_json(self, sess):
        return {'accessToken': sess['id'], 'expiresAt': int(self.expires_at * 1000), 'robot': {'name': self.robot_name},
                'nickname': sess['nickname'], 'places': self._places()}

    async def _end(self, text):
        await self._feed(ev('end', reason=text))
        self._reconcile()
        return text

    def _places(self):
        """Layout only: names, labels and coordinates. The map itself is never served."""
        return [{'name': n, 'label': p['label'], 'description': p['description'], 'x': p['x'], 'y': p['y'], 'home': n == self.sm.cfg.home}
                for n, p in self.places.items()]

    def _public_state(self):
        s = self.sm.snapshot()
        r = s['robot']
        pose = r.get('pose') or {}
        avail = bool(r.get('gateway_up')) and r.get('connected') is not False and r.get('mode') != 'damping'
        return {'state': s['state'], 'goal_status': s['goal_status'], 'destination': s['destination'], 'at': s['at'],
                'distance_remaining': s['distance_remaining'], 'reason': s['reason'], 'options': s['options'],
                'robot': {'x': pose.get('x'), 'y': pose.get('y'), 'yaw': pose.get('yaw'), 'available': avail}}

    def _state(self, after):
        return {'seq': self.seq, 'messages': [m for m in self.outbox if m['seq'] > after], 'expiresAt': int(self.expires_at * 1000),
                **self._public_state()}

    # ------------------------------------------------------------------ requests (the frontend's task model)
    def _interpret(self, text, proposed, place):
        """-> (action, dest|None). The text decides; proposed_action / place are only hints (the backend validates everything)."""
        kind, dest = self.nlu(text, self.places)
        explicit = place if isinstance(place, str) and place in self.places and proposed == 'GO_TO_PLACE' else None
        if kind in ('select', 'home'):
            dest = dest or self.sm.cfg.home
            if explicit and explicit != dest:
                return None, None                                     # text and tapped place disagree: do not guess
            return 'GO_TO_PLACE', dest
        if kind == 'retry':
            return ('GO_TO_PLACE', self.sm.last_dest) if self.sm.last_dest else ('HELP', None)
        if kind == 'stop':
            return 'STOP_ROBOT', None
        if kind == 'end':
            return 'END_SESSION', None
        if explicit:
            return 'GO_TO_PLACE', explicit
        t = NLU._norm(text)
        if any(NLU._has(t, w) for w in INFO):
            return 'ROBOT_INFO', None
        if any(NLU._has(t, w) for w in GREETING):
            return 'GREET', None
        if any(NLU._has(t, w) for w in HELP):
            return 'HELP', None
        return (proposed if proposed in FRONTEND_ACTIONS else None), None

    async def _create_request(self, headers, d):
        text, source, proposed, cid, place = d.get('text'), d.get('source'), d.get('proposed_action'), d.get('client_request_id'), d.get('place')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_TEXT or source not in ('typed', 'transcript') \
                or not isinstance(cid, str) or not RID.match(cid) or (proposed is not None and proposed not in FRONTEND_ACTIONS + EXTRA_ACTIONS) \
                or (place is not None and not isinstance(place, str)) or headers.get('idempotency-key', cid) != cid:
            raise HttpError(400, 'invalid_request')
        text = text.strip()
        digest = hashlib.sha256(json.dumps([text, source, proposed, cid, place], sort_keys=False).encode()).hexdigest()
        rid = self.by_client.get(cid)
        if rid is not None:
            t = self.tasks[rid]
            if t.digest != digest:
                raise HttpError(409, 'conflict')
            return self._json(self._task_json(t))                       # idempotent replay: same answer, no second robot command
        action, dest = self._interpret(text, proposed, place)
        rid = 'req-' + secrets.token_hex(8)
        t = Task(rid, cid, digest, action, 'nav' if action == 'GO_TO_PLACE' else 'instant')
        self.tasks[rid], self.by_client[cid] = t, rid
        nick = self.sm.session['nickname']
        if action is None:
            t.set('denied', reason='unsupported_request', message=self.sm.answerer(text))
        elif action == 'GO_TO_PLACE':
            t.dest = dest
            t.set('processing')
            await self._feed(ev('select', dest=dest, tag=t.tag))
        elif action == 'STOP_ROBOT':
            await self._feed(ev('stop'))
            t.set('completed', reply='Okay, I have stopped. Tell me where you would like to go.')
        elif action == 'END_SESSION':
            t.set('completed', reply=await self._end('Goodbye! It was nice to walk with you.'))
        elif action == 'GREET':
            t.set('completed', reply=f"Hello {nick}! I'm your robot companion. Where would you like to go?")
        elif action == 'ROBOT_INFO':
            t.set('completed', reply="I'm a four-legged robot companion. I can walk you to places in this area. Where would you like to go?")
        else:                                                              # HELP
            t.set('completed', reply=self.sm.answerer(text))
        self._reconcile()
        return self._json(self._task_json(t), 201)

    def _reconcile(self):
        """Bring every navigation task in line with what the state machine reports for its tag."""
        for t in self.tasks.values():
            if t.kind != 'nav' or t.done():
                continue
            o = self.sm.outcomes.get(t.tag)
            if o:
                k, msg, why = o['kind'], o['message'], o['reason']
                if k in ('reached', 'already_there'):
                    t.set('completed', reply=msg)
                elif k == 'refused' and why == 'unsupported_request':
                    t.set('denied', reason='unsupported_request', message=msg)
                else:
                    t.set('failed', reason=why if why in ('execution_failed', 'robot_unavailable') else 'execution_failed', message=msg)
            elif self.sm.tag == t.tag:
                t.set('executing' if self.sm.goal_status == 'moving' else 'processing')
            elif self.sm.session is None:
                t.set('failed', reason='execution_failed', message='The session ended.')

    def _task_json(self, t):
        o = {'request_id': t.request_id, 'client_request_id': t.client_id, 'status': t.status, 'action': t.action, 'revision': t.revision, 'demo': False}
        if t.status == 'completed':
            o['reply'] = t.reply
        if t.reason:
            o['reason'] = t.reason
        if t.message and t.status != 'completed':
            o['message'] = t.message                                       # extension: the frontend's decoder forbids `reply` unless completed
        if t.kind == 'nav' and t.dest:
            o['place'] = t.dest
            if t.status in ('processing', 'executing') and self.sm.tag == t.tag:
                o['progress'] = {'goal_status': self.sm.goal_status, 'distance_remaining': self.sm.distance}
        return o

    # ------------------------------------------------------------------ state machine <-> world
    async def _feed(self, event):
        await self._run_effects(self.sm.handle(event))
        self._reconcile()

    async def _run_effects(self, effects):
        for e in effects or []:
            d = e.data
            if e.kind == 'send_goal':
                if not await self.gw.send({'type': 'goal', 'id': d['id'], 'place': d['place']}):
                    await self._feed_late(ev('goal_rejected', id=d['id'], reason='the robot control link is down'))
            elif e.kind == 'stop_robot':
                await self.gw.send({'type': 'stop'})
            elif e.kind == 'intent':
                if not await self.gw.send({'type': 'intent', 'id': d['id'], 'name': d['name'], 'args': d.get('args') or {}}):
                    await self._feed_late(ev('intent_failed', id=d['id'], reason='the robot control link is down'))
            elif e.kind == 'say':
                if self.sm.session:
                    self.seq += 1
                    self.outbox.append({'seq': self.seq, 'text': d['text']})
                if self.robot_speech:
                    await self.gw.send({'type': 'intent', 'id': 'say-' + secrets.token_hex(3), 'name': 'say', 'args': {'text': d['text']}})
            elif e.kind == 'session_closed':                          # nothing about the guest is kept after the session
                self.outbox.clear()
                self.tasks.clear()
                self.by_client.clear()
                self.expires_at = 0.0
            elif e.kind == 'log':
                log.info(d.get('text', ''))

    async def _feed_late(self, event):
        await self._run_effects(self.sm.handle(event))

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.25)
            if self.sm.session and self.wall() >= self.expires_at:
                await self._feed(ev('end', reason='Your session has expired. Goodbye!'))
            effects = self.sm.tick()
            if effects:
                await self._run_effects(effects)
            self._reconcile()

    # ------------------------------------------------------------------ gateway
    async def _gw_up(self):
        await self._feed(ev('gateway_up'))

    async def _gw_down(self):
        await self._feed(ev('gateway_down'))

    async def _on_gateway(self, m):
        t, gid = m.get('type'), m.get('id')
        if t in ('goal_received', 'goal_accepted', 'goal_reached', 'intent_done'):
            await self._feed(ev(t, id=gid))
        elif t == 'goal_progress':
            await self._feed(ev(t, id=gid, distance=m.get('distance_remaining')))
        elif t in ('goal_failed', 'goal_canceled', 'goal_rejected', 'intent_failed'):
            await self._feed(ev(t, id=gid, reason=m.get('reason')))
        elif t == 'state':
            await self._feed(ev('robot_state', state={k: v for k, v in m.items() if k not in ('type', 'ts')}))


def main(argv=None):
    ap = argparse.ArgumentParser(description='D1 companion: visitor frontend <-> state machine <-> robot gateway')
    ap.add_argument('--places', required=True, help='places.yaml (same file as the gateway)')
    ap.add_argument('--gateway', default='ws://127.0.0.1:8765')
    ap.add_argument('--gateway-token', default=os.environ.get('D1_GATEWAY_TOKEN', ''))
    ap.add_argument('--host', default='127.0.0.1', help='keep 127.0.0.1 and publish with Tailscale Serve')
    ap.add_argument('--port', type=int, default=8780)
    ap.add_argument('--join-token', default=os.environ.get('D1_COMPANION_JOIN_TOKEN', ''), help='secret in the QR code (random if empty)')
    ap.add_argument('--admin-token', default=os.environ.get('D1_COMPANION_ADMIN_TOKEN', ''))
    ap.add_argument('--robot-name', default='D1')
    ap.add_argument('--robot-speech', default='false', help='also send what the robot says to the gateway /say intent')
    ap.add_argument('--home', default='home')
    ap.add_argument('--min-battery', type=float, default=0.20)
    ap.add_argument('--session-ttl-s', type=float, default=600.0)
    ap.add_argument('--input-prompt-s', type=float, default=60.0)
    ap.add_argument('--input-timeout-s', type=float, default=120.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    cfg = Config(home=a.home, min_battery=a.min_battery, input_prompt_s=a.input_prompt_s, input_timeout_s=a.input_timeout_s)
    srv = CompanionServer(P.load_places(a.places), cfg, a.gateway, a.gateway_token, a.join_token or None, a.admin_token, a.host, a.port,
                          a.robot_name, str(a.robot_speech).lower() in ('1', 'true', 'yes'), a.session_ttl_s)

    async def run():
        await srv.start()
        print(f'QR code: <frontend>/join?t={srv.join_token}   (the visitor frontend in robot mode reads t and calls /api/session)', flush=True)
        await asyncio.Event().wait()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
