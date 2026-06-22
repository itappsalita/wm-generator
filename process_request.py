import json
import os
import sys
import uuid
import pickle
import requests
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── Config ────────────────────────────────────────────────

BASE_DIR = "/var/www/wm-generator"
load_dotenv(os.path.join(BASE_DIR, ".env"))

REQUESTS_DIR = os.path.join(BASE_DIR, "requests")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
TOKEN_FILE = os.path.join(BASE_DIR, "token.pickle")

DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
APPSHEET_APP_ID = os.getenv("APPSHEET_APP_ID")
APPSHEET_API_KEY = os.getenv("APPSHEET_API_KEY")
APPSHEET_TABLE = os.getenv("APPSHEET_TABLE", "Sheet1")

# ── Drive ─────────────────────────────────────────────────

def get_drive_service():
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)

    if creds.expired and creds.refresh_token:
        print("  Refreshing token...")
        creds.refresh(Request())
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

    print(f"\n  AppSheet URL   : {url}")
    print(f"  AppSheet Body  : {json.dumps(body, indent=2)}")

    response = requests.post(url, headers=headers, json=body, timeout=30)

    print(f"\n  AppSheet Status: {response.status_code}")
    print(f"  AppSheet Raw   : {response.text}")

    return response.status_code, response.json()


# ── Coordinate Conversion ─────────────────────────────────

def decimal_to_dms(decimal_deg, is_lat):
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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20
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

    x = image.width  - text_width  - padding
    y = image.height - text_height - padding

    draw.multiline_text((x, y), watermark_text, fill=(255, 255, 255), font=font)
    image.save(output_file, quality=95)


# ── Main ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python process_request.py <filename>")
        print("Example: python process_request.py 20260620_194756_7b29ff86.json")
        sys.exit(1)

    filename = sys.argv[1]
    filepath = filename if os.path.isabs(filename) else os.path.join(REQUESTS_DIR, filename)

    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    with open(filepath, "r") as f:
        payload = json.load(f)

    row_id      = payload.get("id")
    date        = payload.get("date", "-")
    latlong     = payload.get("latlong", "")
    email       = payload.get("email", "")
    ne_site_id   = payload.get("SITE ID NEAR END", "-")
    ne_site_name = payload.get("SITE NAME NEAR END", "-")
    fe_site_id   = payload.get("SITE ID FAR END", "-")
    fe_site_name = payload.get("SITE NAME FAR END", "-")

    print(f"\n{'='*50}")
    print(f"  Processing : {filename}")
    print(f"  Row ID     : {row_id}")
    print(f"  NE Site    : {ne_site_id} / {ne_site_name}")
    print(f"  FE Site    : {fe_site_id} / {fe_site_name}")
    print(f"  Date       : {date}")
    print(f"  LatLong    : {latlong}")
    print(f"  Email      : {email}")
    print(f"{'='*50}\n")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    appsheet_updates = {}
    PHOTO_KEYWORDS = ["Picture", "PHOTO", "Team On-site", "SITE NEAR END PHOTO", "SITE FAR END PHOTO"]

    for key, value in payload.items():
        if not any(keyword in key for keyword in PHOTO_KEYWORDS):
            continue
        if not value:
            print(f"  [{key}] empty — skip")
            continue
        if "GENERATEDWM_" in str(value):
            print(f"  [{key}] Already watermarked — skip")
            continue

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

    if appsheet_updates:
        print(f"\n  Updating AppSheet row {row_id}...")
        print(f"  Updates: {json.dumps(appsheet_updates, indent=2)}")
        status, result = update_appsheet_row(row_id, appsheet_updates, email)
        print(f"\n  Final result: {status} → {result}")
    else:
        print("\n  No photos to update in AppSheet.")

    print("\nAll done!")


if __name__ == "__main__":
    main()