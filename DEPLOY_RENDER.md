# Deploy FastAPI ไป Render

อ้างอิง: [Render CLI docs](https://render.com/docs/cli)

## 1. ติดตั้ง Render CLI (Windows)

```powershell
winget install render.cli
```

ปิดแล้วเปิดเทอร์มินัลใหม่ แล้วเช็ค:

```powershell
render --version
```

## 2. Login

```powershell
render login
```

เปิดเบราว์เซอร์ → **Authorize CLI** → เลือก workspace

## 3. ส่งโค้ดขึ้น Git (Render  deploy จาก Git)

```powershell
cd "E:\mini project"
git init
git add render.yaml runtime.txt requirements.txt server.py vision.py index.html model.onnx .gitignore
git commit -m "Add FastAPI vision server for Render"
```

สร้าง repo บน GitHub แล้ว:

```powershell
git remote add origin https://github.com/YangNobody12/object-detection.git
git branch -M main
git push -u origin main
```

> ต้องมี **`model.onnx`** ใน repo (~5 MB) ไม่งั้นเซิร์ฟเวอร์จะสตาร์ทไม่ได้

## 4. Deploy ด้วย Blueprint (`render.yaml`)

```powershell
cd "E:\mini project"
render blueprints validate render.yaml
```

จาก Dashboard: **New → Blueprint** → เลือก repo → Render อ่าน `render.yaml`

หรือหลัง push repo แล้วใน Dashboard: **New → Web Service** → Connect repo → ตั้งค่า:

| ช่อง | ค่า |
|------|-----|
| Runtime | Python |
| Build | `pip install --upgrade pip && pip install -r requirements.txt` |
| Start | `uvicorn server:app --host 0.0.0.0 --port $PORT` |
| Health check | `/health` |

## 5. หลัง deploy

- เปิด `https://YOUR-SERVICE.onrender.com/health` → ต้องได้ JSON `build: 2026-09-28-fastapi-yolo`
- เปิด `https://YOUR-SERVICE.onrender.com/` → หน้า `index.html` + กล้อง (ต้อง **https**)

### WebSocket บน Render

Free tier รองรับ WebSocket ได้ แต่ถ้า `/ws` ไม่ติด หน้าเว็บจะ fallback **HTTP** (`POST /api/predict`) อัตโนมัติ

บังคับ HTTP: `?transport=http`

## 6. คำสั่ง CLI ที่ใช้บ่อย

```powershell
render services -o json --confirm
render deploys create SERVICE_ID --confirm --wait
render logs SERVICE_ID --confirm
```

Non-interactive (CI): ตั้ง `RENDER_API_KEY` ตาม [docs](https://render.com/docs/cli#non-interactive-mode)
