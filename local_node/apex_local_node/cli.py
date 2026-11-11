"""Standalone CLI for the APEX ONE Hybrid Local Node."""

from __future__ import annotations

import argparse
import asyncio
import json

from apex_local_node import PROTOCOL_VERSION, __version__
from apex_local_node.capabilities import discover_node_capabilities
from apex_local_node.client import LocalNodeClient
from apex_local_node.config import LocalNodeSettings


async def _run_once(settings: LocalNodeSettings) -> int:
    client = LocalNodeClient(settings)
    hs = await client.handshake()
    hb = await client.heartbeat()
    print(json.dumps({"handshake": hs, "heartbeat": hb}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="APEX ONE Hybrid Local Node Agent")
    parser.add_argument(
        "--inspect-capabilities",
        action="store_true",
        help="Print discovered local node capabilities and protocol metadata.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Perform a single handshake + heartbeat cycle against APEX_NODE_CLOUD_URL.",
    )
    args = parser.parse_args(argv)
    settings = LocalNodeSettings()

    if args.inspect_capabilities:
        print(
            json.dumps(
                {
                    "app": "apex-local-node",
                    "version": __version__,
                    "protocol_version": PROTOCOL_VERSION,
                    "node_id": settings.node_id,
                    "capabilities": discover_node_capabilities(),
                    "arena_notice": (
                        "Running inside current host; inside Arena sandbox this is "
                        "NOT connected to the user's physical workstation."
                    ),
                },
                indent=2,
            )
        )
        return 0

    if args.once:
        return asyncio.run(_run_once(settings))

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
