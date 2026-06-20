from fastapi import FastAPI, Request
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests
import uuid
import os
import json
import logging

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
REQUESTS_DIR = os.path.join(BASE_DIR, "requests")
SERVICE_ACCOUNT_FILE = os.path.join(BASE_DIR, "service-account.json")

DRIVE_FOLDER_ID = "1anh13991wmHoArOKZNEuUI-JnkJEx8Qu"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)
os.makedirs(REQUESTS_DIR, exist_ok=True)

# in-memory tracker to skip already processed photos
processed_tracker = {}


# ── Google Drive ──────────────────────────────────────────

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path: str, filename: str, folder_id: str) -> dict:
    service = get_drive_service()

    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }

    media = MediaFileUpload(file_path, mimetype="image/jpeg")

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()

    return {
        "file_id": file.get("id"),
        "drive_link": file.get("webViewLink")
    }


# ── Watermark ─────────────────────────────────────────────

def get_font():
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            28
        )
    except:
        return ImageFont.load_default()


def add_watermark(
    input_file,
    output_file,
    project,
    date,
    latlong
):
    image = Image.open(input_file)

    if image.mode != "RGB":
        image = image.convert("RGB")

    draw = ImageDraw.Draw(image)
    font = get_font()

    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    watermark_text = (
        f"Project : {project}\n"
        f"Date    : {date}\n"
        f"GPS     : {latlong}\n"
        f"Stamp   : {timestamp}"
    )

    bbox = draw.multiline_textbbox(
        (0, 0),
        watermark_text,
        font=font
    )

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    padding = 20

    x = 20
    y = image.height - text_height - (padding * 2) - 20

    draw.rectangle(
        (
            x - padding,
            y - padding,
            x + text_width + padding,
            y + text_height + padding
        ),
        fill=(0, 0, 0)
    )

    draw.multiline_text(
        (x, y),
        watermark_text,
        fill=(255, 255, 255),
        font=font
    )

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
    latlong = payload.get("latlong", "-")

    if row_id not in processed_tracker:
        processed_tracker[row_id] = {}

    processed_files = []
    skipped = []
    failed = []

    for key, value in payload.items():

        if not key.startswith("photo_"):
            continue

        if not value:
            continue

        # skip if same photo already processed
        if processed_tracker[row_id].get(key) == value:
            skipped.append(key)
            continue

        try:

            response = requests.get(
                value,
                timeout=60
            )

            if response.status_code != 200:
                failed.append({"field": key, "reason": f"download failed {response.status_code}"})
                continue

            filename = f"{uuid.uuid4()}.jpg"

            original_file = os.path.join(
                UPLOAD_DIR,
                filename
            )

            processed_file = os.path.join(
                PROCESSED_DIR,
                filename
            )

            with open(original_file, "wb") as f:
                f.write(response.content)

            add_watermark(
                original_file,
                processed_file,
                project,
                date,
                latlong
            )

            drive_filename = f"WM_{row_id}_{key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            drive_result = upload_to_drive(processed_file, drive_filename, DRIVE_FOLDER_ID)

            # mark as processed
            processed_tracker[row_id][key] = value

            processed_files.append({
                "field": key,
                "drive_file_id": drive_result["file_id"],
                "drive_link": drive_result["drive_link"]
            })

        except Exception as e:
            print(f"Failed processing {key}: {str(e)}")
            failed.append({"field": key, "reason": str(e)})

    return {
        "status": "success",
        "row_id": row_id,
        "processed": len(processed_files),
        "skipped": skipped,
        "failed": failed,
        "files": processed_files
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