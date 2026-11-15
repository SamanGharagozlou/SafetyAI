#!/usr/bin/env python3
"""Robot-free backend for trying the visitor app: the real gateway core + companion with a *mock* robot (no ROS, no hardware).

  python3 tools/mock_stack.py --token <qr-token> [--speed 0.3] [--not-localized] [--port 8780] [--places maps/<map>/places.yaml]

The mock robot walks at --speed m/s between the places, stands, and answers like the real one (goal received / accepted / progress / reached,
stand-up, refusal when not placed). --not-localized shows the "Not ready" state. Binds 127.0.0.1 only; the frontend dev server proxies /api to it.
"""
import argparse
import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ros2_ws/src/d1_gateway'))
sys.path.insert(0, str(ROOT / 'ros2_ws/src/d1_companion'))
from d1_companion.places import load_places  # noqa: E402
from d1_companion.server import CompanionServer  # noqa: E402
from d1_companion.statemachine import Config  # noqa: E402
from d1_gateway.core import GatewayCore  # noqa: E402
from d1_gateway.mock_robot import MockRobot  # noqa: E402


async def main(a):
    places = load_places(a.places)
    gw = GatewayCore(MockRobot(speed=a.speed, intent_s=0.8, localized=not a.not_localized), places, port=0, state_hz=10)
    await gw.serve()
    comp = CompanionServer(places, Config(), f'ws://127.0.0.1:{gw.port}', join_token=a.token, port=a.port)
    await comp.start()
    print(f'mock stack up: companion http://127.0.0.1:{a.port}, mock robot {a.speed} m/s, '
          f'{"NOT placed on the map" if a.not_localized else "placed"}, {len(places)} places', flush=True)
    await asyncio.Event().wait()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--token', required=True, help='the secret carried by the QR code')
    ap.add_argument('--speed', type=float, default=0.3)
    ap.add_argument('--not-localized', action='store_true')
    ap.add_argument('--port', type=int, default=8780)
    ap.add_argument('--places', default=str(ROOT / 'maps/metacam_lite_2026-11-14/places.yaml'))
    try:
        asyncio.run(main(ap.parse_args()))
    except KeyboardInterrupt:
        pass
