import re
import math
from datetime import datetime, timezone, timedelta
from .database import col_weather_logs

def normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s

def get_alert_level(potensi: str):
    p = (potensi or "").lower()
    if "potensi tsunami" in p or "awas" in p:
        return {"level": "DANGER", "emoji": "🔴", "label": "BAHAYA: POTENSI TSUNAMI"}
    if "waspada" in p or "siaga" in p:
        return {"level": "WARNING", "emoji": "🟠", "label": "WASPADA"}
    return {"level": "SAFE", "emoji": "🟢", "label": "AMAN"}

def format_ts_ms(ts_ms: int) -> str:
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return str(ts_ms)

def _first(arr):
    return arr[0] if isinstance(arr, list) and arr else None

def parse_windy_latest(windy_json: dict):
    """
    Ambil time-slice index 0 dari response.
    Hitung wind speed dari u/v.
    """
    if not windy_json or "ts" not in windy_json:
        return None

    ts_list = windy_json.get("ts") or []
    if not isinstance(ts_list, list) or not ts_list:
        return None

    i = 0
    ts = ts_list[i]

    # Wind: vector u/v
    u = _first(windy_json.get("wind_u-surface"))
    v = _first(windy_json.get("wind_v-surface"))
    wind_speed = None
    wind_dir_deg = None
    if u is not None and v is not None:
        wind_speed = float(math.sqrt(u*u + v*v))
        # arah meteorologi sederhana (opsional). Kita simpan saja deg dari arctan2.
        wind_dir_deg = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0

    gust = _first(windy_json.get("gust-surface"))
    temp = _first(windy_json.get("temp-surface"))
    rh = _first(windy_json.get("rh-surface"))
    pressure = _first(windy_json.get("pressure-surface"))

    # precip -> past3hprecip-surface (akumulasi 3 jam terakhir)
    precip3h = _first(windy_json.get("past3hprecip-surface"))

    lclouds = _first(windy_json.get("lclouds-surface"))
    mclouds = _first(windy_json.get("mclouds-surface"))
    hclouds = _first(windy_json.get("hclouds-surface"))

    # total cloud cover pendekatan (rata-rata)
    cloud_avg = None
    clouds_vals = [x for x in [lclouds, mclouds, hclouds] if x is not None]
    if clouds_vals:
        cloud_avg = float(sum(clouds_vals) / len(clouds_vals))

    return {
        "ts": ts,
        "wind_speed_ms": wind_speed,
        "wind_dir_deg": wind_dir_deg,
        "gust_ms": float(gust) if gust is not None else None,
        "temp_c": float(temp) if temp is not None else None,
        "rh_pct": float(rh) if rh is not None else None,
        "pressure_pa": float(pressure) if pressure is not None else None,
        "precip_3h_mm": float(precip3h) if precip3h is not None else None,
        "lclouds_pct": float(lclouds) if lclouds is not None else None,
        "mclouds_pct": float(mclouds) if mclouds is not None else None,
        "hclouds_pct": float(hclouds) if hclouds is not None else None,
        "cloud_avg_pct": cloud_avg,
    }

def calculate_24h_precipitation(location_id: str) -> float:
    """
    Menghitung total curah hujan (precip_3h_mm) dalam 24 jam terakhir.
    Catatan: Karena log diambil setiap jam dan data adalah 'past 3h',
    penjumlahan langsung mungkin akan overlap. Namun sesuai instruksi,
    kita jumlahkan nilai yang ada di log.
    """
    now = datetime.now(timezone.utc)
    start_time = now - timedelta(hours=24)
    
    pipeline = [
        {
            "$match": {
                "location_id": location_id,
                "timestamp": {"$gte": start_time}
            }
        },
        {
            "$group": {
                "_id": None,
                "total_precip": {"$sum": "$latest.precip_3h_mm"}
            }
        }
    ]
    
    result = list(col_weather_logs.aggregate(pipeline))
    if result:
        # Karena data adalah 'past 3h' dan kita log setiap jam,
        # maka penjumlahan langsung akan overcount ~3x.
        # Kita bagi 3 untuk estimasi yang lebih mendekati.
        total_raw = float(result[0].get("total_precip", 0.0))
        return total_raw / 3.0
    return 0.0

def get_bmkg_weather_text(code: str) -> str:
    # Kode Cuaca BMKG: https://data.bmkg.go.id/prakiraan-cuaca/
    codes = {
        "0": "Cerah",
        "1": "Cerah Berawan",
        "2": "Cerah Berawan",
        "3": "Berawan", 
        "4": "Berawan Tebal", 
        "5": "Udara Kabur",
        "10": "Asap",
        "45": "Kabut",
        "60": "Hujan Ringan",
        "61": "Hujan Sedang",
        "63": "Hujan Lebat",
        "80": "Hujan Lokal",
        "95": "Hujan Petir",
        "97": "Hujan Petir"
    }
    return codes.get(str(code), "Berawan")

def get_weather_score(weather_text: str) -> int:
    """
    Mengembalikan skor bahaya berdasarkan teks cuaca (0-100).
    """
    text = weather_text.lower()
    if "petir" in text or "lebat" in text:
        return 100 # BAHAYA
    if "sedang" in text:
        return 75 # WASPADA
    if "ringan" in text or "lokal" in text:
        return 50 # SIAGA
    return 0 # AMAN

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371  # Radius bumi (km)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) * math.sin(dlat / 2) + \
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(dlon / 2) * math.sin(dlon / 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
