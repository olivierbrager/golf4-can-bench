import asyncio, json, sys
import websockets

URL = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8010/ws"

async def main():
    async with websockets.connect(URL) as ws:
        for _ in range(5):
            msg = await ws.recv()
            p = json.loads(msg)
            m = p["meta"]
            print("stale=", m["stale"], "rx=", m["rx_total"], "/", m["rx_decoded"], "last=", m["last_frame"])
asyncio.run(main())
