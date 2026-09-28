# Object detection (YOLO + FastAPI)

- **Frontend:** `index.html` — camera, MQTT (RED/YELLOW/OFF)
- **Backend:** `server.py` + `vision.py` — YOLO via ONNX Runtime
- **Deploy:** [Render](https://render.com) — see `render.yaml` and `DEPLOY_RENDER.md`

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

Health: `GET /health`
