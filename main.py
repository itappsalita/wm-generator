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


# ── Coordinate Conversion ─────────────────────────────────

def decimal_to_dms(decimal_deg, is_lat):
    """Convert decimal degrees to DMS string (e.g. 6° 18' 19.566'' S)"""
    is_negative = decimal_deg < 0
    decimal_deg = abs(decimal_deg)

    degrees = int(decimal_deg)
    minutes_float = (decimal_deg - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60

    if is_lat:
        direction = "S" if is_negative else "N"
    else:
        direction = "W" if is_negative else "E"

    return f"{degrees}° {minutes}' {seconds:.4f}'' {direction}"


def parse_latlong(latlong_str):
    """
    Parse '-6.305435, 106.853183' → '6° 18' 19.566'' S, 106° 51' 11.4588'' E'
    Returns original string if parsing fails.
    """
    try:
        parts = latlong_str.strip().split(",")
        if len(parts) != 2:
            return latlong_str
        lat = float(parts[0].strip())
        lng = float(parts[1].strip())
        return f"{decimal_to_dms(lat, is_lat=True)}, {decimal_to_dms(lng, is_lat=False)}"
    except Exception:
        return latlong_str


# ── Watermark ─────────────────────────────────────────────

def get_font():
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            20          # ← reduced from 28
        )
    except:
        return ImageFont.load_default()


def add_watermark(input_file, output_file, site_id, site_name, latlong, date):
    image = Image.open(input_file)
    if image.mode != "RGB":
        image = image.convert("RGB")

    draw = ImageDraw.Draw(image)
    font = get_font()

    dms_latlong = parse_latlong(latlong)

    watermark_text = (
        f"{site_id} - {site_name}\n"
        f"{dms_latlong}\n"
        f"{date}"
    )

    bbox = draw.multiline_textbbox((0, 0), watermark_text, font=font)
    text_width  = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    padding = 20

    # Bottom-right corner
    x = image.width  - text_width  - padding
    y = image.height - text_height - padding

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

    row_id    = payload.get("id")
    date      = payload.get("date", "-")
    latlong   = payload.get("latlong", "")
    email     = payload.get("email", "")

    # Near End & Far End site info
    ne_site_id   = payload.get("SITE ID NEAR END", "-")
    ne_site_name = payload.get("SITE NAME NEAR END", "-")
    fe_site_id   = payload.get("SITE ID FAR END", "-")
    fe_site_name = payload.get("SITE NAME FAR END", "-")

    print(f"\n{'='*50}")
    print(f"  Processing row : {row_id}")
    print(f"  NE Site        : {ne_site_id} / {ne_site_name}")
    print(f"  FE Site        : {fe_site_id} / {fe_site_name}")
    print(f"  Date           : {date}")
    print(f"  LatLong        : {latlong}")
    print(f"  Email          : {email}")
    print(f"{'='*50}\n")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    appsheet_updates = {}
    PHOTO_KEYWORDS = ["Picture", "PHOTO", "Team On-site", "SITE NEAR END PHOTO", "SITE FAR END PHOTO"]
    for key, value in payload.items():
        # Only process keys that contain "Picture" (photo columns)
        if not any(keyword in key for keyword in PHOTO_KEYWORDS):
            continue
        if not value:
            print(f"  [{key}] empty — skip")
            continue
        if "GENERATEDWM_" in str(value):
            print(f"  [{key}] Already watermarked — skip")
            continue

        # Determine Near End or Far End based on key name
        key_lower = key.lower()
        if "near end" in key_lower or key_lower.endswith(" ne") or " ne " in key_lower:
            site_id   = ne_site_id
            site_name = ne_site_name
            end_label = "NE"
        else:
            site_id   = fe_site_id
            site_name = fe_site_name
            end_label = "FE"

        print(f"  [{end_label}] [{key}] Downloading...")
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
        add_watermark(
            original_file,
            processed_file,
            site_id=site_id,
            site_name=site_name,
            latlong=latlong,
            date=date
        )

        print(f"  [{key}] Uploading to Drive...")
        safe_key = key.replace(" ", "_").replace("/", "-")[:40]
        drive_filename = f"GENERATEDWM_{row_id}_{end_label}_{safe_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_id, uploaded_name = upload_to_drive(processed_file, drive_filename)

        appsheet_path = f"LIST_Images/{uploaded_name}"
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
    uvicorn.run(app, host="0.0.0.0", port=9000)