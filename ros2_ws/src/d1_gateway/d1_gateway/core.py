"""Transport + dispatch of the gateway: WebSocket server, client registry, command routing. No ROS imports.

The robot side is a `RobotPort` (ros_robot.RosRobot on the robot, mock_robot.MockRobot for frontend development):

    start(emit)                      emit(dict) may be called from any thread; it broadcasts to every client
    navigate(goal_id, pose)          pose = {"x","y","yaw","frame"}; answers via emit(goal_accepted|goal_rejected|goal_progress|goal_reached|goal_failed|goal_canceled)
    cancel_goal(goal_id, reason)
    intent(intent_id, name, args)    answers via emit(intent_started|intent_done|intent_failed)
    stop()                           stop moving now (cancel goal, zero velocity), keep standing
    estop()                          stop and damp
    state() -> dict                  snapshot for the periodic `state` message
"""
import asyncio
import json
import logging

from d1_gateway import protocol as P

log = logging.getLogger('d1_gateway')


class GatewayCore:
    def __init__(self, robot, places=None, token='', state_hz=2.0, host='127.0.0.1', port=8765):
        self.robot, self.places, self.token = robot, dict(places or {}), token or ''
        self.state_period = 1.0 / state_hz
        self.host, self.port = host, port
        self.clients = set()
        self.loop = None
        self.active_goal = None          # id of the goal the robot is working on
        self._server = None

    # ------------------------------------------------------------------ robot -> clients
    def emit(self, msg):
        """Thread-safe: called by the robot port for goal/intent events."""
        if msg.get('type') in P.GOAL_FINAL and msg.get('id') == self.active_goal:
            self.active_goal = None
        msg.setdefault('ts', P.now())
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self._broadcast, msg)

    def _broadcast(self, msg):
        data = json.dumps(msg)
        for ws in list(self.clients):
            asyncio.ensure_future(self._send_raw(ws, data))

    async def _send_raw(self, ws, data):
        try:
            await ws.send(data)
        except Exception:
            self.clients.discard(ws)

    async def _send(self, ws, msg):
        await self._send_raw(ws, json.dumps(msg))

    # ------------------------------------------------------------------ clients -> robot
    async def handle(self, ws, *_):
        authed = not self.token
        await self._send(ws, P.event('hello_ok' if authed else 'hello_required', protocol=P.PROTOCOL_VERSION,
                                     intents=sorted(P.INTENTS), places=sorted(self.places), auth_required=bool(self.token)))
        if authed:
            self.clients.add(ws)
            await self._send(ws, P.event('state', **self.robot.state(), active_goal=self.active_goal))
        try:
            while True:
                if authed:
                    raw = await ws.recv()
                else:
                    raw = await asyncio.wait_for(ws.recv(), 5.0)
                err, msg = None, None
                try:
                    err, msg = P.validate(json.loads(raw))
                except (ValueError, TypeError):
                    err = 'invalid JSON'
                if err:
                    await self._send(ws, P.event('error', message=err, id=_id(raw)))
                    continue
                if not authed:
                    if msg['type'] == 'hello' and msg.get('token') == self.token:
                        authed = True
                        self.clients.add(ws)
                        await self._send(ws, P.event('hello_ok', protocol=P.PROTOCOL_VERSION, intents=sorted(P.INTENTS),
                                                     places=sorted(self.places)))
                    else:
                        await ws.close(4401, 'unauthorized')
                        return
                    continue
                await self._dispatch(ws, msg)
        except asyncio.TimeoutError:
            await ws.close(4401, 'hello with token required')
        except Exception as e:            # connection closed or handler bug: never take the server down
            if 'Connection' not in type(e).__name__ and 'Closed' not in type(e).__name__:
                log.exception('client handler')
        finally:
            self.clients.discard(ws)

    async def _dispatch(self, ws, m):
        t = m['type']
        if t == 'ping':
            await self._send(ws, P.event('pong', id=m.get('id')))
        elif t == 'hello':
            await self._send(ws, P.event('hello_ok', protocol=P.PROTOCOL_VERSION, intents=sorted(P.INTENTS), places=sorted(self.places)))
        elif t == 'goal':
            gid = m['id']
            if 'place' in m:
                p = self.places.get(m['place'])
                if p is None:
                    self._broadcast(P.event('goal_received', id=gid))
                    self._broadcast(P.event('goal_rejected', id=gid, reason=f'unknown place {m["place"]!r}', places=sorted(self.places)))
                    return
                pose = {'x': float(p['x']), 'y': float(p['y']), 'yaw': float(p.get('yaw', 0.0)), 'frame': p.get('frame', 'map')}
            else:
                tg = m['target']
                pose = {'x': float(tg['x']), 'y': float(tg['y']), 'yaw': float(tg.get('yaw', 0.0)), 'frame': tg.get('frame', 'map')}
            self._broadcast(P.event('goal_received', id=gid, target=pose, place=m.get('place')))
            self.active_goal = gid
            self.robot.navigate(gid, pose)
        elif t == 'cancel':
            gid = m.get('id') or self.active_goal
            if gid is None or gid != self.active_goal:
                await self._send(ws, P.event('error', id=m.get('id'), message='no such active goal'))
            else:
                self.robot.cancel_goal(gid, 'canceled by client')
        elif t == 'intent':
            self._broadcast(P.event('intent_received', id=m['id'], name=m['name']))
            self.robot.intent(m['id'], m['name'], m.get('args') or {})
        elif t == 'stop':
            self.robot.stop()
            self._broadcast(P.event('robot_event', kind='stop', detail='stop requested by a client'))
        elif t == 'estop':
            self.robot.estop()
            self._broadcast(P.event('robot_event', kind='estop', detail='emergency stop requested by a client'))

    # ------------------------------------------------------------------ lifecycle
    async def _state_loop(self):
        while True:
            await asyncio.sleep(self.state_period)
            if self.clients:
                self._broadcast(P.event('state', **self.robot.state(), active_goal=self.active_goal))

    async def serve(self):
        import websockets
        self.loop = asyncio.get_running_loop()
        self.robot.start(self.emit)
        self._server = await websockets.serve(self.handle, self.host, self.port, ping_interval=20, ping_timeout=20, max_size=64 * 1024)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        log.info('gateway listening on ws://%s:%s (%d places, auth %s)', self.host, self.port, len(self.places),
                 'on' if self.token else 'off')
        asyncio.ensure_future(self._state_loop())

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()


def _id(raw):
    try:
        v = json.loads(raw)
        return v.get('id') if isinstance(v, dict) and isinstance(v.get('id'), str) else None
    except Exception:
        return None
