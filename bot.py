from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ConversationHandler,
    MessageHandler,
    filters
)

from bot_modules.config import TOKEN_BOT, MONGO_URI
from bot_modules.handlers import (
    start_with_jobs, menu_callback, handle_location_text, cancel, WAITING_LOCATION
)

from bot_modules.database import col_locations
from bot_modules.services import geocode_location
from bot_modules.jobs import ensure_system_jobs
from bot_modules.utils import normalize_name

async def setup_system(app: Application):
    """
    Setup default locations for SYSTEM context if not exists.
    """
    print("⚙️ Checking System Configuration...")
    
    # Ensure system jobs are running
    ensure_system_jobs(app)
    
    # Check if SYSTEM has locations
    if col_locations.count_documents({"chat_id": "SYSTEM"}) == 0:
        print("⚠️ No system locations found. Seeding defaults...")
        defaults = ["Banda Aceh", "Lhokseumawe", "Meulaboh", "Sigli", "Takengon"]
        
        for loc_name in defaults:
            geo = await geocode_location(loc_name)
            if geo:
                name_norm = normalize_name(geo["display_name"])
                col_locations.update_one(
                    {"chat_id": "SYSTEM", "name_norm": name_norm},
                    {"$set": {
                        "chat_id": "SYSTEM",
                        "name": geo["display_name"],
                        "name_norm": name_norm,
                        "lat": geo["lat"],
                        "lon": geo["lon"],
                        "added_at": None
                    }},
                    upsert=True
                )
                print(f"✅ Added system location: {loc_name}")
    else:
        print("✅ System locations ready.")

if __name__ == "__main__":
    print("🚀 MHEWS Bot berjalan (Modular)...")

    if not TOKEN_BOT:
        raise SystemExit("❌ TELEGRAM_TOKEN tidak ditemukan di .env")
    if not MONGO_URI:
        raise SystemExit("❌ MONGO_URI tidak ditemukan di .env")

    application = Application.builder().token(TOKEN_BOT).build()

    # Conversation Handler untuk Tambah Lokasi (dari tombol)
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(menu_callback, pattern="^loc_add$")],
        states={WAITING_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_location_text)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True
    )

    # Register handlers
    # override /start handler agar auto pasang job
    application.add_handler(CommandHandler("start", start_with_jobs))
    application.add_handler(conv_handler)
    application.add_handler(CallbackQueryHandler(menu_callback))

    # Run system setup on startup
    # Note: post_init is the clean way to run async setup in PTB
    application.post_init = setup_system

    application.run_polling()
