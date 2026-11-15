"""End to end over real WebSockets, no ROS: fake phone <-> CompanionServer <-> d1_gateway (mock robot)."""
import asyncio
import json
import os
import sys

import pytest
import websockets

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'd1_gateway'))
from d1_companion.server import CompanionServer  # noqa: E402
from d1_companion.statemachine import Config, S  # noqa: E402
from d1_gateway.core import GatewayCore  # noqa: E402
from d1_gateway.mock_robot import MockRobot  # noqa: E402

PLACES = {
    'home': {'x': 0, 'y': 0, 'yaw': 0, 'label': 'Start point', 'aliases': ['home', 'start'], 'description': ''},
    'front_2m': {'x': 2, 'y': 0, 'yaw': 0, 'label': 'Front, 2 m ahead', 'aliases': ['front'], 'description': ''},
    'left_2m': {'x': 0, 'y': 2, 'yaw': 1.57, 'label': 'Left, 2 m', 'aliases': ['left'], 'description': ''},
    'far': {'x': 60, 'y': 0, 'yaw': 0, 'label': 'Far away', 'aliases': ['far'], 'description': ''},
}
TOKEN = 'secret-join-token'


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class Phone:
    def __init__(self, ws):
        self.ws, self.seen = ws, []

    async def send(self, **m):
        await self.ws.send(json.dumps(m))

    async def until(self, pred, timeout=8.0):
        async def loop():
            while True:
                m = json.loads(await self.ws.recv())
                self.seen.append(m)
                if pred(m):
                    return m
        try:
            return await asyncio.wait_for(loop(), timeout)
        except asyncio.TimeoutError:
            raise AssertionError('timeout; saw ' + str([(m['type'], m.get('state')) for m in self.seen][-12:]))

    async def state(self, name, timeout=8.0):
        return await self.until(lambda m: m['type'] == 'state' and m['state'] == name, timeout)


async def with_stack(fn, robot=None, **cfg):
    gw = GatewayCore(robot or MockRobot(speed=4.0, intent_s=0.2), PLACES, port=0, state_hz=20)
    await gw.serve()
    comp = CompanionServer(PLACES, Config(**cfg), f'ws://127.0.0.1:{gw.port}', join_token=TOKEN, admin_token='staff', port=0)
    await comp.start()
    for _ in range(100):                                     # wait until the companion sees the gateway and the first robot state
        if comp.gw.up and comp.sm.robot:
            break
        await asyncio.sleep(0.05)
    try:
        await fn(f'ws://127.0.0.1:{comp.port}', comp, gw)
    finally:
        await comp.stop()
        await gw.close()


async def join(url, nick='Ana', token=TOKEN):
    ws = await websockets.connect(url)
    p = Phone(ws)
    await p.send(type='join', token=token, nickname=nick)
    return p


def test_qr_token_is_required():
    async def t(url, comp, gw):
        p = await join(url, token='wrong')
        m = await p.until(lambda m: m['type'] in ('joined', 'join_rejected'))
        assert m['type'] == 'join_rejected' and 'QR' in m['reason'] and comp.sm.session is None
        await p.ws.close()
        async with websockets.connect(url) as ws:                        # not joining at all: commands are refused
            await ws.send(json.dumps({'type': 'select', 'destination': 'front_2m'}))
            assert json.loads(await ws.recv())['message'] == 'join first'
    run(with_stack(t))


def test_join_escort_arrive_next_and_end():
    async def t(url, comp, gw):
        p = await join(url)
        j = await p.until(lambda m: m['type'] == 'joined')
        assert {x['name'] for x in j['places']} == set(PLACES) and j['map'] is None
        await p.state('AWAITING_INPUT')
        assert any(m['type'] == 'say' and 'Ana' in m['text'] for m in p.seen)                 # the greeting
        await p.send(type='select', destination='front_2m')
        nav = await p.state('NAVIGATING')
        assert nav['destination'] == 'front_2m'
        arr = await p.state('ARRIVED')
        assert arr['at'] == 'front_2m' and any(o['dest'] == 'left_2m' for o in arr['options'] if o['kind'] == 'place')
        assert any(m['type'] == 'say' and 'arrived at Front' in m['text'] for m in p.seen)
        assert abs(gw.robot.pose['x'] - 2.0) < 0.2                                            # the (mock) robot really got there
        await p.send(type='say', text='take me to the left please')                          # next step by free text
        await p.state('NAVIGATING')
        arr = await p.state('ARRIVED')
        assert arr['at'] == 'left_2m'
        await p.send(type='end')
        await p.until(lambda m: m['type'] == 'session_ended')
        for _ in range(100):                                                                 # the robot drives back to the start point
            if comp.sm.state == S.IDLE:
                break
            await asyncio.sleep(0.1)
        assert comp.sm.state == S.IDLE and comp.sm.at == 'home' and abs(gw.robot.pose['y']) < 0.2
    run(with_stack(t))


