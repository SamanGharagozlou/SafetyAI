"""Asyncio client for the robot gateway (docs/robot_gateway.md): auto-reconnect, thread-free. Only goals / stop / named intents are sent."""
import asyncio
import json
import logging

log = logging.getLogger('d1_companion.gateway')


class GatewayClient:
    def __init__(self, url, token='', on_message=None, on_up=None, on_down=None):
        self.url, self.token = url, token
        self.on_message, self.on_up, self.on_down = on_message, on_up, on_down
        self.ws = None
        self.up = False
        self._task = None

    def start(self):
        self._task = asyncio.ensure_future(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.ws is not None:
            await self.ws.close()

    async def send(self, msg):
        """Returns False when the gateway is not connected (the caller decides what that means)."""
        if not self.up or self.ws is None:
            return False
        try:
            await self.ws.send(json.dumps(msg))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _run(self):
        import websockets
        delay = 0.5
        while True:
            try:
                async with websockets.connect(self.url, max_size=1 << 20, ping_interval=20, ping_timeout=20) as ws:
                    self.ws = ws
                    first = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
                    if first.get('type') == 'hello_required':
                        await ws.send(json.dumps({'type': 'hello', 'token': self.token}))
                        first = json.loads(await asyncio.wait_for(ws.recv(), 5.0))
                    if first.get('type') != 'hello_ok':
                        raise RuntimeError(f'unexpected gateway greeting: {first.get("type")}')
                    self.up, delay = True, 0.5
                    log.info('gateway connected: %s', self.url)
                    if self.on_up:
                        await self.on_up()
                    async for raw in ws:
                        try:
                            m = json.loads(raw)
                        except ValueError:
                            continue
                        if self.on_message:
                            await self.on_message(m)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning('gateway link: %s', e)
            was_up, self.up, self.ws = self.up, False, None
            if was_up and self.on_down:
                await self.on_down()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 5.0)
