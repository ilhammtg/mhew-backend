import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import os
import httpx
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

from .config import BMKG_NOWCAST_RSS, DEFAULT_WEATHER_MODE
from .database import (
    col_alerts, col_weather_alerts, col_weather_logs, 
    col_locations, get_setting
)
from .services import (
    get_bmkg_eq, fetch_bytes, windy_point_forecast, get_bmkg_forecast_xml,
    fetch_bmkg_point_forecast_json
)
from .utils import (
    get_alert_level, normalize_name, parse_windy_latest, calculate_24h_precipitation,
    haversine_distance, get_bmkg_weather_text, get_weather_score, get_adm4_from_csv
)

# Global State
LAST_EQ_TIME = None
# LAST_WEATHER_LINK removed

async def check_gempa(context: ContextTypes.DEFAULT_TYPE):
    global LAST_EQ_TIME
    try:
        gempa = await get_bmkg_eq()
        if gempa.get("DateTime") != LAST_EQ_TIME:
            LAST_EQ_TIME = gempa.get("DateTime")
            alert = get_alert_level(gempa.get("Potensi", ""))

            gempa["_id"] = gempa["DateTime"]
            gempa["alert_level"] = alert["level"]
            gempa["saved_at"] = datetime.now(timezone.utc)

            col_alerts.update_one({"_id": gempa["_id"]}, {"$set": gempa}, upsert=True)

            if alert["level"] in ["DANGER", "WARNING"]:
                chat_id = context.job.data.get("chat_id") if context.job and context.job.data else None
                if chat_id:
                    msg = (
                        f"{alert['emoji']} *{alert['label']}*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *Wilayah:* {gempa.get('Wilayah')}\n"
                        f"📏 *Magnitudo:* {gempa.get('Magnitude')} SR\n"
                        f"📉 *Kedalaman:* {gempa.get('Kedalaman')}\n"
                        f"🌊 *Potensi:* {gempa.get('Potensi')}\n"
                        f"⏱ *Waktu:* {gempa.get('DateTime')}\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"⚠️ _Cek informasi resmi BMKG_"
                    )
                    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        print(f"⚠️ EQ Error: {e}")

async def check_weather_rss(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = context.job.data.get("chat_id") if context.job and context.job.data else None
        if not chat_id:
            return

        xml_bytes = await fetch_bytes(BMKG_NOWCAST_RSS)
        root = ET.fromstring(xml_bytes)

        loc_docs = list(col_locations.find({"chat_id": chat_id}))
        keywords = [d.get("name", "") for d in loc_docs if d.get("name")] or ["Aceh"]
        keywords_norm = [normalize_name(k) for k in keywords]

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            hay = normalize_name(f"{title} {desc}")
            match = any(k and k in hay for k in keywords_norm)

            if match and link:
                # Unique ID for this user + alert link
                alert_id = f"{chat_id}:{link}"
                
                # Check if already sent
                if col_weather_alerts.find_one({"_id": alert_id}):
                    continue

                data = {
                    "_id": alert_id,
                    "chat_id": chat_id,
                    "type": "bmkg_nowcast",
                    "title": title,
                    "desc": desc,
                    "link": link,
                    "date": pub_date,
                    "matched_keywords": keywords,
                    "saved_at": datetime.now(timezone.utc)
                }
                col_weather_alerts.update_one({"_id": data["_id"]}, {"$set": data}, upsert=True)

                msg = (
                    f"⛈ *PERINGATAN CUACA BMKG*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"*{title}*\n\n"
                    f"{desc[:300]}...\n\n"
                    f"🔗 [Baca Selengkapnya]({link})\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📅 {pub_date}"
                )
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)
                break

    except Exception as e:
        print(f"⚠️ Weather RSS Error: {e}")

async def storm_monitor(context: ContextTypes.DEFAULT_TYPE):
    """
    Memantau potensi badai dari Windy (Wind & Pressure).
    """
    try:
        chat_id = context.job.data.get("chat_id")
        if not chat_id: return

        locs = list(col_locations.find({"chat_id": chat_id}))
        if not locs: return

        # API Key & URL
        api_key = os.getenv("API_KEY", "RAHASIA_KUNCI_API_ANDA")
        api_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000") # Default local

        for loc in locs:
            try:
                # 1. Fetch Windy Data
                windy = await windy_point_forecast(loc["lat"], loc["lon"])
                latest = parse_windy_latest(windy)
                
                if not latest: continue

                # 2. Check Thresholds
                # Wind Gust > 18 m/s (~65 km/h) OR Pressure < 998 hPa
                wind_gust = latest.get("gust_ms", 0)
                pressure = latest.get("pressure_pa", 101325) / 100 # Convert Pa to hPa
                
                is_alert = False
                alert_msg = None

                if wind_gust > 18:
                    is_alert = True
                    alert_msg = f"🌬 *POTENSI BADAI ANGIN*\nKecepatan Angin: {wind_gust:.1f} m/s"
                elif pressure < 996:
                    is_alert = True
                    alert_msg = f"🌀 *TEKANAN RENDAH EKSTRIM*\nTekanan: {pressure:.1f} hPa"

                # 3. Log to Storm Monitor via API
                payload = {
                    "location_id": str(loc["_id"]),
                    "last_check": datetime.now(timezone.utc).isoformat(),
                    "source": "Windy",
                    "parameters": {
                        "wind_gust": wind_gust,
                        "pressure": pressure,
                        "wind_direction": latest.get("wind_dir_deg", 0)
                    },
                    "is_alert": is_alert,
                    "alert_message": alert_msg
                }

                # Post to API (Fire and forget, or log error)
                headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"{api_url}/api/v1/storm/log", json=payload, headers=headers)

                # 4. Telegram Alert
                if is_alert:
                     # Check cooldown? For now send alert.
                    msg = (
                        f"⚠️ *PERINGATAN DINI BADAI*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *{loc['name']}*\n"
                        f"{alert_msg}\n\n"
                        f"Tetap waspada dan pantau peta badai."
                    )
                    await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=ParseMode.MARKDOWN)

            except Exception as e:
                print(f"⚠️ Storm Monitor Error {loc['name']}: {e}")

    except Exception as e:
        print(f"⚠️ Storm Loop Error: {e}")

