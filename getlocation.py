import requests
import json


def get_location_name(latlong: str) -> dict:
    if not latlong or "," not in latlong:
        return {"error": "Invalid input. Use format: lat, lon"}

    try:
        lat, lon = [x.strip() for x in latlong.split(",", 1)]

        response = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "zoom": 18,        # 18 = detail maksimal (nama jalan)
                "addressdetails": 1,
            },
            headers={"User-Agent": "getLocation/1.0"},
            timeout=10
        )

        response.raise_for_status()
        data = response.json()

        if "error" in data:
            return {"error": data["error"]}

        address = data.get("address", {})

        jalan     = address.get("road") or address.get("pedestrian") or address.get("path") or ""
        kelurahan = address.get("suburb") or address.get("village") or address.get("hamlet") or ""
        kecamatan = address.get("city_district") or address.get("district") or address.get("town") or ""
        kabupaten = address.get("city") or address.get("county") or address.get("regency") or ""
        provinsi  = address.get("state") or ""
        negara    = address.get("country") or ""

        parts = [p for p in [jalan, kelurahan, kecamatan, kabupaten, provinsi, negara] if p]
        readable = ", ".join(parts)

        return {
            "input":      latlong,
            "latitude":   lat,
            "longitude":  lon,
            "location":   readable,
            "jalan":      jalan,
            "kelurahan":  kelurahan,
            "kecamatan":  kecamatan,
            "kabupaten":  kabupaten,
            "provinsi":   provinsi,
            "negara":     negara,
            "display_name": data.get("display_name", ""),
            "raw_address":  address,   # kalau mau lihat semua field mentahnya
        }

    except requests.exceptions.Timeout:
        return {"error": "Request timeout. Cek koneksi internet."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Network error: {e}"}
    except Exception as e:
        return {"error": f"Error: {e}"}


def main():
    # ── Ganti koordinat di sini ───────────────────────────
    latlong = "-6.303334, 106.837142"
    # ─────────────────────────────────────────────────────

    print(f"\nLooking up: {latlong}")
    print("-" * 50)

    result = get_location_name(latlong)

    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"Jalan     : {result['jalan']}")
    print(f"Kelurahan : {result['kelurahan']}")
    print(f"Kecamatan : {result['kecamatan']}")
    print(f"Kabupaten : {result['kabupaten']}")
    print(f"Provinsi  : {result['provinsi']}")
    print(f"Negara    : {result['negara']}")
    print(f"\nLengkap   : {result['location']}")
    print(f"\n--- Raw Address Fields ---")
    print(json.dumps(result["raw_address"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()