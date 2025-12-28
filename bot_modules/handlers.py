from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler
from datetime import datetime, timezone

from .config import DEFAULT_WEATHER_MODE, WINDY_API_KEY
from .database import (
    col_locations, col_alerts, col_weather_alerts, 
    get_setting, set_setting
)
from .services import (
    get_bmkg_eq, geocode_location, windy_point_forecast,
    fetch_bmkg_point_forecast_json
)
from .utils import (
    get_alert_level, normalize_name, format_ts_ms, parse_windy_latest,
    get_adm4_from_csv, get_bmkg_weather_text
)
from .keyboards import (
    main_menu_keyboard, location_menu_keyboard, 
    settings_keyboard, back_keyboard
)
from .jobs import ensure_jobs_for_chat, LAST_EQ_TIME

# Conversation states
WAITING_LOCATION = 1

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if get_setting(chat_id, "weather_mode", None) is None:
        set_setting(chat_id, "weather_mode", DEFAULT_WEATHER_MODE)

        "👋 *MHEWS - Multi-Hazard Early Warning System*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Bot ini memantau:\n"
        "🌍 Gempa (BMKG AutoGempa)\n"
        "⛈ Peringatan Cuaca (BMKG RSS & Point Forecast)\n\n"
        "*Cara pakai cepat:*\n"
        "1) Masuk *Kelola Lokasi* → *Tambah Lokasi* (cukup ketik nama kota/daerah)\n"
        "2) Masuk *Cek Cuaca* untuk melihat prakiraan BMKG per lokasi\n"
        "3) *Pengaturan* untuk notifikasi lanjutan\n\n"
        "*Catatan:*\n"
        "• Bot ini menggunakan API BMKG v2 (Resolution 1 jam - 3 jam).\n"
        "• Koordinat otomatis dicocokkan dengan kode wilayah BMKG terdekat.\n\n"
        "Pilih menu di bawah:"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())

