from fastapi import FastAPI, Request
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import requests
import uuid
import os

app = FastAPI()

UPLOAD_DIR = "uploads"
PROCESSED_DIR = "processed"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(PROCESSED_DIR, exist_ok=True)


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


@app.post("/process-photo")
async def process_photo(request: Request):

    payload = await request.json()

    row_id = payload.get("id")
    project = payload.get("project", "-")
    date = payload.get("date", "-")
    latlong = payload.get("latlong", "-")

    processed_files = []

    for key, value in payload.items():

        if not key.startswith("photo_"):
            continue

        if not value:
            continue

        try:

            response = requests.get(
                value,
                timeout=60
            )

            if response.status_code != 200:
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

            processed_files.append({
                "field": key,
                "file": processed_file
            })

        except Exception as e:
            print(
                f"Failed processing {key}: {str(e)}"
            )

    return {
        "status": "success",
        "row_id": row_id,
        "processed": len(processed_files),
        "files": processed_files
    }
@app.post("/inspect")
async def inspect(request: Request):
    payload = await request.json()

    print("=" * 50)
    print("INCOMING PAYLOAD FROM APPSHEET BOT")
    print("=" * 50)
    for key, value in payload.items():
        print(f"  {key:<12} : {value}")
    print("=" * 50)

    return {
        "status": "received",
        "total_fields": len(payload),
        "payload": payload
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000
    )