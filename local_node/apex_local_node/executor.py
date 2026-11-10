"""Capability-Guarded Local Node Task Executor (Protocol v1.0).

Executes bounded JSON-RPC 2.0 tasks on the user's workstation strictly within
configured `allowed_directories` and capability grants (`can_read`, `can_write`):
- `fs.list_directory`: enumerate files inside an allowed directory.
- `fs.read_file`: read text document + compute SHA-256 checksum inside allowed directory.
- `fs.write_report`: write `.md` / `.txt` / `.json` artifact when `can_write=True`.
- `ollama.probe`: inspect local Ollama `/api/tags` models on `127.0.0.1:11434`.

Never exposes an unrestricted shell or allows symlink/`..` traversal outside
explicitly authorized directories.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.request import Request as UrlRequest
from urllib.request import urlopen


class LocalNodeCapabilityError(PermissionError):
    """Raised when a requested task exceeds granted capabilities or allowed paths."""


@dataclass(frozen=True)
class LocalNodePolicy:
    allowed_directories: tuple[Path, ...] = field(default_factory=tuple)
    can_read: bool = True
    can_write: bool = False
    can_execute: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"


class LocalNodeTaskExecutor:
    """Executes JSON-RPC 2.0 task requests against capability-scoped local resources."""

    SAFE_WRITE_EXTENSIONS = {".md", ".txt", ".json", ".csv"}
    MAX_READ_BYTES = 2 * 1024 * 1024  # 2 MiB

    def __init__(self, policy: LocalNodePolicy) -> None:
        self._resolved_roots = tuple(p.resolve() for p in policy.allowed_directories)
        self._policy = policy

    def _resolve_authorized_path(self, raw_path: str) -> Path:
        if not raw_path or "\x00" in raw_path:
            raise LocalNodeCapabilityError("Invalid empty or null-byte path.")
        candidate = Path(raw_path).expanduser().resolve()
        if not self._resolved_roots:
            raise LocalNodeCapabilityError("No allowed_directories configured on node.")
        for allowed in self._resolved_roots:
            if candidate == allowed or candidate.is_relative_to(allowed):
                return candidate
        raise LocalNodeCapabilityError(
            f"Path '{raw_path}' escapes allowed_directories."
        )

    def handle_jsonrpc(self, request_frame: dict[str, Any]) -> dict[str, Any]:
        req_id = request_frame.get("id")
        method = str(request_frame.get("method") or "")
        params = dict(request_frame.get("params") or {})
        try:
            result = self.dispatch(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except LocalNodeCapabilityError as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32001, "message": str(exc)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(exc)},
            }

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "node.ping":
            return {"pong": True}

        if method == "fs.list_directory":
            if not self._policy.can_read:
                raise LocalNodeCapabilityError("Read capability is disabled on this node.")
            target = self._resolve_authorized_path(str(params.get("path", "")))
            if not target.is_dir():
                raise ValueError(f"Not a directory: {target}")
            entries = []
            for child in sorted(target.iterdir(), key=lambda p: p.name)[:200]:
                # Ensure symlinks inside allowed directory cannot point outside allowed roots
                resolved_child = child.resolve()
                if not any(
                    resolved_child == r or resolved_child.is_relative_to(r)
                    for r in self._resolved_roots
                ):
                    continue
                entries.append(
                    {
                        "name": child.name,
                        "is_dir": child.is_dir(),
                        "size_bytes": child.stat().st_size if child.is_file() else 0,
                    }
                )
            return {"directory": str(target), "entries": entries}

        if method == "fs.read_file":
            if not self._policy.can_read:
                raise LocalNodeCapabilityError("Read capability is disabled on this node.")
            target = self._resolve_authorized_path(str(params.get("path", "")))
            if not target.is_file():
                raise ValueError(f"File not found: {target}")
            raw = target.read_bytes()[: self.MAX_READ_BYTES]
            return {
                "path": str(target),
                "byte_size": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
                "content": raw.decode("utf-8", errors="replace"),
            }

        if method == "fs.write_report":
            if not self._policy.can_write:
                raise LocalNodeCapabilityError("Write capability is disabled on this node.")
            target = self._resolve_authorized_path(str(params.get("path", "")))
            if target.suffix.lower() not in self.SAFE_WRITE_EXTENSIONS:
                raise LocalNodeCapabilityError(
                    f"Extension '{target.suffix}' is not permitted for report export."
                )
            content = str(params.get("content", ""))
            raw = content.encode("utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            return {
                "path": str(target),
                "bytes_written": len(raw),
                "checksum_sha256": hashlib.sha256(raw).hexdigest(),
            }

        if method == "ollama.probe":
            url = f"{self._policy.ollama_base_url.rstrip('/')}/api/tags"
            req = UrlRequest(url=url, method="GET")
            try:
                with urlopen(req, timeout=3.0) as resp:  # nosec B310
                    body = json.loads(resp.read().decode("utf-8", errors="replace"))
                models = [
                    str(m.get("name", ""))
                    for m in (body.get("models") or [])
                    if isinstance(m, dict)
                ]
                return {"reachable": True, "models": models}
            except Exception as exc:
                return {"reachable": False, "models": [], "error": str(exc)}

        raise LocalNodeCapabilityError(f"Unsupported or disallowed method '{method}'.")
