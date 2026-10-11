from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

from f1_engineer.udp.isolated import IsolatedUDPSource
from f1_engineer.udp.source import UDPSource


async def measure(source_type, args):
    source = source_type("127.0.0.1", 0, 1024, independent_receiver=True)
    await source.open()
    sender = None
    try:
        port = source._receiver_socket.getsockname()[1]
        code = (
            "import socket,sys,time;sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);"
            "print('ready',flush=True);time.sleep(.05);started=time.perf_counter();"
            "\nfor index in range(int(sys.argv[2])):"
            "\n time.sleep(max(0,started+index/float(sys.argv[3])-time.perf_counter()));"
            "sender.sendto(index.to_bytes(2,'little')+bytes(1458),('127.0.0.1',int(sys.argv[1])))"
        )
        sender = subprocess.Popen([sys.executable, "-c", code, str(port), str(args.packets), str(args.rate)],
                                  stdout=subprocess.PIPE, text=True)
        if await asyncio.to_thread(sender.stdout.readline) != "ready\n":
            raise RuntimeError("independent sender did not become ready")
        await asyncio.sleep(0.075)
        ctypes.PyDLL(None).usleep(int(args.hold_ms * 1000))
        await asyncio.to_thread(sender.wait, 5)
        await asyncio.sleep(0.3)
        source.close()
        packets = source.drain_pending()
        return {"sent": args.packets, "received": source.stats.received,
                "application_drops": source.stats.dropped, "kernel_drops": source.kernel_receive_drops,
                "socket_errors": source.stats.socket_errors, "queue_limit": 1024,
                "receive_buffer_bytes": source.receive_buffer_bytes,
                "payload_order_valid": [int.from_bytes(raw.payload[:2], "little") for raw in packets]
                == list(range(args.packets))}
    finally:
        if sender is not None:
            if sender.poll() is None:
                sender.kill()
            sender.wait(timeout=3)
            sender.stdout.close()
        source.close()


def main():
    parser = argparse.ArgumentParser(description="Isolated-port interpreter-stall reproduction; not live-game acceptance")
    parser.add_argument("--rate", type=float, default=530)
    parser.add_argument("--packets", type=int, default=240)
    parser.add_argument("--hold-ms", type=float, default=190)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Linux is required")
    if not 1 <= args.packets <= 1024 or not 0 < args.rate <= 2000 or not 0 < args.hold_ms <= 1000:
        parser.error("invalid bounded diagnostic parameters")
    before = asyncio.run(measure(UDPSource, args))
    after = asyncio.run(measure(IsolatedUDPSource, args))
    result = {"rate": args.rate, "hold_ms": args.hold_ms, "thread_receiver": before,
              "isolated_receiver": after, "reproduced": (before["kernel_drops"] or 0) > 0,
              "passed": (after["received"] == args.packets and after["application_drops"] == 0
                         and after["kernel_drops"] == 0 and after["socket_errors"] == 0
                         and after["payload_order_valid"]
                         and before["receive_buffer_bytes"] == after["receive_buffer_bytes"])}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