def test_second_guest_is_told_the_robot_is_busy():
    async def t(url, comp, gw):
        a = await join(url, 'Ana')
        await a.until(lambda m: m['type'] == 'joined')
        b = await join(url, 'Bob')
        m = await b.until(lambda m: m['type'] in ('joined', 'join_rejected'))
        assert m['type'] == 'join_rejected' and 'another guest' in m['reason']
        await b.send(type='select', destination='front_2m')                                  # and cannot steer it
        assert (await b.until(lambda m: m['type'] == 'error'))['message'] == 'join first'
    run(with_stack(t))


def test_stop_midway_then_continue():
    async def t(url, comp, gw):
        p = await join(url)
        await p.state('AWAITING_INPUT')
        await p.send(type='select', destination='far')
        await p.until(lambda m: m['type'] == 'state' and m['state'] == 'NAVIGATING' and m['goal_status'] == 'moving')
        await p.send(type='stop')
        await p.state('STOPPED')
        await asyncio.sleep(0.5)
        assert comp.sm.state == S.STOPPED                                                    # the late goal_canceled changed nothing
        await p.send(type='select', destination='front_2m')
        await p.state('ARRIVED')
    run(with_stack(t))


def test_not_localized_robot_refuses_and_says_why():
    async def t(url, comp, gw):
        p = await join(url)
        await p.state('AWAITING_INPUT')
        await p.send(type='select', destination='front_2m')
        b = await p.state('BLOCKED')
        assert 'not placed' in b['reason'] and gw.robot.pose['x'] == 0                       # nothing was sent to the robot
    run(with_stack(t, robot=MockRobot(speed=4.0, localized=False)))


def test_gateway_loss_holds_and_recovers():
    async def t(url, comp, gw):
        p = await join(url)
        await p.state('AWAITING_INPUT')
        await p.send(type='select', destination='far')
        await p.until(lambda m: m['type'] == 'state' and m['goal_status'] == 'moving')
        await gw.close()                                                                     # the control link dies mid-escort
        h = await p.state('HOLD')
        assert 'control link' in h['reason'] or 'link' in h['message']
    run(with_stack(t))


def test_silence_ends_the_session():
    async def t(url, comp, gw):
        p = await join(url)
        await p.state('AWAITING_INPUT')
        await p.until(lambda m: m['type'] == 'say' and 'still there' in m['text'], 5)
        ended = await p.until(lambda m: m['type'] == 'session_ended', 5)
        assert 'not heard' in ended['reason']
    run(with_stack(t, input_prompt_s=0.6, input_timeout_s=1.5))


def test_resume_and_staff_end_session():
    async def t(url, comp, gw):
        p = await join(url)
        j = await p.until(lambda m: m['type'] == 'joined')
        await p.ws.close()                                                                   # phone loses signal ...
        async with websockets.connect(url) as ws:                                           # ... and comes back
            await ws.send(json.dumps({'type': 'resume', 'session': j['session']}))
            q = Phone(ws)
            await q.until(lambda m: m['type'] == 'joined')
            await q.until(lambda m: m['type'] == 'state')
            await q.send(type='admin', token='nope', action='end_session')
            assert 'not allowed' in (await q.until(lambda m: m['type'] == 'error'))['message']
            assert comp.sm.session is not None
            await q.send(type='admin', token='staff', action='end_session')
            await q.until(lambda m: m['type'] == 'session_ended')
        assert comp.sm.session is None
        async with websockets.connect(url) as ws:                                           # an old session id no longer works
            await ws.send(json.dumps({'type': 'resume', 'session': j['session']}))
            assert json.loads(await ws.recv())['type'] == 'join_rejected'
    run(with_stack(t))


def test_bad_input_does_not_break_the_server():
    async def t(url, comp, gw):
        p = await join(url)
        await p.state('AWAITING_INPUT')
        for bad in ('not json', '[]', json.dumps({'type': 'fly'}), json.dumps({'type': 'select', 'destination': 5}),
                    json.dumps({'type': 'say', 'text': 'x' * 600})):
            await p.ws.send(bad)
            await p.until(lambda m: m['type'] == 'error')
        await p.send(type='ping')
        await p.until(lambda m: m['type'] == 'pong')
        assert comp.sm.state == S.AWAITING_INPUT
    run(with_stack(t))
