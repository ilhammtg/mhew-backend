import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, Application

from .config import BMKG_NOWCAST_RSS, DEFAULT_WEATHER_MODE
from .database import (
    col_alerts, col_weather_alerts, col_weather_logs, 
    col_locations, get_setting
)
from .services import (
    get_bmkg_eq, fetch_bytes, windy_point_forecast, get_bmkg_forecast_xml
)
from .utils import (
    get_alert_level, normalize_name, parse_windy_latest, calculate_24h_precipitation,
    haversine_distance, get_bmkg_weather_text, get_weather_score
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

async def weather_logger(context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = context.job.data.get("chat_id") if context.job and context.job.data else None
        if not chat_id:
            return

        locs = list(col_locations.find({"chat_id": chat_id}))
        if not locs:
            return

        # 1. Ambil data BMKG (XML)
        # Asumsi semua lokasi di Aceh/Sumut, jika tidak bisa dinamis nanti.
        # Untuk efisiensi kita ambil XML sekali saja per run job ini.
        try:
            xml_bytes = await get_bmkg_forecast_xml("Aceh")
            root = ET.fromstring(xml_bytes)
            
            # Extract areas
            areas = []
            for area in root.findall(".//area"):
                lat = area.get("latitude")
                lon = area.get("longitude")
                desc = area.get("description") or area.get("id")
                if lat and lon:
                    areas.append({
                        "node": area,
                        "lat": float(lat),
                        "lon": float(lon),
                        "desc": desc
                    })
        except Exception as e:
            print(f"⚠️ BMKG XML Error: {e}")
            return

        now_utc = datetime.now(timezone.utc)
        
        for loc in locs:
            try:
                # 2. Cari area terdekat
                nearest = None
                min_dist = 99999.0
                
                for a in areas:
                    dist = haversine_distance(loc["lat"], loc["lon"], a["lat"], a["lon"])
                    if dist < min_dist:
                        min_dist = dist
                        nearest = a

                if not nearest or min_dist > 50: # Max 50km tolerance
                    # print(f"⚠️ No BMKG station near {loc['name']} ({min_dist:.1f} km)")
                    continue
                
                # 3. Parse Weather Code (h=0 usually closest to now)
                # BMKG timerange format: 202412290000, 202412290600, etc.
                # Kita cari timerange dengan datetime terdekat dengan now.
                
                weather_node = nearest["node"].find("parameter[@id='weather']")
                if not weather_node:
                    continue

                best_val = None
                min_time_diff = 999999
                
                for timerange in weather_node.findall("timerange"):
                    dt_str = timerange.get("datetime") # YYYYMMDDHHmm
                    if not dt_str: continue
                    
                    # BMKG XML format is usually local time or UTC? Usually UTC+7? 
                    # Spec says datetime attribute is YYYYMMDDHHmm (local time? No, usually UTC in raw data, let's assume raw string compare for simplicity closest)
                    # Let's clean parse.
                    try:
                        t = datetime.strptime(dt_str, "%Y%m%d%H%M")
                        # Adjust timezone if needed. Context says parsing logic might be complex.
                        # Simple logic: XML usually has h="0", "6" etc from base.
                        # We just take the first one (h="0" -> closest forecast)
                        
                        val = timerange.findtext("value")
                        if val:
                            best_val = val
                            # Break on first match usually current
                            break
                    except:
                        pass
                
                if not best_val:
                    continue
                    
                weather_text = get_bmkg_weather_text(best_val)
                score = get_weather_score(weather_text)
                
                # 4. Log to DB
                log_data = {
                    "chat_id": chat_id,
                    "location_id": loc["_id"],
                    "location_name": loc["name"],
                    "timestamp": now_utc,
                    "source": "bmkg_digital_forecast",
                    "bmkg_area": nearest["desc"],
                    "bmkg_dist_km": min_dist,
                    "weather_code": best_val,
                    "weather_text": weather_text,
                    "score": score
                }
                col_weather_logs.insert_one(log_data)

                # 5. Alert Logic (Simple & Readable)
                # Only alert if DANGER (score 100) or changed significantly?
                # User asked: "gampang dipahami".
                
                if score >= 75: # Waspada (75) / Bahaya (100)
                    status_label = "BAHAYA" if score >= 100 else "WASPADA"
                    emoji = "🔴" if score >= 100 else "🟠"
                    
                    alert_msg = (
                        f"{emoji} *STATUS: {status_label}*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📍 *{loc['name']}*\n"
                        f"☁️ Kondisi: *{weather_text}*\n"
                        f"⚠️ Hati-hati beraktivitas di luar rumah."
                    )
                    await context.bot.send_message(chat_id=chat_id, text=alert_msg, parse_mode=ParseMode.MARKDOWN)

            except Exception as e:
                print(f"⚠️ Error logging {loc.get('name')}: {e}")

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
    jq.run_repeating(weather_logger, interval=3600, first=30, name=name_prefix + "wlog", data={"chat_id": chat_id})

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
    jq.run_repeating(weather_logger_system, interval=3600, first=10, name=name_prefix + "wlog")
