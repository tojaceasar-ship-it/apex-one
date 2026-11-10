"""Host capability discovery for the standalone Hybrid Local Node."""

from __future__ import annotations

import os
import platform
import sys
from typing import Any


def discover_node_capabilities() -> dict[str, Any]:
    return {
        "os": platform.system().lower(),
        "arch": platform.machine(),
        "python_version": sys.version.split()[0],
        "cpu_count": os.cpu_count() or 1,
        "supported_transports": ["https-json", "wss-jsonrpc-2.0"],
        "local_fs_confined": True,
    }
