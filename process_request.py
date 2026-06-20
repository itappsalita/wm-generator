import json
import os
import sys
import uuid
import pickle
import requests
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ── Config ────────────────────────────────────────────────

BASE_DIR = "/var/www/wm-generator"
REQUESTS_DIR = os.path.join(BASE_DIR, "requests")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
TOKEN_FILE = os.path.join(BASE_DIR, "token.pickle")
DRIVE_FOLDER_ID = "1anh13991wmHoArOKZNEuUI-JnkJEx8Qu"

# ── Drive ─────────────────────────────────────────────────

def get_drive_service():
    with open(TOKEN_FILE, "rb") as f:
        creds = pickle.load(f)

    # auto refresh if expired
    if creds.expired and creds.refresh_token:
        print("  Refreshing token...")
        creds.refresh(Request())
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("drive", "v3", credentials=creds)


def upload_to_drive(file_path, filename):
    service = get_drive_service()
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink"
    ).execute()
    return file.get("id"), file.get("webViewLink")


# ── Watermark ─────────────────────────────────────────────

def get_font():
    try:
        return ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28
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

    # removed draw.rectangle here

    draw.multiline_text((x, y), watermark_text, fill=(255, 255, 255), font=font)

    image.save(output_file, quality=95)


# ── Main ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python process_request.py <filename>")
        print("Example: python process_request.py 20260620_194756_7b29ff86.json")
        sys.exit(1)

    filename = sys.argv[1]

    if not os.path.isabs(filename):
        filepath = os.path.join(REQUESTS_DIR, filename)
    else:
        filepath = filename

    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    with open(filepath, "r") as f:
        payload = json.load(f)

    row_id = payload.get("id")
    project = payload.get("project", "-")
    date = payload.get("date", "-")
    latlong = payload.get("latlong", "-")

    print(f"\n{'='*50}")
    print(f"  Processing: {filename}")
    print(f"  ID      : {row_id}")
    print(f"  Project : {project}")
    print(f"  Date    : {date}")
    print(f"  LatLong : {latlong}")
    print(f"{'='*50}\n")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    for key, value in payload.items():
        if not key.startswith("photo_"):
            continue
        if not value:
            print(f"  [{key}] empty — skip")
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
        file_id, drive_link = upload_to_drive(processed_file, drive_filename)

        print(f"  [{key}] Done!")
        print(f"         Drive: {drive_link}\n")

    print("All done!")


if __name__ == "__main__":
    main()