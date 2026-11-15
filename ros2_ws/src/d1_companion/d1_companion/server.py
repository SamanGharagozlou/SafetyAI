"""Companion server: guest phone <-> state machine <-> robot gateway.

  phone  --WebSocket JSON-->  this server  --WebSocket JSON-->  d1_gateway  --ROS 2-->  Nav2 / bridge
  phone  <--HTTP-- phone.html, /map.png (needs a live session)

Guest messages (after `join`):  select {destination} | say {text} | stop | end | ping        (first message: join {token, nickname, lang} or resume {session})
Server messages:  joined {session, places, map, robot_name} | join_rejected {reason} | state {...snapshot} | say {text} | session_ended {reason} | error {message}
Staff:  admin {token, action: end_session | stop}  (only when --admin-token is set)

Security: binds 127.0.0.1 by default; joining needs the token that is in the QR code URL (?t=...); the gateway is only ever sent goals / stop / named
intents. Expose it with Tailscale Serve (HTTPS), never Funnel. No audio, images or guest text is stored; a session lives in memory only.
"""
import argparse
import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from http import HTTPStatus

from d1_companion import places as P
from d1_companion.gateway_client import GatewayClient
from d1_companion.statemachine import Companion, Config, S, ev

log = logging.getLogger('d1_companion')
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
MAX_MSG = 4096


