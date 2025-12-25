from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()
try:
    client = MongoClient(os.getenv("MONGO_URI"))
    # Perintah 'ping' untuk cek koneksi ke server Atlas
    client.admin.command('ping')
    print("✅ Koneksi ke MongoDB Atlas Berhasil!")
except Exception as e:
    print(f"❌ Koneksi Gagal: {e}")