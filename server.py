"""
FastAPI: realtime YOLO predict + static index.html

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from vision import SERVER_BUILD, VisionEngine, ClientState

ROOT = Path(__file__).resolve().parent
engine: VisionEngine | None = None
_infer_lock = asyncio.Lock()
_http_states: dict[str, ClientState] = {}


def get_engine() -> VisionEngine:
    if engine is None:
        raise RuntimeError("Model not loaded")
    return engine


def http_state(client_id: str | None) -> ClientState:
    key = (client_id or "default")[:64]
    if key not in _http_states:
        _http_states[key] = ClientState()
    return _http_states[key]


def result_payload(frame_id: int, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "result",
        "id": frame_id,
        "level": result["level"],
        "bestScore": result["bestScore"],
        "yoloMs": result["yoloMs"],
        "depthMs": 0,
        "hasDepth": False,
        "depthPreview": None,
        "det": result["det"],
    }


async def run_predict(jpeg: bytes, vw: int, vh: int, cw: int, ch: int, state: ClientState) -> dict[str, Any]:
    eng = get_engine()

    def _run():
        return eng.process_frame(jpeg, vw, vh, cw, ch, state)

    async with _infer_lock:
        return await asyncio.to_thread(_run)


app = FastAPI(title="YOLO Vision API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def load_model():
    global engine
    engine = VisionEngine.load()
    print(f"[yolo] loaded {ROOT / 'model.onnx'}")


def health_data() -> dict[str, Any]:
    return {
        "ok": True,
        "build": SERVER_BUILD,
        "yolo": engine is not None,
        "depth": False,
        "depthSize": [0, 0],
        "predictHttp": "/api/predict",
        "wsPath": "/ws",
    }


@app.get("/health")
@app.get("/api/health")
async def health():
    return health_data()


@app.post("/api/predict")
async def predict_http(request: Request):
    try:
        ct = request.headers.get("content-type", "")
        body = await request.body()
        frame_id = 0
        client_id = request.headers.get("x-client-id")

        if "application/json" in ct:
            data = json.loads(body.decode("utf-8"))
            jpeg = base64.b64decode(data["jpeg"])
            vw = int(data.get("vw") or 0)
            vh = int(data.get("vh") or 0)
            cw = int(data.get("cw") or 0)
            ch = int(data.get("ch") or 0)
            client_id = data.get("clientId") or client_id
            frame_id = int(data.get("id") or 0)
        else:
            jpeg = body
            vw = int(request.headers.get("x-vw") or 0)
            vh = int(request.headers.get("x-vh") or 0)
            cw = int(request.headers.get("x-cw") or 0)
            ch = int(request.headers.get("x-ch") or 0)
            frame_id = int(request.headers.get("x-frame-id") or 0)

        if not jpeg:
            return JSONResponse({"type": "error", "message": "empty jpeg"}, status_code=400)

        state = http_state(client_id)
        result = await run_predict(jpeg, vw, vh, cw, ch, state)
        return result_payload(frame_id, result)
    except Exception as e:
        return JSONResponse({"type": "error", "message": str(e)}, status_code=500)


@app.websocket("/ws")
async def websocket_predict(ws: WebSocket):
    await ws.accept()
    state = ClientState()
    await ws.send_json(
        {
            "type": "ready",
            "yolo": True,
            "depth": False,
            "depthSize": [0, 0],
            "nearThreshold": 0.35,
        }
    )
    pending_meta: dict[str, Any] | None = None
    try:
        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("text") is not None:
                msg = json.loads(message["text"])
                if msg.get("type") != "frame":
                    continue
                if msg.get("encoding") == "binary":
                    pending_meta = msg
                    continue
                if not msg.get("jpeg"):
                    continue
                jpeg = base64.b64decode(msg["jpeg"])
                vw = int(msg.get("vw") or 0)
                vh = int(msg.get("vh") or 0)
                cw = int(msg.get("cw") or 0)
                ch = int(msg.get("ch") or 0)
                frame_id = int(msg.get("id") or 0)
                result = await run_predict(jpeg, vw, vh, cw, ch, state)
                await ws.send_json(result_payload(frame_id, result))
                continue

            if message.get("bytes") is not None and pending_meta:
                msg = pending_meta
                pending_meta = None
                jpeg = message["bytes"]
                vw = int(msg.get("vw") or 0)
                vh = int(msg.get("vh") or 0)
                cw = int(msg.get("cw") or 0)
                ch = int(msg.get("ch") or 0)
                frame_id = int(msg.get("id") or 0)
                result = await run_predict(jpeg, vw, vh, cw, ch, state)
                await ws.send_json(result_payload(frame_id, result))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


app.mount("/", StaticFiles(directory=str(ROOT), html=True), name="static")
