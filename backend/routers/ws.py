"""WebSocket for the browser (ROADMAP 3.7). Server to client only; what the client sends is ignored."""

from __future__ import annotations

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter(tags=["websocket"])

TOO_SLOW = 1013   # "try again later"


async def _watch_for_disconnect(websocket: WebSocket, scope: anyio.CancelScope) -> None:
    """Reading is how a closed connection is noticed; whatever the client sends is dropped."""
    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, RuntimeError):
        pass
    scope.cancel()


@router.websocket("/api/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    hub = websocket.app.state.hub
    await websocket.accept()
    conn = hub.connect()
    try:
        # A task group, not bare asyncio tasks: it cooperates with the cancellation of the server
        # (and of the test client) instead of leaving tasks behind.
        async with anyio.create_task_group() as tg:
            tg.start_soon(_watch_for_disconnect, websocket, tg.cancel_scope)
            try:
                while True:
                    text = await conn.queue.get()
                    if text is None:   # the hub gave up on this client: it was too far behind
                        await websocket.close(code=TOO_SLOW)
                        break
                    await websocket.send_text(text)
            except (WebSocketDisconnect, RuntimeError):   # the client went away while we were sending
                pass
            tg.cancel_scope.cancel()
    finally:
        hub.disconnect(conn)