class CompanionServer:
    def __init__(self, places, config=None, gateway_url='ws://127.0.0.1:8765', gateway_token='', join_token=None, admin_token='',
                 host='127.0.0.1', port=8780, map_info=None, robot_name='D1', robot_speech=False, clock=time.monotonic, nlu=None, answerer=None):
        self.sm = Companion(places, config, clock=clock, nlu=nlu, answerer=answerer)
        self.places, self.map_info, self.robot_name = places, map_info, robot_name
        self.join_token = join_token or secrets.token_urlsafe(9)
        self.admin_token, self.host, self.port, self.robot_speech = admin_token, host, port, robot_speech
        self.gw = GatewayClient(gateway_url, gateway_token, on_message=self._on_gateway, on_up=self._gw_up, on_down=self._gw_down)
        self.conns = {}                  # websocket -> session id (None until joined)
        self._server, self._tick_task = None, None
        self._last_snap = None

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        import websockets
        self.gw.start()
        self._server = await websockets.serve(self._handler, self.host, self.port, process_request=self._http, max_size=MAX_MSG,
                                              ping_interval=20, ping_timeout=20)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        self._tick_task = asyncio.ensure_future(self._ticker())
        log.info('companion on http://%s:%s  (join URL needs ?t=<token>)', self.host, self.port)

    async def stop(self):
        if self._tick_task:
            self._tick_task.cancel()
        await self.gw.stop()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    # ------------------------------------------------------------------ HTTP (same port as the WebSocket)
    async def _http(self, path, headers):
        if (headers.get('Upgrade') or '').lower() == 'websocket':
            return None
        route, _, query = path.partition('?')
        h = [('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
             ('Content-Security-Policy', CSP)]
        files = {'/': ('phone.html', 'text/html; charset=utf-8'), '/phone.js': ('phone.js', 'text/javascript; charset=utf-8'),
                 '/phone.css': ('phone.css', 'text/css; charset=utf-8')}
        if route in files:
            name, ctype = files[route]
            with open(os.path.join(STATIC, name), 'rb') as f:
                return HTTPStatus.OK, h + [('Content-Type', ctype)], f.read()
        if route == '/map.png' and self.map_info:
            sid = dict(kv.split('=', 1) for kv in query.split('&') if '=' in kv).get('s', '')
            if self.sm.session and hmac.compare_digest(sid, self.sm.session['id']):
                return HTTPStatus.OK, h + [('Content-Type', 'image/png')], self.map_info['png']
            return HTTPStatus.FORBIDDEN, h, b'join first'
        if route == '/healthz':
            return HTTPStatus.OK, h + [('Content-Type', 'application/json')], json.dumps(
                {'ok': True, 'state': self.sm.state.value, 'gateway': self.gw.up}).encode()
        return HTTPStatus.NOT_FOUND, h, b'not found'

    # ------------------------------------------------------------------ guests
    async def _send(self, ws, msg):
        try:
            await ws.send(json.dumps(msg))
        except Exception:  # noqa: BLE001
            self.conns.pop(ws, None)

    async def _to_guests(self, msg, sid=None):
        sid = sid or (self.sm.session['id'] if self.sm.session else None)
        for ws, s in list(self.conns.items()):
            if s is not None and s == sid:
                await self._send(ws, msg)

    async def _handler(self, ws, *_):
        self.conns[ws] = None
        try:
            tmo, stamps = 15.0, []
            while True:
                raw = await asyncio.wait_for(ws.recv(), tmo if self.conns.get(ws) is None else None)
                now = time.monotonic()
                stamps = [t for t in stamps if now - t < 1.0] + [now]
                if len(stamps) > 12:
                    await self._send(ws, {'type': 'error', 'message': 'too many messages'})
                    continue
                try:
                    m = json.loads(raw)
                    assert isinstance(m, dict) and isinstance(m.get('type'), str)
                except (ValueError, AssertionError):
                    await self._send(ws, {'type': 'error', 'message': 'invalid message'})
                    continue
                await self._guest_message(ws, m)
        except asyncio.TimeoutError:
            await ws.close(4408, 'join first')
        except Exception:  # noqa: BLE001  (connection closed)
            pass
        finally:
            self.conns.pop(ws, None)

    async def _guest_message(self, ws, m):
        t = m['type']
        if t == 'ping':
            return await self._send(ws, {'type': 'pong'})
        if t == 'admin':
            return await self._admin(ws, m)
        if t in ('join', 'resume'):
            return await self._join(ws, m)
        sid = self.conns.get(ws)
        if sid is None or not self.sm.session or sid != self.sm.session['id']:
            return await self._send(ws, {'type': 'error', 'message': 'join first'})
        if t == 'select':
            dest = m.get('destination')
            if not isinstance(dest, str):
                return await self._send(ws, {'type': 'error', 'message': 'destination must be a string'})
            await self._feed(ev('select', dest=dest))
        elif t == 'say':
            text = m.get('text')
            if not isinstance(text, str) or len(text) > 500:
                return await self._send(ws, {'type': 'error', 'message': 'text must be a string up to 500 characters'})
            await self._feed(ev('say', text=text))
        elif t == 'stop':
            await self._feed(ev('stop'))
        elif t == 'end':
            await self._feed(ev('end'))
        else:
            await self._send(ws, {'type': 'error', 'message': f'unknown type {t!r}'})

    async def _join(self, ws, m):
        if m['type'] == 'resume':
            if self.sm.session and isinstance(m.get('session'), str) and hmac.compare_digest(m['session'], self.sm.session['id']):
                self.conns[ws] = self.sm.session['id']
                await self._send(ws, self._joined())
                return await self._send(ws, {'type': 'state', **self.sm.snapshot()})
            return await self._send(ws, {'type': 'join_rejected', 'reason': 'session expired'})
        tok = m.get('token')
        if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.join_token.encode()):
            return await self._send(ws, {'type': 'join_rejected', 'reason': 'This QR code is not valid (any more). Please scan the code on the robot again.'})
        nick = m.get('nickname')
        nick = nick.strip()[:24] if isinstance(nick, str) and nick.strip() else 'Guest'
        lang = m.get('lang') if isinstance(m.get('lang'), str) else 'en'
        effects = self.sm.handle(ev('join', nickname=nick, lang=lang[:8]))
        rejected = [e for e in effects if e.kind == 'join_rejected']
        if rejected:
            return await self._send(ws, {'type': 'join_rejected', 'reason': rejected[0].data['reason']})
        self.conns[ws] = self.sm.session['id']
        await self._send(ws, self._joined())
        await self._run_effects(effects)
        await self._publish(force=True)

    def _joined(self):
        mi = self.map_info
        return {'type': 'joined', 'session': self.sm.session['id'], 'robot_name': self.robot_name,
                'places': [{'name': n, 'x': p['x'], 'y': p['y'], 'yaw': p['yaw'], 'label': p['label'], 'description': p['description'],
                            'home': n == self.sm.cfg.home} for n, p in self.places.items()],
                'map': None if not mi else {'url': '/map.png?s=' + self.sm.session['id'], 'w': mi['w'], 'h': mi['h'], 'res': mi['res'],
                                            'x0': mi['x0'], 'y0': mi['y0']}}

    async def _admin(self, ws, m):
        tok = m.get('token')
        if not self.admin_token or not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.admin_token.encode()):
            return await self._send(ws, {'type': 'error', 'message': 'not allowed'})
        if m.get('action') == 'end_session':
            await self._feed(ev('end'))
        elif m.get('action') == 'stop':
            await self._feed(ev('stop'))
        await self._send(ws, {'type': 'ok'})

    # ------------------------------------------------------------------ state machine <-> world
    async def _feed(self, event):
        await self._run_effects(self.sm.handle(event))
        await self._publish()

    async def _run_effects(self, effects):
        for e in effects or []:
            d = e.data
            if e.kind == 'send_goal':
                ok = await self.gw.send({'type': 'goal', 'id': d['id'], 'place': d['place']})
                if not ok:
                    await self._feed_noeffects(ev('goal_rejected', id=d['id'], reason='the robot control link is down'))
            elif e.kind == 'stop_robot':
                await self.gw.send({'type': 'stop'})
            elif e.kind == 'intent':
                ok = await self.gw.send({'type': 'intent', 'id': d['id'], 'name': d['name'], 'args': d.get('args') or {}})
                if not ok:
                    await self._feed_noeffects(ev('intent_failed', id=d['id'], reason='the robot control link is down'))
            elif e.kind == 'say':
                await self._to_guests({'type': 'say', 'text': d['text'], 'from': 'robot'}, d.get('session'))
                if self.robot_speech:
                    await self.gw.send({'type': 'intent', 'id': 'say-' + secrets.token_hex(3), 'name': 'say', 'args': {'text': d['text']}})
            elif e.kind == 'session_closed':
                await self._to_guests({'type': 'session_ended', 'reason': d.get('reason', '')}, d.get('session'))
                for ws, s in list(self.conns.items()):
                    if s == d.get('session'):
                        self.conns[ws] = None
            elif e.kind == 'log':
                log.info(d.get('text', ''))

    async def _feed_noeffects(self, event):
        await self._run_effects(self.sm.handle(event))

    async def _publish(self, force=False):
        snap = self.sm.snapshot()
        key = json.dumps(snap, sort_keys=True)
        if not force and key == self._last_snap:
            return
        self._last_snap = key
        await self._to_guests({'type': 'state', **snap})

    async def _ticker(self):
        while True:
            await asyncio.sleep(0.25)
            effects = self.sm.tick()
            if effects:
                await self._run_effects(effects)
            await self._publish()

    # ------------------------------------------------------------------ gateway
    async def _gw_up(self):
        await self._feed(ev('gateway_up'))

    async def _gw_down(self):
        await self._feed(ev('gateway_down'))

    async def _on_gateway(self, m):
        t = m.get('type')
        gid = m.get('id')
        if t in ('goal_received', 'goal_accepted', 'goal_reached'):
            await self._feed(ev(t, id=gid))
        elif t == 'goal_progress':
            await self._feed(ev(t, id=gid, distance=m.get('distance_remaining')))
        elif t in ('goal_failed', 'goal_canceled', 'goal_rejected', 'intent_failed'):
            await self._feed(ev(t, id=gid, reason=m.get('reason')))
        elif t == 'intent_done':
            await self._feed(ev(t, id=gid))
        elif t == 'state':
            await self._feed(ev('robot_state', state={k: v for k, v in m.items() if k not in ('type', 'ts')}))


