"""YOLO inference (ONNX) — shared by HTTP and WebSocket."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort
from PIL import Image

ROOT = Path(__file__).resolve().parent
YOLO_PATH = ROOT / "model.onnx"

INPUT_SIZE = 640
CONF_THRESHOLD = 0.30
IOU_THRESHOLD = 0.45
NEAR_RATIO = 0.35
NEAR_HYSTERESIS = 0.08
VEHICLE_HOLD_MS = 700
VEHICLE_CLASSES = {1, 2, 3, 5, 6, 7}

LEVEL_NONE = 0
LEVEL_FAR = 1
LEVEL_NEAR = 2

SERVER_BUILD = "2026-09-28-fastapi-yolo2"


def clamp(v: float, a: float, b: float) -> float:
    return min(b, max(a, v))


def iou(b1, b2) -> float:
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
    a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
    return inter / (a1 + a2 - inter or 1.0)


def nms(boxes, scores, class_ids, threshold: float):
    idxs = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    keep = []
    suppressed = set()
    for i, cur in enumerate(idxs):
        if cur in suppressed:
            continue
        keep.append(cur)
        for next_i in idxs[i + 1 :]:
            if next_i in suppressed:
                continue
            if class_ids[cur] == class_ids[next_i] and iou(boxes[cur], boxes[next_i]) > threshold:
                suppressed.add(next_i)
    return keep


@dataclass
class ClientState:
    current_level: int = LEVEL_NONE
    vehicle_seen_at: float = field(default_factory=lambda: -1e18)


class VisionEngine:
    def __init__(self, session: ort.InferenceSession):
        self.session = session
        self.input_name = session.get_inputs()[0].name
        self.output_name = session.get_outputs()[0].name

    @classmethod
    def load(cls) -> "VisionEngine":
        if not YOLO_PATH.is_file():
            raise FileNotFoundError(f"ไม่พบ {YOLO_PATH}")
        session = ort.InferenceSession(
            str(YOLO_PATH),
            providers=["CPUExecutionProvider"],
        )
        return cls(session)

    def letterbox(self, jpeg: bytes, vw: int, vh: int, cw: int) -> tuple[np.ndarray, dict[str, float]]:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if vw <= 0 or vh <= 0:
            vw, vh = img.size
        scale = min(INPUT_SIZE / vw, INPUT_SIZE / vh)
        dw = max(1, round(vw * scale))
        dh = max(1, round(vh * scale))
        pad_x = (INPUT_SIZE - dw) // 2
        pad_y = (INPUT_SIZE - dh) // 2

        resized = img.resize((dw, dh), Image.Resampling.BILINEAR)
        canvas = Image.new("RGB", (INPUT_SIZE, INPUT_SIZE), (114, 114, 114))
        canvas.paste(resized, (pad_x, pad_y))

        arr = np.asarray(canvas, dtype=np.float32) / 255.0
        chw = np.transpose(arr, (2, 0, 1))
        tensor = np.expand_dims(chw, 0)
        lb = {"pad_x": pad_x, "pad_y": pad_y, "k": cw / dw if dw else 1.0}
        return tensor, lb

    def decode_yolo(self, output: np.ndarray, dims, lb: dict, cw: int, ch: int) -> dict[str, Any]:
        dims = list(dims)
        if len(dims) == 3 and dims[0] == 1:
            output = output[0]
            dims = dims[1:]
        boxes: list = []
        scores: list = []
        class_ids: list = []

        def to_canvas(x1, y1, x2, y2):
            k = lb["k"]
            return [
                clamp((x1 - lb["pad_x"]) * k, 0, cw),
                clamp((y1 - lb["pad_y"]) * k, 0, ch),
                clamp((x2 - lb["pad_x"]) * k, 0, cw),
                clamp((y2 - lb["pad_y"]) * k, 0, ch),
            ]

        if len(dims) == 2 and dims[1] == 6:
            n, _ = dims
            flat = output.reshape(n, 6)
        elif len(dims) == 3 and dims[2] == 6 and dims[1] <= 2000:
            n = dims[1]
            flat = output.reshape(n, 6)
        else:
            flat = None

        if flat is not None:
            max_coord = 0.0
            for row in flat:
                if row[4] > CONF_THRESHOLD:
                    max_coord = max(max_coord, float(row[0]), float(row[1]), float(row[2]), float(row[3]))
            u = INPUT_SIZE if max_coord > 0 and max_coord <= 1.5 else 1.0
            best_score = 0.0
            for row in flat:
                score = float(row[4])
                if score > best_score:
                    best_score = score
                if score <= CONF_THRESHOLD:
                    continue
                boxes.append(to_canvas(row[0] * u, row[1] * u, row[2] * u, row[3] * u))
                scores.append(score)
                class_ids.append(int(round(row[5])))
            return {
                "boxes": boxes,
                "scores": scores,
                "classIds": class_ids,
                "keep": list(range(len(boxes))),
                "bestScore": best_score,
                "closeness": {},
            }

        if len(dims) != 3:
            raise ValueError(f"unsupported output dims {dims}")

        channels_first = dims[1] < dims[2]
        num_attr = dims[1] if channels_first else dims[2]
        num_boxes = dims[2] if channels_first else dims[1]
        num_classes = num_attr - 4
        if num_classes < 1:
            raise ValueError(f"unsupported dims {dims}")

        data = output
        best_score = 0.0
        for i in range(num_boxes):
            if channels_first:
                max_score = 0.0
                best_class = -1
                for c in range(num_classes):
                    s = float(data[4 + c, i])
                    if s > max_score:
                        max_score = s
                        best_class = c
                cx, cy, w, h = float(data[0, i]), float(data[1, i]), float(data[2, i]), float(data[3, i])
            else:
                base = i * num_attr
                max_score = 0.0
                best_class = -1
                for c in range(num_classes):
                    s = float(data[base + 4 + c])
                    if s > max_score:
                        max_score = s
                        best_class = c
                cx = float(data[base])
                cy = float(data[base + 1])
                w = float(data[base + 2])
                h = float(data[base + 3])

            if max_score > best_score:
                best_score = max_score
            if max_score <= CONF_THRESHOLD:
                continue
            boxes.append(to_canvas(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
            scores.append(max_score)
            class_ids.append(best_class)

        keep = nms(boxes, scores, class_ids, IOU_THRESHOLD)
        return {
            "boxes": boxes,
            "scores": scores,
            "classIds": class_ids,
            "keep": keep,
            "bestScore": best_score,
            "closeness": {},
        }

    def compute_level(self, det: dict, cw: int, ch: int, state: ClientState) -> int:
        now = time.time() * 1000
        det["closeness"] = {}
        vehicles = 0
        max_close = -1.0
        for idx in det["keep"]:
            cid = det["classIds"][idx]
            if cid not in VEHICLE_CLASSES:
                continue
            x1, y1, x2, y2 = det["boxes"][idx]
            c = clamp(max((x2 - x1) / max(cw, 1), (y2 - y1) / max(ch, 1)), 0, 1)
            det["closeness"][idx] = c
            vehicles += 1
            max_close = max(max_close, c)

        if vehicles == 0:
            if now - state.vehicle_seen_at > VEHICLE_HOLD_MS:
                state.current_level = LEVEL_NONE
            return state.current_level

        state.vehicle_seen_at = now
        if state.current_level == LEVEL_NEAR:
            if max_close < NEAR_RATIO - NEAR_HYSTERESIS:
                state.current_level = LEVEL_FAR
        else:
            state.current_level = LEVEL_NEAR if max_close >= NEAR_RATIO else LEVEL_FAR
        return state.current_level

    def process_frame(self, jpeg: bytes, vw: int, vh: int, cw: int, ch: int, state: ClientState) -> dict[str, Any]:
        t0 = time.perf_counter()
        tensor, lb = self.letterbox(jpeg, vw, vh, cw)
        outputs = self.session.run([self.output_name], {self.input_name: tensor})
        yolo_ms = (time.perf_counter() - t0) * 1000
        out = outputs[0]
        det = self.decode_yolo(out, list(out.shape), lb, cw, ch)
        level = self.compute_level(det, cw, ch, state)
        closeness = {str(k): float(v) for k, v in (det.get("closeness") or {}).items()}
        return {
            "level": int(level),
            "bestScore": float(det.get("bestScore") or 0),
            "yoloMs": round(float(yolo_ms), 1),
            "det": {
                "boxes": [[float(x) for x in b] for b in det["boxes"]],
                "scores": [float(s) for s in det["scores"]],
                "classIds": [int(c) for c in det["classIds"]],
                "keep": [int(i) for i in det["keep"]],
                "closeness": closeness,
            },
        }
