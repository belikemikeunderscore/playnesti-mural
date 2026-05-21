import json
import pathlib
import secrets
from aiohttp import web, WSMsgType

import config

STATIC_DIR = pathlib.Path(__file__).parent / "static"
MEDIA_DIR = pathlib.Path("media")
METADATA_PATH = MEDIA_DIR / "metadata.json"

# Simple session storage
_auth_sessions = set()

def _save_metadata(items, message_media):
    payload = {
        "items": items,
        "message_media": {str(key): urls for key, urls in message_media.items()},
    }
    METADATA_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

def _load_metadata():
    if not METADATA_PATH.exists():
        return [], {}
    try:
        data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        items = data.get("items", [])
        message_media = data.get("message_media", {})
        return items, message_media
    except Exception:
        return [], {}


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    state = request.app["state"]
    state.ws_clients.add(ws)

    if state.media_items:
        await ws.send_json({"type": "init", "items": state.media_items})

    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                if data.get("type") == "delete" and "id" in data:
                    # Remove item from state
                    state.media_items = [
                        item for item in state.media_items if item["url"] != data["id"]
                    ]
                    # Delete file if exists
                    try:
                        path = MEDIA_DIR / data["id"].removeprefix("/media/")
                        if path.exists():
                            path.unlink()
                    except Exception:
                        pass
                    # Save metadata
                    _, message_media = _load_metadata()
                    _save_metadata(state.media_items, message_media)
                    # Broadcast removal
                    dead = set()
                    for client in state.ws_clients:
                        try:
                            await client.send_json({"type": "remove", "id": data["id"]})
                        except Exception:
                            dead.add(client)
                    state.ws_clients -= dead
            except json.JSONDecodeError:
                pass
        elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
            break

    state.ws_clients.discard(ws)
    return ws


async def index_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def login_handler(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        password = data.get("password", "")
        
        if password == config.MODERATION_PASSWORD:
            # Generate session token
            token = secrets.token_urlsafe(32)
            _auth_sessions.add(token)
            
            # Set cookie with 24-hour expiration
            response = web.Response(status=200)
            response.set_cookie('mod_session', token, max_age=86400, httponly=True, samesite='Lax')
            return response
        else:
            return web.Response(status=401, text="Unauthorized")
    except Exception:
        return web.Response(status=400, text="Bad Request")


async def moderation_handler(request: web.Request) -> web.Response:
    token = request.cookies.get('mod_session', '')
    
    if token not in _auth_sessions:
        return web.FileResponse(STATIC_DIR / "login.html")
    
    return web.FileResponse(STATIC_DIR / "moderation.html")


def create_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state

    app.router.add_get("/", index_handler)
    app.router.add_post("/auth/login", login_handler)
    app.router.add_get("/moderation", moderation_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/media", MEDIA_DIR)
    app.router.add_static("/static", STATIC_DIR)

    return app
