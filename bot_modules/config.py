import os
from dotenv import load_dotenv

load_dotenv()

TOKEN_BOT = os.getenv("TELEGRAM_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
WINDY_API_KEY = os.getenv("WINDY_API_KEY")
DEFAULT_WEATHER_MODE = (os.getenv("WEATHER_MODE", "both") or "both").lower().strip()

# URLs
BMKG_EQ_URL = "https://data.bmkg.go.id/DataMKG/TEWS/autogempa.json"
BMKG_NOWCAST_RSS = "https://www.bmkg.go.id/alerts/nowcast/id/rss.xml"
WINDY_POINT_FORECAST_URL = "https://api.windy.com/api/point-forecast/v2"
