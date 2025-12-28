from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pydantic import BaseModel
from typing import List, Optional, Any
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import logging

# Load environment variables
load_dotenv()

app = FastAPI(title="MHEWS Aceh API", version="2.5.0")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- KONFIGURASI CORS (DISESUAIKAN UNTUK KEAMANAN HEADER) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["X-API-KEY", "Content-Type", "Authorization"],
)

# --- KONEKSI DATABASE ---
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI, tlsAllowInvalidCertificates=True, serverSelectionTimeoutMS=5000)
db = client["emergency_db"]

# --- SECURITY DEPENDENCY ---
async def verify_api_key(x_api_key: str = Header(None)):
    SERVER_API_KEY = os.getenv("API_KEY", "RAHASIA_KUNCI_API_ANDA") 
    if not x_api_key or x_api_key != SERVER_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")
    return x_api_key

# --- PYDANTIC MODELS (VALIDASI DATA) ---
class ForecastItem(BaseModel):
    time: str
    temp: int
    desc: str
    humidity: int
    wind_speed: float
    precip: float
    precip_mm: Optional[float] = None
    humidity: Optional[int] = None
    wind_speed: Optional[float] = None

class AutoDetectRequest(BaseModel):
    lat: float
    lon: float

class WeatherLogData(BaseModel):
    temp: float
    humidity: int
    weather_desc: str
    precip_mm: float
    wind_speed: float

class WeatherLog(BaseModel):
    location_id: str
    timestamp: datetime
    source: str = "BMKG"
    data: WeatherLogData
    forecast_3h: List[ForecastItem]

class StormLog(BaseModel):
    location_id: str
    last_check: datetime
    source: str = "Windy"
    parameters: dict
    is_alert: bool
    alert_message: Optional[str] = None

# --- ENDPOINTS POST (INPUT DATA DENGAN PROTEKSI API KEY) ---

@app.post("/api/v1/weather/log", dependencies=[Depends(verify_api_key)])
async def log_weather(log: WeatherLog):
    try:
        doc = log.dict()
        db.weather_logs.insert_one(doc)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/storm/log", dependencies=[Depends(verify_api_key)])
async def log_storm(log: StormLog):
    try:
        doc = log.dict()
        db.storm_monitor.insert_one(doc)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINTS GET (KONSUMSI DATA FRONTEND) ---

@app.get("/api/v1/gempa/terkini")
async def get_gempa():
    # Mengambil gempa terbaru
    data = db.alerts.find_one(sort=[("DateTime", -1)], projection={"_id": 0})
    return data if data else {"error": "No data"}

@app.get("/api/v1/gempa/aceh")
async def get_history():
    # History 10 gempa terakhir di Aceh
    return list(db.alerts.find({"is_aceh": True}, {"_id": 0}).sort("DateTime", -1).limit(10))

@app.get("/api/v1/cuaca/precip")
async def get_precip_status():
    try:
        locs = list(db.locations.find({}, {"_id": 1, "name": 1}))
        results = []
        start_time = datetime.now(timezone.utc) - timedelta(hours=24)
        
        for loc in locs:
            # Agregasi jumlah hujan 24 jam terakhir dari BMKG
            pipeline = [
                {"$match": {"location_id": loc["_id"], "timestamp": {"$gte": start_time}, "source": "BMKG"}},
                {"$group": {"_id": None, "total_precip": {"$sum": "$data.precip_mm"}}}
            ]
            agg = list(db.weather_logs.aggregate(pipeline))
            total = float(agg[0]["total_precip"]) if agg else 0.0
            
            # Kategori banjir sesuai standar BMKG
            if total > 150: status = "DANGER"
            elif total > 100: status = "WARNING"
            elif total > 50: status = "WASPADA"
            else: status = "SAFE"

            # Ambil deskripsi cuaca terakhir
            latest = db.weather_logs.find_one({"location_id": loc["_id"]}, sort=[("timestamp", -1)])
            desc = latest["data"]["weather_desc"] if latest else "Berawan"

            results.append({
                "name": loc["name"],
                "total_precip_24h": round(total, 2),
                "status": status,
                "desc": desc
            })
        return results
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/v1/cuaca/point-forecast")
async def get_point_forecast():
    try:
        # Mengambil ramalan cuaca terbaru untuk semua lokasi
        locs = list(db.locations.find({}, {"_id": 1, "name": 1, "coordinates": 1}))
        data = []
        for loc in locs:
             latest = db.weather_logs.find_one(
                {"location_id": loc["_id"], "source": "BMKG"},
                sort=[("timestamp", -1)],
                projection={"_id": 0}
            )
             if latest:
                 latest["location_name"] = loc["name"]
                 latest["coords"] = loc["coordinates"]
                 data.append(latest)
        return data
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/v1/iot/trigger")
async def iot_trigger():
    latest = db.alerts.find_one(sort=[("DateTime", -1)])
    # Trigger sirene jika gempa berpotensi tsunami
    trigger = False
    if latest and "Potensi" in latest:
        if "tsunami" in latest["Potensi"].lower():
            trigger = True
    return {"trigger": trigger}

@app.post("/api/v1/auto-detect", dependencies=[Depends(verify_api_key)])
async def auto_detect_location(req: AutoDetectRequest):
    """
    1. Reverse Geocode (Lat/Lon -> Village Name)
    2. DB Lookup (Village Name -> ADM4 Code)
    3. Return details
    """
    from bot_modules.services import reverse_geocode
    from bot_modules.utils import normalize_name
    
    try:
        # 1. Reverse Geocode
        geo = await reverse_geocode(req.lat, req.lon)
        if not geo:
            # Fallback invalid
            raise HTTPException(status_code=404, detail="Location address not found")
        
        village_clean = normalize_name(geo["village"]).replace("KELURAHAN ", "").replace("DESA ", "").strip()
        
        # 2. DB Lookup in wilayah_bmkg
        # Try finding exact name first in wilayah_bmkg
        wilayah = db.wilayah_bmkg.find_one({"name": village_clean})
        
        # Fallback text search if needed
        if not wilayah:
             # Try regex for partial match
             wilayah = db.wilayah_bmkg.find_one({"name": {"$regex": f"^{village_clean}", "$options": "i"}})
        
        if not wilayah:
             return {
                "found": False,
                "geo_name": geo["village"],
                "fallback_message": "Wilayah tidak ada di database BMKG"
            }

        return {
            "found": True,
            "adm4": wilayah["_id"],
            "name": wilayah["name"],
            "level": wilayah["level"],
            "geo_detail": geo
        }

    except Exception as e:
        print(f"Auto-Detect Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))