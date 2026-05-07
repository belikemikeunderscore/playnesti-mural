import pathlib
from aiohttp import web, WSMsgType

STATIC_DIR = pathlib.Path(__file__).parent / "static"
MEDIA_DIR = pathlib.Path("media")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    state = request.app["state"]
    state.ws_clients.add(ws)

    if state.media_items:
        await ws.send_json({"type": "init", "items": state.media_items})

    async for msg in ws:
        if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
            break

    state.ws_clients.discard(ws)
    return ws


async def index_handler(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


def create_app(state) -> web.Application:
    app = web.Application()
    app["state"] = state

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/media", MEDIA_DIR)
    app.router.add_static("/static", STATIC_DIR)

    return app
