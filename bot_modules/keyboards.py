from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("📊 Status Sistem", callback_data="menu_status"),
            InlineKeyboardButton("🌍 Info Gempa", callback_data="menu_gempa")
        ],
        [
            InlineKeyboardButton("📍 Kelola Lokasi", callback_data="menu_locations"),
            InlineKeyboardButton("🌦 Cek Cuaca", callback_data="menu_weather")
        ],
        [
            InlineKeyboardButton("⚙️ Pengaturan", callback_data="menu_settings"),
            InlineKeyboardButton("ℹ️ Bantuan", callback_data="menu_help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def location_menu_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("➕ Tambah Lokasi", callback_data="loc_add"),
            InlineKeyboardButton("📋 Lihat Lokasi", callback_data="loc_list")
        ],
        [
            InlineKeyboardButton("🗑 Hapus Lokasi", callback_data="loc_delete"),
            InlineKeyboardButton("◀️ Kembali", callback_data="back_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def settings_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("🌐 Mode: BMKG", callback_data="mode_bmkg"),
            InlineKeyboardButton("🌬 Mode: Windy", callback_data="mode_windy")
        ],
        [
            InlineKeyboardButton("🔄 Mode: Both", callback_data="mode_both"),
        ],
        [
            InlineKeyboardButton("◀️ Kembali", callback_data="back_main")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Kembali ke Menu", callback_data="back_main")]])
