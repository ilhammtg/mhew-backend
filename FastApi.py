from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional, Any

# ... (Previous imports stay, ensure matching)

# Security Dependency
async def verify_api_key(x_api_key: str = Header(...)):
    # In real app, fetch from ENV. For now hardcode or simpler env check
    SERVER_API_KEY = os.getenv("API_KEY", "RAHASIA_KUNCI_API_ANDA") 
    if x_api_key != SERVER_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return x_api_key

# Pydantic Models
class ForecastItem(BaseModel):
    time: str
    temp: int
    desc: str

class WeatherLogData(BaseModel):
    temp: int
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

# ... (Existing code)

@app.post("/api/v1/weather/log", dependencies=[Depends(verify_api_key)])
async def log_weather(log: WeatherLog):
    if db is None: return {"error": "Database error"}
    try:
        # Convert pydantic to dict
        doc = log.dict()
        # Ensure timestamp is datetime if pydantic didn't handle it (it usually does)
        db.weather_logs.insert_one(doc)
        return {"status": "success", "id": str(doc.get("_id"))}
    except Exception as e:
        logger.error(f"Log Weather Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/storm/log", dependencies=[Depends(verify_api_key)])
async def log_storm(log: StormLog):
    if db is None: return {"error": "Database error"}
    try:
        doc = log.dict()
        db.storm_monitor.insert_one(doc)
        return {"status": "success", "id": str(doc.get("_id"))}
    except Exception as e:
        logger.error(f"Log Storm Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/cuaca/precip")
async def get_precip_status():
    if db is None: return {"error": "Database not connected"}
    try:
        locs = list(db.locations.find({}, {"_id": 1, "name": 1}))
        results = []
        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=24)
        
        for loc in locs:
            # Aggregation: Sum data.precip_mm for source=BMKG in last 24h
            pipeline = [
                {
                    "$match": {
                        "location_id": loc["_id"],
                        "timestamp": {"$gte": start_time},
                        "source": "BMKG"
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "total_precip": {"$sum": "$data.precip_mm"}
                    }
                }
            ]
            agg = list(db.weather_logs.aggregate(pipeline))
            total = float(agg[0]["total_precip"]) if agg else 0.0
            
            # Determine status
            status = "SAFE"
            if total > 150: status = "DANGER"
            elif total > 100: status = "WARNING"
            elif total > 50: status = "WARNING"

            # Get latest description for display
            latest = db.weather_logs.find_one(
                {"location_id": loc["_id"], "source": "BMKG"},
                sort=[("timestamp", -1)]
            )
            desc = "Berawan"
            if latest and "data" in latest:
                desc = latest["data"].get("weather_desc", "Berawan")

            results.append({
                "name": loc["name"],
                "total_precip_24h": total, # Frontend Point Forecast logic might use this
                "status": status,
                "desc": desc,
                "display_text": f"{desc}" # For QuickStats
            })
        return results
    except Exception as e:
        logger.error(f"Error in precip: {e}")
        return {"error": str(e)}

@app.get("/api/v1/cuaca/point-forecast")
async def get_point_forecast(location_id: Optional[str] = None):
    if db is None: return []
    try:
        # If location_id provided, filter. Else get latest for 'Aceh' default or all?
        # Let's return latest log for each location
        locs = list(db.locations.find({}, {"_id": 1, "name": 1}))
        data = []
        for loc in locs:
             latest = db.weather_logs.find_one(
                {"location_id": loc["_id"], "source": "BMKG"},
                sort=[("timestamp", -1)],
                projection={"_id": 0}
            )
             if latest:
                 latest["location_name"] = loc["name"]
                 data.append(latest)
        return data
    except Exception as e:
        return {"error": str(e)}

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
