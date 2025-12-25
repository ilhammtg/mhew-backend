from pymongo import MongoClient, ASCENDING
from datetime import datetime, timezone
from .config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client["emergency_db"]

col_alerts = db["alerts"]
col_weather_alerts = db["weather_alerts"]
col_weather_logs = db["weather_logs"]
col_locations = db["locations"]
col_settings = db["settings"]

# Indexes
col_locations.create_index([("chat_id", ASCENDING), ("name_norm", ASCENDING)], unique=True)
col_weather_logs.create_index([("chat_id", ASCENDING), ("location_id", ASCENDING), ("timestamp", ASCENDING)])
col_alerts.create_index([("DateTime", ASCENDING)])
col_weather_alerts.create_index([("saved_at", ASCENDING)])

def get_setting(chat_id: int, key: str, default=None):
    doc = col_settings.find_one({"_id": f"{chat_id}:{key}"})
    return doc["value"] if doc and "value" in doc else default

def set_setting(chat_id: int, key: str, value):
    col_settings.update_one(
        {"_id": f"{chat_id}:{key}"},
        {"$set": {"value": value, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
