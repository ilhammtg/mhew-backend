
import csv
import os
import re
from pymongo import MongoClient, ASCENDING, TEXT
from dotenv import load_dotenv

# Load Env
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")

if not MONGO_URI:
    print("❌ MONGO_URI missing in .env")
    exit(1)

# Connect DB
try:
    client = MongoClient(MONGO_URI)
    db = client["emergency_db"]
    col = db["wilayah_bmkg"]
    print("✅ Connected to MongoDB")
except Exception as e:
    print(f"❌ DB Connection Error: {e}")
    exit(1)

def normalize_name(s):
    return re.sub(r"\s+", " ", s.strip().upper())

def determine_level(code):
    dots = code.count(".")
    if dots == 0: return "PROVINSI"
    if dots == 1: return "KABUPATEN/KOTA"
    if dots == 2: return "KECAMATAN"
    if dots == 3: return "DESA/KELURAHAN"
    return "UNKNOWN"

def run_import():
    csv_path = "base.csv"
    if not os.path.exists(csv_path):
        print(f"❌ File {csv_path} not found!")
        return

    print("🚀 Starting Import...")
    
    # Reset Collection (Optional: or upsert?)
    # User said "Bulk Import... ke koleksi baru". Let's drop first to be clean.
    col.drop()
    print("🗑️ Dropped existing 'wilayah_bmkg' collection.")

    docs = []
    batch_size = 5000
    
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2: continue
            
            code = row[0].strip()
            name = normalize_name(row[1])
            level = determine_level(code)
            
            # Parent logic
            parent = None
            if "." in code:
                parent = code.rsplit(".", 1)[0]

            doc = {
                "_id": code,
                "name": name,
                "level": level,
                "parent": parent,
                # "location": None # NO COORDINATES IN CSV
            }
            docs.append(doc)
            
            if len(docs) >= batch_size:
                col.insert_many(docs)
                print(f"📦 Imported {batch_size} rows...")
                docs = []

        if docs:
            col.insert_many(docs)
            print(f"📦 Imported remaining {len(docs)} rows.")

    print("🎉 Import Finished.")
    
    # Create Indexes
    print("⚙️ Creating Indexes...")
    col.create_index([("name", TEXT)]) # Text search for fallback
    col.create_index([("parent", ASCENDING)])
    # col.create_index([("location", "2dsphere")]) # CANNOT DO THIS yet
    print("✅ Indexes Created (Name: TEXT, Parent: ASC).")
    
    count = col.count_documents({})
    print(f"📊 Total Documents: {count}")

if __name__ == "__main__":
    run_import()
