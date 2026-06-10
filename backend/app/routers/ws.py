from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.redis_client import get_redis

router = APIRouter()


@router.websocket("/ws/world-stream")
async def world_stream(websocket: WebSocket):
    """Real-time feed: vision_received, event_accepted, event_rejected, with world_state diffs."""
    await websocket.accept()
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(settings.stream_channel)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(settings.stream_channel)
        await pubsub.aclose()
