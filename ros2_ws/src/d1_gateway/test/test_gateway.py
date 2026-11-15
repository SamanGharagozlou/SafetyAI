"""End-to-end tests over a real WebSocket with the mock robot (no ROS needed): pytest ros2_ws/src/d1_gateway/test"""
import asyncio
import json
import os
import sys

import pytest
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from d1_gateway.core import GatewayCore  # noqa: E402
from d1_gateway.mock_robot import MockRobot  # noqa: E402
from d1_gateway import protocol as P  # noqa: E402

PLACES = {'reception': {'x': 0.3, 'y': 0.0, 'yaw': 1.0}}


async def _with_gateway(fn, token='', speed=2.0):
    core = GatewayCore(MockRobot(speed=speed, intent_s=0.2), PLACES, token=token, port=0, state_hz=20)
    await core.serve()
    try:
        await fn(f'ws://127.0.0.1:{core.port}', core)
    finally:
        await core.close()


async def _until(ws, pred, timeout=5.0):
    seen = []
    async def loop():
        while True:
            m = json.loads(await ws.recv())
            seen.append(m)
            if pred(m):
                return m
    try:
        return await asyncio.wait_for(loop(), timeout), seen
    except asyncio.TimeoutError:
        raise AssertionError(f'timeout; saw {[m["type"] for m in seen]}')


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_goal_lifecycle_and_state():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            hello = json.loads(await ws.recv())
            assert hello['type'] == 'hello_ok' and 'say_hi' in hello['intents'] and hello['places'] == ['reception']
            await ws.send(json.dumps({'type': 'goal', 'id': 'g1', 'target': {'x': 1.0, 'y': 0.0, 'yaw': 0.0}}))
            _, seen = await _until(ws, lambda m: m['type'] == 'goal_reached')
            types = [m['type'] for m in seen if m.get('id') == 'g1']
            assert types[0] == 'goal_received' and 'goal_accepted' in types and types[-1] == 'goal_reached'
            assert any(m['type'] == 'state' and m['pose'] for m in seen)
    run(_with_gateway(t))


def test_named_place_and_unknown_place():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'goal', 'id': 'p', 'place': 'reception'}))
            await _until(ws, lambda m: m['type'] == 'goal_reached' and m['id'] == 'p')
            await ws.send(json.dumps({'type': 'goal', 'id': 'q', 'place': 'nowhere'}))
            m, _ = await _until(ws, lambda m: m['type'] == 'goal_rejected')
            assert 'unknown place' in m['reason']
    run(_with_gateway(t))


def test_intent_cancel_preempt_stop():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'intent', 'id': 'i1', 'name': 'say_hi', 'args': {'text': 'Hi'}}))
            _, seen = await _until(ws, lambda m: m['type'] == 'intent_done')
            assert [m['type'] for m in seen if m.get('id') == 'i1'] == ['intent_received', 'intent_started', 'intent_done']
            # preempt: a far goal then a second one
            await ws.send(json.dumps({'type': 'goal', 'id': 'a', 'target': {'x': 50, 'y': 0}}))
            await _until(ws, lambda m: m['type'] == 'goal_accepted')
            await ws.send(json.dumps({'type': 'goal', 'id': 'b', 'target': {'x': 60, 'y': 0}}))
            m, _ = await _until(ws, lambda m: m['type'] == 'goal_canceled')
            assert m['id'] == 'a' and 'preempted' in m['reason']
            await ws.send(json.dumps({'type': 'cancel', 'id': 'b'}))
            m, _ = await _until(ws, lambda m: m['type'] == 'goal_canceled' and m['id'] == 'b')
    run(_with_gateway(t, speed=1.0))


def test_follow_intents():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            hello = json.loads(await ws.recv())
            assert {'follow_lock', 'follow_release'} <= set(hello['intents'])
            for name in ('follow_lock', 'follow_release'):
                await ws.send(json.dumps({'type': 'intent', 'id': name, 'name': name}))
                m, _ = await _until(ws, lambda m: m['type'] == 'intent_done' and m['id'] == name)
    run(_with_gateway(t))


def test_bad_messages_do_not_kill_the_connection():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            await ws.recv()
            for bad in ('not json', '[]', json.dumps({'type': 'fly'}), json.dumps({'type': 'goal', 'id': 'x'}),
                        json.dumps({'type': 'intent', 'id': 'x', 'name': 'dance'})):
                await ws.send(bad)
                m, _ = await _until(ws, lambda m: m['type'] == 'error')
            await ws.send(json.dumps({'type': 'ping', 'id': 'k'}))
            m, _ = await _until(ws, lambda m: m['type'] == 'pong')
            assert m['id'] == 'k'
    run(_with_gateway(t))


def test_token_required():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            assert json.loads(await ws.recv())['type'] == 'hello_required'
            await ws.send(json.dumps({'type': 'hello', 'token': 'wrong'}))
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
        async with websockets.connect(url) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'hello', 'token': 's3cret'}))
            assert json.loads(await ws.recv())['type'] == 'hello_ok'
    run(_with_gateway(t, token='s3cret'))


def test_goal_rejected_when_lying_and_estop():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'estop'}))
            await _until(ws, lambda m: m['type'] == 'robot_event' and m['kind'] == 'estop')
            await ws.send(json.dumps({'type': 'goal', 'id': 'z', 'target': {'x': 1, 'y': 1}}))
            m, _ = await _until(ws, lambda m: m['type'] == 'goal_rejected')
            assert 'stand_up' in m['reason']
    run(_with_gateway(t))


def test_validate():
    assert P.validate({'type': 'goal', 'id': 'a', 'target': {'x': 1, 'y': float('nan')}})[0]
    assert P.validate({'type': 'goal', 'id': 'a', 'target': {'x': 1, 'y': 2}})[0] is None