async def start_with_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_jobs_for_chat(context.application, update.effective_chat.id)
    await start(update, context)

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "back_main":
        await query.edit_message_text(
            "🏠 *MENU UTAMA*\n━━━━━━━━━━━━━━━━━━\n\nPilih fitur yang ingin digunakan:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard()
        )
        return

    if query.data == "menu_status":
        mode = get_setting(chat_id, "weather_mode", DEFAULT_WEATHER_MODE)
        locs = list(col_locations.find({"chat_id": chat_id}))
        gempa_count = col_alerts.count_documents({})
        alert_count = col_weather_alerts.count_documents({"chat_id": chat_id})

        text = (
            f"📊 *STATUS SISTEM*\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"✅ *Status:* Online\n"
            f"🌐 *Mode Cuaca:* `{mode.upper()}`\n"
            f"📍 *Lokasi Terpantau:* {len(locs)}\n"
            f"🌍 *Total Gempa Tercatat:* {gempa_count}\n"
            f"⛈ *Alert Cuaca (chat ini):* {alert_count}\n\n"
            f"🕐 *Update Terakhir:*\n"
            f"├ Gempa: {LAST_EQ_TIME or 'Belum ada'}\n"
            f"└ RSS: {('Ada' if col_weather_alerts.find_one({'chat_id': 'SYSTEM'}) else 'Belum ada')}\n\n"
            f"⚙️ *API Status:*\n"
            f"├ BMKG Gempa: ✅\n"
            f"└ BMKG Cuaca: ✅ (v2 JSON)"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    if query.data == "menu_gempa":
        try:
            data = await get_bmkg_eq()
            alert = get_alert_level(data.get("Potensi", ""))
            text = (
                f"{alert['emoji']} *INFORMASI GEMPA TERKINI*\n"
                f"━━━━━━━━━━━━━━━━━━\n\n"
                f"📍 *Wilayah:* {data.get('Wilayah')}\n"
                f"📏 *Magnitudo:* {data.get('Magnitude')} SR\n"
                f"📉 *Kedalaman:* {data.get('Kedalaman')}\n"
                f"🌊 *Potensi:* {data.get('Potensi')}\n"
                f"⏱ *Waktu:* {data.get('DateTime')}\n"
                f"🧭 *Koordinat:* {data.get('Coordinates', '-')}\n\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ℹ️ *Sumber:* BMKG"
            )
        except Exception as e:
            text = f"❌ Gagal mengambil data gempa.\n\nError: {str(e)}"

        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

    if query.data == "menu_locations":
        text = (
            "📍 *KELOLA LOKASI*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Tambah lokasi (tanpa koordinat manual),\n"
            "lihat daftar, atau hapus lokasi."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=location_menu_keyboard())
        return

    if query.data == "loc_list":
        docs = list(col_locations.find({"chat_id": chat_id}).sort("created_at", 1))
        if not docs:
            text = "📍 *DAFTAR LOKASI*\n━━━━━━━━━━━━━━━━━━\n\nBelum ada lokasi.\nGunakan *Tambah Lokasi*."
        else:
            lines = ["📍 *DAFTAR LOKASI TERPANTAU*\n━━━━━━━━━━━━━━━━━━\n"]
            for i, d in enumerate(docs, start=1):
                lines.append(f"{i}. *{d['name']}*\n   📌 `{d['lat']:.4f}, {d['lon']:.4f}`\n")
            text = "\n".join(lines)

        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=location_menu_keyboard())
        return

    if query.data == "loc_add":
        text = (
            "➕ *TAMBAH LOKASI*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "Kirim nama lokasi.\n\n"
            "*Contoh:*\n"
            "• Banda Aceh\n"
            "• Lhokseumawe, Aceh\n"
            "• Jakarta Pusat\n\n"
            "_Ketik /cancel untuk membatalkan_"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        return WAITING_LOCATION

    if query.data == "loc_delete":
        docs = list(col_locations.find({"chat_id": chat_id}).sort("created_at", 1))
        if not docs:
            await query.edit_message_text("❌ Tidak ada lokasi untuk dihapus.", parse_mode=ParseMode.MARKDOWN, reply_markup=location_menu_keyboard())
            return

        keyboard = [[InlineKeyboardButton(f"🗑 {d['name']}", callback_data=f"del_{d['_id']}")] for d in docs]
        keyboard.append([InlineKeyboardButton("◀️ Batal", callback_data="menu_locations")])
        await query.edit_message_text(
            "🗑 *HAPUS LOKASI*\n━━━━━━━━━━━━━━━━━━\n\nPilih lokasi:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data.startswith("del_"):
        loc_id = query.data[4:]
        res = col_locations.delete_one({"_id": loc_id, "chat_id": chat_id})
        await query.answer("✅ Dihapus" if res.deleted_count else "❌ Gagal menghapus")
        await query.edit_message_text(
            "📍 *KELOLA LOKASI*\n━━━━━━━━━━━━━━━━━━\n\nPilih aksi:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=location_menu_keyboard()
        )
        return

    if query.data == "menu_weather":
        mode = get_setting(chat_id, "weather_mode", DEFAULT_WEATHER_MODE)
        docs = list(col_locations.find({"chat_id": chat_id}))

        if not docs:
            await query.edit_message_text(
                "❌ *BELUM ADA LOKASI*\n━━━━━━━━━━━━━━━━━━\n\nTambah lokasi dulu di *Kelola Lokasi*.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        if mode in ("windy", "both") and not WINDY_API_KEY:
            await query.edit_message_text(
                "❌ *WINDY API TIDAK TERSEDIA*\n━━━━━━━━━━━━━━━━━━\n\nWINDY_API_KEY belum dikonfigurasi.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_keyboard()
            )
            return

        keyboard = [[InlineKeyboardButton(f"🌦 {d['name']}", callback_data=f"weather_{d['_id']}")] for d in docs]
        keyboard.append([InlineKeyboardButton("◀️ Kembali", callback_data="back_main")])
        await query.edit_message_text(
            f"🌦 *CEK CUACA* ({mode.upper()})\n━━━━━━━━━━━━━━━━━━\n\nPilih lokasi:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data.startswith("weather_"):
        loc_id = query.data[8:]
        doc = col_locations.find_one({"_id": loc_id, "chat_id": chat_id})
        if not doc:
            await query.answer("❌ Lokasi tidak ditemukan")
            return

        mode = get_setting(chat_id, "weather_mode", DEFAULT_WEATHER_MODE)
        await query.answer("🔄 Mengambil data cuaca...")

        text_parts = [
            "🌦 *CUACA LOKASI*",
            "━━━━━━━━━━━━━━━━━━",
            f"📍 *Lokasi:* {doc['name']}",
            f"🧭 *Koordinat:* `{doc['lat']:.4f}, {doc['lon']:.4f}`",
            ""
        ]

        # BMKG Logic (Primary)
        adm4 = doc.get("adm4")
        if not adm4:
            adm4 = get_adm4_from_csv(doc["name"])
            if adm4:
                # Save for future
                col_locations.update_one({"_id": loc_id}, {"$set": {"adm4": adm4}})
        
        if adm4:
            try:
                data_json = await fetch_bmkg_point_forecast_json(adm4)
                # Parse logic (simplified from jobs.py)
                raw_data = data_json.get("data", [])
                if raw_data:
                    cuaca_lists = raw_data[0].get("cuaca", [])
                    forecast_flat = []
                    for sublist in cuaca_lists:
                        for item in sublist:
                            forecast_flat.append(item)
                    
                    # Sort
                    def parse_dt(d_str):
                        try: return datetime.strptime(d_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        except: return datetime.min.replace(tzinfo=timezone.utc)
                    
                    forecast_flat.sort(key=lambda x: parse_dt(x.get("utc_datetime", "")))
                    
                    # Find current (closest)
                    now_utc = datetime.now(timezone.utc)
                    best_current = None
                    min_diff = 999999999
                    for item in forecast_flat:
                        dt_obj = parse_dt(item.get("utc_datetime"))
                        diff = abs((dt_obj - now_utc).total_seconds())
                        if diff < min_diff:
                            min_diff = diff
                            best_current = item
                    
                    if best_current:
                        lat_disp = f"{doc['lat']:.4f}"
                        lon_disp = f"{doc['lon']:.4f}"
                        
                        t = best_current.get("t", "-")
                        h = best_current.get("hu", "-")
                        ws_kmh = float(best_current.get("ws", 0))
                        ws_ms = ws_kmh / 3.6
                        desc = best_current.get("weather_desc", "Berawan")
                        
                        text_parts += [
                            "⛈ *BMKG Point Forecast*",
                            f"📍 *Wilayah:* {doc['name']}",
                            f"🆔 *Kode Wilayah:* {adm4}",
                            f"🕐 *Waktu:* {best_current.get('local_datetime', '-')}",
                            "",
                            f"🌡 *Suhu:* {t}°C",
                            f"💧 *Kelembapan:* {h}%",
                            f"☁️ *Cuaca:* {desc}",
                            f"🌬 *Angin:* {ws_ms:.1f} m/s",
                            "",
                            "ℹ️ *Sumber:* BMKG API v2",
                            ""
                        ]
                    else:
                        text_parts.append("⚠️ Data BMKG kosong untuk wilayah ini.")
                else:
                    text_parts.append("⚠️ Format Data BMKG tidak dikenali.")

            except Exception as bmkg_err:
                text_parts.append(f"⚠️ Gagal mengambil data BMKG: {bmkg_err}")
                
        else:
            text_parts.append("⚠️ Kode Wilayah BMKG tidak ditemukan. Coba nama lokasi yang lebih spesifik (misal: nama desa/kecamatan).")

        text = "\n".join(text_parts).strip()

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"weather_{loc_id}")],
            [InlineKeyboardButton("◀️ Kembali", callback_data="menu_weather")]
        ]
        
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if query.data == "menu_settings":
        mode = get_setting(chat_id, "weather_mode", DEFAULT_WEATHER_MODE)
        text = (
            "⚙️ *PENGATURAN*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"*Mode Cuaca Aktif:* `{mode.upper()}`\n\n"
            "Saat ini semua layanan menggunakan data **BMKG**.\n"
            "Pilihan mode di bawah hanya mempengaruhi jenis notifikasi (RSS vs Point).\n\n"
            "Pilih mode:"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=settings_keyboard())
        return

    if query.data.startswith("mode_"):
        new_mode = query.data[5:]
        if new_mode not in ("bmkg", "windy", "both"):
            await query.answer("Mode tidak valid")
            return
        set_setting(chat_id, "weather_mode", new_mode)
        await query.answer(f"✅ Mode: {new_mode.upper()}")
        await query.edit_message_text(
            "⚙️ *PENGATURAN*\n━━━━━━━━━━━━━━━━━━\n\n"
            f"*Mode Cuaca Aktif:* `{new_mode.upper()}`\n\nPilih mode:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=settings_keyboard()
        )
        return

    if query.data == "menu_help":
        text = (
            "ℹ️ *BANTUAN*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "• Tambah lokasi: *Kelola Lokasi → Tambah Lokasi*\n"
            "• Cek cuaca: *Cek Cuaca* lalu pilih lokasi\n"
            "• Mode cuaca: *Pengaturan*\n\n"
            "Tips:\n"
            "• Ketik lokasi lebih spesifik: `Banda Aceh, Indonesia`\n"
            "• Jika Windy error, cek apakah API Key khusus *Point Forecast*."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=back_keyboard())
        return

async def handle_location_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user

    msg = await update.message.reply_text(f"🔍 Mencari lokasi: *{text}*...", parse_mode=ParseMode.MARKDOWN)
    geo = await geocode_location(text)

    if not geo:
        await msg.edit_text(
            "❌ *Lokasi tidak ditemukan.*\n\n"
            "Coba lebih spesifik.\n"
            "Contoh: _Banda Aceh, Indonesia_",
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END

    loc_id = f"{chat_id}:{normalize_name(geo['display_name'])}"
    loc_data = {
        "_id": loc_id,
        "chat_id": chat_id,
        "name": geo["display_name"],
        "name_norm": normalize_name(geo["display_name"]),
        "lat": geo["lat"],
        "lon": geo["lon"],
        "created_at": datetime.now(timezone.utc),
        "created_by": user.id
    }

    try:
        col_locations.update_one(
            {"_id": loc_id},
            {"$set": loc_data},
            upsert=True
        )
        await msg.edit_text(
            "✅ *LOKASI TERSIMPAN*\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📍 *Nama:* {geo['display_name']}\n"
            f"🧭 *Lat:* `{geo['lat']}`\n"
            f"🧭 *Lon:* `{geo['lon']}`\n\n"
            "Lokasi ini akan dipantau.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await msg.edit_text(f"❌ Gagal menyimpan lokasi: {e}")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Operasi dibatalkan.", reply_markup=main_menu_keyboard())
    return ConversationHandler.END
