from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta
import logging

# Load environment variables
load_dotenv()

app = FastAPI()

# Logging untuk mempermudah debug di Railway
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# CORS - Menggunakan ["*"] sangat disarankan untuk tahap awal deploy
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# --- PERBAIKAN KONEKSI DATABASE ---
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    logger.error("❌ MONGO_URI tidak ditemukan di Environment Variables!")
    # Di Railway, ini akan menyebabkan log error yang jelas
    client = None
    db = None
else:
    try:
        # tlsAllowInvalidCertificates=True membantu menghindari error SSL di beberapa server cloud
        # serverSelectionTimeoutMS agar tidak menunggu terlalu lama jika koneksi gagal
        client = MongoClient(
            MONGO_URI, 
            tlsAllowInvalidCertificates=True,
            serverSelectionTimeoutMS=5000 
        )
        db = client["emergency_db"]
        # Tes koneksi singkat
        client.admin.command('ping')
        logger.info("✅ Berhasil terhubung ke MongoDB Atlas")
    except Exception as e:
        logger.error(f"❌ Gagal terhubung ke MongoDB: {e}")
        db = None

@app.get("/api/v1/gempa/terkini")
async def get_gempa():
    if db is None: return {"error": "Database not connected"}
    return db.alerts.find_one(sort=[("DateTime", -1)], projection={"_id": 0})

@app.get("/api/v1/gempa/aceh")
async def get_history():
    if db is None: return []
    return list(db.alerts.find({"is_aceh": True}, {"_id": 0}).sort("DateTime", -1).limit(10))

@app.get("/api/v1/cuaca/aceh")
async def get_cuaca():
    if db is None: return []
    return list(db.weather_alerts.find({}, {"_id": 0}).sort("date", -1).limit(5))

@app.get("/api/v1/iot/trigger")
async def iot():
    if db is None: return {"trigger": False}
    latest = db.alerts.find_one(sort=[("DateTime", -1)])
    return {"trigger": latest.get("alert_level") == "DANGER" if latest else False}

@app.get("/api/v1/cuaca/precip")
async def get_precip_status():
    if db is None: return {"error": "Database not connected"}
    try:
        locs = list(db.locations.find({}, {"_id": 1, "name": 1}))
        results = []
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=24)
        
        for loc in locs:
            pipeline = [
                {
                    "$match": {
                        "location_id": loc["_id"],
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
            agg = list(db.weather_logs.aggregate(pipeline))
            total = float(agg[0]["total_precip"]) if agg else 0.0
            
            status = "SAFE"
            if total > 150: status = "DANGER"
            elif total > 100: status = "WARNING"
            
            results.append({
                "name": loc["name"],
                "total_precip_24h": round(total, 2),
                "status": status
            })
        return results
    except Exception as e:
        logger.error(f"Error in precip: {e}")
        return {"error": str(e)}

@app.get("/api/v1/cuaca/forecast")
async def get_forecast():
    if db is None: return {"error": "Database not connected"}
    log = db.weather_logs.find_one(
        {"forecast_raw": {"$exists": True}},
        sort=[("timestamp", -1)],
        projection={"_id": 0, "forecast_raw": 1, "location_name": 1, "timestamp": 1}
    )
    if log:
        return log
    return {"error": "No forecast data available"}