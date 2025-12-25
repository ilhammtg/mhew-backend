import requests
import pymongo
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

client = pymongo.MongoClient(os.getenv("MONGO_URI"))
db = client["emergency_db"]

def test_precip_calculation():
    print("🧪 Testing Precipitation Calculation...")
    
    # 1. Setup Mock Location
    loc_id = "TEST_LOC_001"
    db.locations.update_one(
        {"_id": loc_id},
        {"$set": {"name": "Test Location", "lat": 5.55, "lon": 95.32}},
        upsert=True
    )
    
    # 2. Insert Mock Logs (3 hours of heavy rain)
    # Each log says "past 3h precip was 60mm".
    # If we sum them up blindly: 60 + 60 + 60 = 180mm (DANGER)
    # If we divide by 3: 180 / 3 = 60mm (SAFE)
    
    now = datetime.now(timezone.utc)
    logs = []
    for i in range(3):
        logs.append({
            "location_id": loc_id,
            "timestamp": now - timedelta(hours=i),
            "latest": {"precip_3h_mm": 60.0}
        })
    
    db.weather_logs.delete_many({"location_id": loc_id})
    db.weather_logs.insert_many(logs)
    
    # 3. Call API
    try:
        res = requests.get("http://127.0.0.1:8000/api/v1/cuaca/precip")
        data = res.json()
        
        target = next((d for d in data if d["name"] == "Test Location"), None)
        
        if not target:
            print("❌ Test Location not found in API response")
            return
            
        print(f"📊 API Result: {target['total_precip_24h']} mm, Status: {target['status']}")
        
        if 59.0 <= target['total_precip_24h'] <= 61.0:
            print("✅ Calculation Correct (approx 60mm)")
        else:
            print(f"❌ Calculation Wrong. Expected ~60mm, got {target['total_precip_24h']}mm")
            
    except Exception as e:
        print(f"❌ API Error: {e}")
        
    # Cleanup
    db.locations.delete_one({"_id": loc_id})
    db.weather_logs.delete_many({"location_id": loc_id})

if __name__ == "__main__":
    test_precip_calculation()