async def weather_logger(context: ContextTypes.DEFAULT_TYPE):
    """
    Log data cuaca BMKG via API.
    """
    try:
        chat_id = context.job.data.get("chat_id")
        if not chat_id: return

        locs = list(col_locations.find({"chat_id": chat_id}))
        if not locs: return

        api_key = os.getenv("API_KEY", "RAHASIA_KUNCI_API_ANDA")
        api_url = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

        now_utc = datetime.now(timezone.utc)

        for loc in locs:
            try:
                # 2. Resolve ADM4 Code (Dynamic from CSV / DB)
                adm4_code = loc.get("adm4")
                if not adm4_code:
                    # Try lookup from CSV
                    found_code = get_adm4_from_csv(loc["name"])
                    if found_code:
                        print(f"✅ Auto-resolved ADM4 for {loc['name']}: {found_code}")
                        # Persist to DB
                        col_locations.update_one({"_id": loc["_id"]}, {"$set": {"adm4": found_code}})
                        adm4_code = found_code
                    else:
                        print(f"⚠️ ADM4 Code not found for {loc['name']}, skipping BMKG log.")
                        continue # Skip if no code found

                # 3. Fetch Data JSON
                data_json = await fetch_bmkg_point_forecast_json(adm4_code)
                if not data_json or "data" not in data_json: 
                    continue
                
                # Structure: data[0] -> cuaca[][]
                # Usually data[0] is the location
                # cuaca is list of list? Or list of objects?
                # Sample: "cuaca": [[{"datetime":...}, ...]]
                # Flatten the list of lists
                
                forecast_flat = []
                # Check data structure deeply
                raw_data = data_json.get("data", [])
                if not raw_data: continue
                
                cuaca_lists = raw_data[0].get("cuaca", [])
                for sublist in cuaca_lists:
                    for item in sublist:
                        forecast_flat.append(item)
                
                # Sort by datetime just in case
                # Format: "2025-10-12 08:00:00" (utc_datetime)
                def parse_dt(d_str):
                    try:
                        return datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    except:
                        return datetime.min.replace(tzinfo=timezone.utc)

                forecast_flat.sort(key=lambda x: parse_dt(x.get("utc_datetime", "")))
                
                # 4. Find Current & Forecast
                # Current = item closest to now_utc
                # Simple logic: first item in future OR very last item if all past?
                # Better: item with smallest abs(delta) to now
                
                best_current = None
                min_diff = 999999999
                
                # Forecast 24h = items > now
                forecast_24h_items = []
                
                for item in forecast_flat:
                    dt_obj = parse_dt(item.get("utc_datetime"))
                    diff = abs((dt_obj - now_utc).total_seconds())
                    
                    if diff < min_diff:
                        min_diff = diff
                        best_current = item
                        
                    if dt_obj > now_utc and len(forecast_24h_items) < 8: # Next 24h (3h intervals -> 8 items)
                        forecast_24h_items.append(item)
                
                if not best_current: continue

                # 5. Extract Current Values
                # Keys: t (temp), hu (humid), ws (wind km/h?), weather_desc, weather (code)
                cur_temp = float(best_current.get("t", 0))
                cur_hu = float(best_current.get("hu", 0))
                cur_desc = best_current.get("weather_desc", "Berawan")
                cur_ws_kmh = float(best_current.get("ws", 0))
                cur_ws_ms = cur_ws_kmh / 3.6 # Km/h to m/s
                
                # Precip Logic (Estimate from Code/Desc)
                # BMKG JSON sometimes has 'tp' (Rain Potential?) or just weather code
                # Sample had "tp": 0.1 (mm?)
                precip_mm = float(best_current.get("tp", 0.0)) # Try reading tp direct
                
                # 6. Build Forecast Array
                final_forecast = []
                for f in forecast_24h_items:
                    f_dt = parse_dt(f.get("utc_datetime"))
                    time_diff = int((f_dt - now_utc).total_seconds() / 3600)
                    
                    ws_ms_item = float(f.get("ws", 0)) / 3.6
                    
                    final_forecast.append({
                        "time": f"+{time_diff}h",
                        "temp": int(f.get("t", 0)),
                        "desc": f.get("weather_desc", ""),
                        "humidity": int(f.get("hu", 0)),
                        "wind_speed": float(f"{ws_ms_item:.1f}"),
                        "precip": float(f.get("tp", 0.0))
                    })

                # 7. Construct Payload
                payload = {
                    "location_id": str(loc["_id"]),
                    "timestamp": now_utc.isoformat(),
                    "source": "BMKG_API",
                    "data": {
                        "temp": int(cur_temp),
                        "humidity": int(cur_hu),
                        "weather_desc": cur_desc,
                        "precip_mm": precip_mm,
                        "wind_speed": cur_ws_ms
                    },
                    "forecast_3h": final_forecast
                }

                # 8. Send to API
                headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(f"{api_url}/api/v1/weather/log", json=payload, headers=headers)

            except Exception as e:
                print(f"⚠️ Weather Log Error {loc['name']}: {e}")

    except Exception as e:
        print(f"⚠️ Weather Logger Error: {e}")

