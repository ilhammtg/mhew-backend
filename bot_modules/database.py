from pymongo import MongoClient, ASCENDING
from datetime import datetime, timezone
from .config import MONGO_URI

client = None
db = None

if MONGO_URI:
    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client["emergency_db"]
    except Exception as e:
        print(f"❌ Database Connection Error: {e}")

# Collections (will be None if db is None, need to handle this in usage)
# Or better, use a proxy or check in functions. 
# For simplicity in this existing codebase, we'll initialize them but they might fail if accessed.
# Actually, if db is None, db["alerts"] raises TypeError.
# Let's keep it simple: if no URI, we don't define cols, or define them as None.

if db is not None:
    col_alerts = db["alerts"]
    col_weather_alerts = db["weather_alerts"]
    col_weather_logs = db["weather_logs"]
    col_locations = db["locations"]
    col_settings = db["settings"]
else:
    # Fallback to avoid ImportErrors, but operations will fail
    col_alerts = None
    col_weather_alerts = None
    col_weather_logs = None
    col_locations = None
    col_settings = None

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
