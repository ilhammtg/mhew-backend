from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
import os
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

load_dotenv()
app = FastAPI()

# Menggunakan ["*"] lebih aman saat proses awal deployment untuk menghindari CORS error
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_methods=["*"], 
    allow_headers=["*"]
)

client = MongoClient(os.getenv("MONGO_URI"))
db = client["emergency_db"]

@app.get("/api/v1/gempa/terkini")
async def get_gempa():
    return db.alerts.find_one(sort=[("DateTime", -1)], projection={"_id": 0})

@app.get("/api/v1/gempa/aceh")
async def get_history():
    return list(db.alerts.find({"is_aceh": True}, {"_id": 0}).sort("DateTime", -1).limit(10))

@app.get("/api/v1/cuaca/aceh")
async def get_cuaca():
    return list(db.weather_alerts.find({}, {"_id": 0}).sort("date", -1).limit(5))

@app.get("/api/v1/iot/trigger")
async def iot():
    latest = db.alerts.find_one(sort=[("DateTime", -1)])
    return {"trigger": latest.get("alert_level") == "DANGER" if latest else False}

@app.get("/api/v1/cuaca/precip")
async def get_precip_status():
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
        
        # PERBAIKAN: total_raw langsung digunakan sebagai akumulasi mm
        total = float(agg[0]["total_precip"]) if agg else 0.0
        
        # Logika Status berdasarkan acuan 150mm/hari
        status = "SAFE"
        if total > 150: status = "DANGER"
        elif total > 100: status = "WARNING"
        
        results.append({
            "name": loc["name"],
            "total_precip_24h": round(total, 2), # Dibulatkan agar rapi di UI
            "status": status
        })
    return results

@app.get("/api/v1/cuaca/forecast")
async def get_forecast():
    log = db.weather_logs.find_one(
        {"forecast_raw": {"$exists": True}},
        sort=[("timestamp", -1)],
        projection={"_id": 0, "forecast_raw": 1, "location_name": 1, "timestamp": 1}
    )
    if log:
        return log
    return {"error": "No forecast data available"}