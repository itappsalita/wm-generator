from fastapi import FastAPI, Request
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests
import uuid
import os
import json
import pickle
import logging

app = FastAPI()

BASE_DIR = "/var/www/wm-generator"
load_dotenv(os.path.join(BASE_DIR, ".env"))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
REQUESTS_DIR = os.path.join(BASE_DIR, "requests")
TOKEN_FILE = os.path.join(BASE_DIR, "token.pickle")

DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
APPSHEET_APP_ID = os.getenv("APPSHEET_APP_ID")
APPSHEET_API_KEY = os.getenv("APPSHEET_API_KEY")
APPSHEET_TABLE = os.getenv("APPSHEET_TABLE", "Sheet1")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(REQUESTS_DIR, exist_ok=True)


# ── Google Drive ──────────────────────────────────────────

def get_drive_service():
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)

    if creds.expired and creds.refresh_token:
        print("  Refreshing token...")
        creds.refresh(GoogleAuthRequest())
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path, drive_filename):
    service = get_drive_service()
    file_metadata = {"name": drive_filename, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name"
    ).execute()
    return file.get("id"), file.get("name")


# ── AppSheet API ──────────────────────────────────────────

def update_appsheet_row(row_id, updates, email):
    url = f"https://api.appsheet.com/api/v2/apps/{APPSHEET_APP_ID}/tables/{APPSHEET_TABLE}/Action"

    headers = {
        "ApplicationAccessKey": APPSHEET_API_KEY,
        "Content-Type": "application/json"
    }

    body = {
        "Action": "Edit",
        "Properties": {
            "Locale": "en-US",
            "RunAsUserEmail": email
        },
        "Rows": [
            {
                "id": row_id,
                **updates
            }
        ]
    }

    response = requests.post(url, headers=headers, json=body, timeout=30)
    return response.status_code, response.json()


# ── Watermark ─────────────────────────────────────────────

def get_font():
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            28
        )
    except:
        return ImageFont.load_default()


def add_watermark(input_file, output_file, project, date, latlong):
    image = Image.open(input_file)
    if image.mode != "RGB":
        image = image.convert("RGB")

    draw = ImageDraw.Draw(image)
    font = get_font()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    watermark_text = (
        f"Project : {project}\n"
        f"Date    : {date}\n"
        f"GPS     : {latlong}\n"
        f"Stamp   : {timestamp}"
    )

    bbox = draw.multiline_textbbox((0, 0), watermark_text, font=font)
    text_height = bbox[3] - bbox[1]
    padding = 20
    x = 20
    y = image.height - text_height - (padding * 2) - 20

    draw.multiline_text((x, y), watermark_text, fill=(255, 255, 255), font=font)

    image.save(output_file, quality=95)


# ── Routes ────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "status": "running",
        "service": "wm-generator"
    }


@app.get("/get")
def health_check():
    return {
        "status": "ok",
        "service": "wm-generator",
        "timestamp": datetime.now().isoformat()
    }


@app.post("/process-photo")
async def process_photo(request: Request):

    payload = await request.json()

    row_id = payload.get("id")
    project = payload.get("project", "-")
    date = payload.get("date", "-")
    latlong = payload.get("latlong", "")
    email = payload.get("email", "")

    print(f"\n{'='*50}")
    print(f"  Processing row: {row_id}")
    print(f"  ID      : {row_id}")
    print(f"  Project : {project}")
    print(f"  Date    : {date}")
    print(f"  LatLong : {latlong}")
    print(f"  Email   : {email}")
    print(f"{'='*50}\n")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    appsheet_updates = {}

    for key, value in payload.items():
        if not key.startswith("photo_"):
            continue
        if not value:
            print(f"  [{key}] empty — skip")
            continue

        if value.startswith("Sheet1_Images/WM_"):
            print(f"  [{key}] Already watermarked — skip")
        continue

        print(f"  [{key}] Downloading...")
        response = requests.get(value, timeout=60)

        if response.status_code != 200:
            print(f"  [{key}] Download failed: {response.status_code}")
            continue

        print(f"  [{key}] Downloaded {len(response.content)} bytes")

        filename_uuid = f"{uuid.uuid4()}.jpg"
        original_file = os.path.join(UPLOAD_DIR, filename_uuid)
        processed_file = os.path.join(PROCESSED_DIR, filename_uuid)

        with open(original_file, "wb") as f:
            f.write(response.content)

        print(f"  [{key}] Adding watermark...")
        add_watermark(original_file, processed_file, project, date, latlong)

        print(f"  [{key}] Uploading to Drive...")
        drive_filename = f"WM_{row_id}_{key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_id, uploaded_name = upload_to_drive(processed_file, drive_filename)

        appsheet_path = f"Sheet1_Images/{uploaded_name}"
        appsheet_updates[key] = appsheet_path

        print(f"  [{key}] Uploaded → {appsheet_path}")

    status = None
    result = None

    if appsheet_updates:
        print(f"\n  Updating AppSheet row {row_id}...")
        print(f"  Updates: {appsheet_updates}")
        status, result = update_appsheet_row(row_id, appsheet_updates, email)
        print(f"  AppSheet response: {status} → {result}")
    else:
        print("\n  No photos to update in AppSheet.")

    print("\nAll done!")

    return {
        "status": "success",
        "row_id": row_id,
        "appsheet_updates": appsheet_updates,
        "appsheet_status": status,
        "appsheet_result": result
    }


@app.post("/inspect")
async def inspect(request: Request):
    try:
        payload = await request.json()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_base = f"{timestamp}_{uuid.uuid4()}"
        json_path = os.path.join(REQUESTS_DIR, f"{file_base}.json")

        with open(json_path, "w", encoding="utf-8") as jf:
            json.dump(payload, jf, ensure_ascii=False, indent=2, default=str)

        return {
            "status": "received",
            "saved_to": json_path,
            "payload": payload
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=9000
    )