async def check_weather_rss_system(context: ContextTypes.DEFAULT_TYPE):
    """
    System-level RSS check for default keywords (Aceh).
    Ensures dashboard has data even without active users.
    """
    try:
        xml_bytes = await fetch_bytes(BMKG_NOWCAST_RSS)
        root = ET.fromstring(xml_bytes)

        # Default keywords for system
        keywords = ["Aceh", "Banda Aceh", "Lhokseumawe", "Meulaboh", "Sabang"]
        keywords_norm = [normalize_name(k) for k in keywords]

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            hay = normalize_name(f"{title} {desc}")
            match = any(k and k in hay for k in keywords_norm)

            if match and link:
                # Unique ID for system + link
                alert_id = f"SYSTEM:{link}"
                
                # Check if already processed
                if col_weather_alerts.find_one({"_id": alert_id}):
                    continue

                data = {
                    "_id": alert_id,
                    "chat_id": "SYSTEM",
                    "type": "bmkg_nowcast",
                    "title": title,
                    "desc": desc,
                    "link": link,
                    "date": pub_date,
                    "matched_keywords": keywords,
                    "saved_at": datetime.now(timezone.utc)
                }
                col_weather_alerts.update_one({"_id": data["_id"]}, {"$set": data}, upsert=True)
                print(f"✅ System Weather Alert: {title}")
                break

    except Exception as e:
        print(f"⚠️ System RSS Error: {e}")

async def weather_logger_system(context: ContextTypes.DEFAULT_TYPE):
    """
    System-level weather logger for default locations.
    """
    try:
        # Default locations if no users are active
        system_locs = list(col_locations.find({"chat_id": "SYSTEM"}))
        if not system_locs:
            return

        now_utc = datetime.now(timezone.utc)
        for loc in system_locs:
            try:
                windy = await windy_point_forecast(loc["lat"], loc["lon"])
                latest = parse_windy_latest(windy) or {}

                log_data = {
                    "chat_id": "SYSTEM",
                    "location_id": loc["_id"],
                    "location_name": loc["name"],
                    "timestamp": now_utc,
                    "source": "windy",
                    "latest": latest,
                    "forecast_raw": windy,
                    "raw_keys": list(windy.keys())
                }
                col_weather_logs.insert_one(log_data)
                print(f"✅ System Logged: {loc['name']}")
                
            except Exception as e:
                print(f"⚠️ Error logging system {loc.get('name')}: {e}")

    except Exception as e:
        print(f"⚠️ System Logger Error: {e}")

def ensure_jobs_for_chat(app: Application, chat_id: int):
    jq = app.job_queue
    name_prefix = f"mhews:{chat_id}:"

    for j in jq.jobs():
        if j.name and j.name.startswith(name_prefix):
            return

    jq.run_repeating(check_gempa, interval=60, first=5, name=name_prefix + "eq", data={"chat_id": chat_id})
    jq.run_repeating(check_weather_rss, interval=300, first=10, name=name_prefix + "rss", data={"chat_id": chat_id})
    jq.run_repeating(weather_logger, interval=3600, first=2, name=name_prefix + "wlog", data={"chat_id": chat_id})

def ensure_system_jobs(app: Application):
    jq = app.job_queue
    if not jq:
        print("⚠️ Job Queue not available! System jobs will not run.")
        return

    name_prefix = "mhews:SYSTEM:"
    
    # Check if system jobs exist
    for j in jq.jobs():
        if j.name and j.name.startswith(name_prefix):
            return

    jq.run_repeating(check_weather_rss_system, interval=300, first=5, name=name_prefix + "rss")
    jq.run_repeating(weather_logger_system, interval=3600, first=2, name=name_prefix + "wlog")
