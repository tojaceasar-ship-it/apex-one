"""FastAPI HTTP + WebSocket routes for the Hybrid Local Node Gateway."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from apex_one.local_node_gateway.protocol import (
    SUPPORTED_PROTOCOL_VERSION,
    HandshakeRequest,
    HeartbeatRequest,
    LocalNodeGatewayRegistry,
)


def build_local_node_router(registry: LocalNodeGatewayRegistry) -> APIRouter:
    router = APIRouter(prefix="/api/v1/local-node", tags=["hybrid-local-node"])

    @router.get("/status")
    async def get_local_node_status() -> dict[str, object]:
        return registry.status_snapshot()

    @router.post("/handshake")
    async def handshake(
        request: Request,
        x_apex_node_timestamp: str | None = Header(default=None),
        x_apex_node_nonce: str | None = Header(default=None),
        x_apex_node_signature: str | None = Header(default=None),
    ) -> dict[str, object]:
        raw_body: dict[str, Any] = await request.json()
        try:
            registry.verify_signed_request(
                timestamp_header=x_apex_node_timestamp,
                nonce_header=x_apex_node_nonce,
                signature_header=x_apex_node_signature,
                payload=raw_body,
            )
            parsed = HandshakeRequest.model_validate(raw_body)
            state = registry.register_handshake(parsed)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "protocol_version": SUPPORTED_PROTOCOL_VERSION,
            "node_id": state.node_id,
            "session_token": state.session_token,
            "registered_at": state.registered_at.isoformat(),
            "arena_isolated_mode": registry.arena_isolated_mode,
            "user_machine_connected": False if registry.arena_isolated_mode else True,
        }

    @router.post("/heartbeat")
    async def heartbeat(
        request: Request,
        authorization: str | None = Header(default=None),
        x_apex_node_timestamp: str | None = Header(default=None),
        x_apex_node_nonce: str | None = Header(default=None),
        x_apex_node_signature: str | None = Header(default=None),
    ) -> dict[str, object]:
        raw_body: dict[str, Any] = await request.json()
        bearer = None
        if authorization and authorization.lower().startswith("bearer "):
            bearer = authorization[7:].strip()

        try:
            registry.verify_signed_request(
                timestamp_header=x_apex_node_timestamp,
                nonce_header=x_apex_node_nonce,
                signature_header=x_apex_node_signature,
                payload=raw_body,
            )
            parsed = HeartbeatRequest.model_validate(raw_body)
            state = registry.record_heartbeat(parsed, bearer_token=bearer)
        except PermissionError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "status": "ACK",
            "node_id": state.node_id,
            "node_status": state.status,
            "last_heartbeat_at": state.last_heartbeat_at.isoformat(),
        }

    @router.websocket("/ws/{node_id}")
    async def local_node_ws(websocket: WebSocket, node_id: str) -> None:
        token = websocket.query_params.get("token", "")
        try:
            state = registry.verify_session_token(node_id, token)
        except PermissionError:
            await websocket.close(code=4401)
            return

        await websocket.accept()
        try:
            while True:
                frame = await websocket.receive_json()
                method = frame.get("method")
                msg_id = frame.get("id")
                if method == "node.ping":
                    await websocket.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "pong": True,
                                "node_id": state.node_id,
                                "protocol_version": SUPPORTED_PROTOCOL_VERSION,
                                "user_machine_connected": False
                                if registry.arena_isolated_mode
                                else True,
                            },
                        }
                    )
                elif method == "task.result":
                    params = dict(frame.get("params") or {})
                    await websocket.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "acknowledged": True,
                                "node_id": state.node_id,
                                "task_id": params.get("task_id"),
                            },
                        }
                    )
                elif method == "node.disconnect":
                    state.status = "OFFLINE"
                    await websocket.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {"disconnected": True},
                        }
                    )
                    await websocket.close(code=1000)
                    return
                else:
                    await websocket.send_json(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {
                                "code": -32601,
                                "message": f"Unknown method '{method}'.",
                            },
                        }
                    )
        except WebSocketDisconnect:
            state.status = "OFFLINE"

    return router