def main(argv=None):
    ap = argparse.ArgumentParser(description='D1 companion: guest phone <-> state machine <-> robot gateway')
    ap.add_argument('--places', required=True, help='places.yaml (same file as the gateway)')
    ap.add_argument('--map', default='', help='map.yaml to draw on the phone (optional)')
    ap.add_argument('--gateway', default='ws://127.0.0.1:8765')
    ap.add_argument('--gateway-token', default=os.environ.get('D1_GATEWAY_TOKEN', ''))
    ap.add_argument('--host', default='127.0.0.1', help='keep 127.0.0.1 and publish with Tailscale Serve')
    ap.add_argument('--port', type=int, default=8780)
    ap.add_argument('--join-token', default=os.environ.get('D1_COMPANION_JOIN_TOKEN', ''), help='secret in the QR code URL (random if empty)')
    ap.add_argument('--admin-token', default=os.environ.get('D1_COMPANION_ADMIN_TOKEN', ''))
    ap.add_argument('--robot-name', default='D1')
    ap.add_argument('--robot-speech', default='false', help='also send what the robot says to the gateway /say intent')
    ap.add_argument('--home', default='home')
    ap.add_argument('--min-battery', type=float, default=0.20)
    ap.add_argument('--input-prompt-s', type=float, default=60.0)
    ap.add_argument('--input-timeout-s', type=float, default=120.0)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    places = P.load_places(a.places)
    cfg = Config(home=a.home, min_battery=a.min_battery, input_prompt_s=a.input_prompt_s, input_timeout_s=a.input_timeout_s)
    srv = CompanionServer(places, cfg, a.gateway, a.gateway_token, a.join_token or None, a.admin_token, a.host, a.port,
                          P.load_map(a.map) if a.map else None, a.robot_name, str(a.robot_speech).lower() in ('1', 'true', 'yes'))

    async def run():
        await srv.start()
        print(f'join URL (put it in the QR code): http://{a.host}:{srv.port}/?t={srv.join_token}', flush=True)
        await asyncio.Event().wait()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
