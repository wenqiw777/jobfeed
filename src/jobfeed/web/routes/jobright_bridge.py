"""Chrome-extension bridge endpoints for the Jobright scan source."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, cast

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from jobfeed.cli import AppContext
from jobfeed.services.jobright_bridge import (
    JobrightBridge,
    JobrightBridgeConnection,
    JobrightBridgeError,
)

router = APIRouter()


class JobrightBridgeStatus(BaseModel):
    """Current in-process extension connection state."""

    connected: bool


@router.get("/sources/jobright/status")
async def bridge_status(request: Request) -> JobrightBridgeStatus:
    """Return whether the Jobright extension is connected.

    Args:
        request: FastAPI request carrying the application context.

    Returns:
        Current in-process bridge connection state.
    """
    return JobrightBridgeStatus(connected=_bridge(request.app.state.context).connected)


@router.websocket("/sources/jobright/bridge")
async def bridge_socket(websocket: WebSocket) -> None:
    """Carry scan commands and result batches over one local extension socket.

    Args:
        websocket: Candidate local Chrome-extension connection.
    """
    origin = websocket.headers.get("origin", "")
    if origin and not origin.startswith("chrome-extension://"):
        await websocket.close(code=1008, reason="Chrome extension origin required")
        return
    await websocket.accept()
    bridge = _bridge(websocket.app.state.context)
    connection: JobrightBridgeConnection | None = None
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), timeout=5)
        if hello != {"type": "hello", "protocol": 1}:
            await websocket.close(code=1008, reason="Unsupported bridge protocol")
            return
        connection = bridge.connect()
        await websocket.send_json({"type": "ready", "protocol": 1})
        sender = asyncio.create_task(_send_commands(websocket, connection))
        receiver = asyncio.create_task(_receive_messages(websocket, bridge))
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()
    except (TimeoutError, WebSocketDisconnect, JobrightBridgeError):
        return
    finally:
        if connection is not None:
            bridge.disconnect(connection)


async def _send_commands(
    websocket: WebSocket, connection: JobrightBridgeConnection
) -> None:
    while True:
        await websocket.send_json(await connection.next_command())


async def _receive_messages(websocket: WebSocket, bridge: JobrightBridge) -> None:
    while True:
        message: Any = await websocket.receive_json()
        if not isinstance(message, dict):
            raise JobrightBridgeError("Jobright bridge message must be an object")
        if message.get("type") == "ping":
            await websocket.send_json({"type": "pong"})
            continue
        await bridge.receive(cast(dict[str, object], message))


def _bridge(context: object) -> JobrightBridge:
    return cast(AppContext, context)["jobright_bridge"]


__all__ = ["router"]
