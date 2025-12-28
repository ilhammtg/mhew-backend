import httpx
from .config import (
    WINDY_API_KEY, 
    WINDY_POINT_FORECAST_URL, 
    BMKG_EQ_URL, 
    BMKG_NOWCAST_RSS
)

async def fetch_json(url: str, timeout: int = 15):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()

async def fetch_bytes(url: str, timeout: int = 15):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.content

async def geocode_location(query: str):
    q = (query or "").strip()
    if not q:
        return None

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "json", "limit": 1}
    headers = {"User-Agent": "MHEWS-Bot/2.1 (telegram; emergency-monitoring)"}

    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if not data:
                return None

            item = data[0]
            return {
                "display_name": item.get("display_name", q),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
            }
    except Exception as e:
        print(f"Geocoding error: {e}")
        return None

async def windy_point_forecast(lat: float, lon: float, model: str = "gfs", parameters=None, levels=None):
    if not WINDY_API_KEY:
        raise RuntimeError("WINDY_API_KEY tidak tersedia di .env")

    if parameters is None:
        parameters = ["wind", "windGust", "temp", "precip", "lclouds", "mclouds", "hclouds", "rh", "pressure"]
    if levels is None:
        levels = ["surface"]

    payload = {
        "lat": float(lat),
        "lon": float(lon),
        "model": model,
        "parameters": parameters,
        "levels": levels,
        "key": WINDY_API_KEY
    }

    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(WINDY_POINT_FORECAST_URL, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Windy HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

async def get_bmkg_eq():
    data = await fetch_json(BMKG_EQ_URL)
    return data["Infogempa"]["gempa"]

async def get_bmkg_forecast_xml(province: str = "Aceh") -> bytes:
    """
    Mengambil XML Digital Forecast berdasarkan provinsi (Default: Aceh).
    Format URL: https://data.bmkg.go.id/DataMKG/MEWS/DigitalForecast/DigitalForecast-{Province}.xml
    """
    # Nama file provinsi umum: Aceh, SumateraUtara, SumateraBarat, Riau, Jambi,
    # SumateraSelatan, Bengkulu, Lampung, BangkaBelitung, KepulauanRiau,
    # DKIGakarta, JawaBarat, JawaTengah, DIJogyakarta, JawaTimur, Banten, Bali,
    # NusaTenggaraBarat, NusaTenggaraTimur, KalimantanBarat, KalimantanTengah,
    # KalimantanSelatan, KalimantanTimur, KalimantanUtara, SulawesiUtara,
    # SulawesiTengah, SulawesiSelatan, SulawesiTenggara, Gorontalo, SulawesiBarat,
    # Maluku, MalukuUtara, PapuaBarat, Papua.
    
    # Normalisasi nama provinsi sederhana (disesuaikan kebutuhan)
    prov_map = {
        "aceh": "Aceh",
        "sumut": "SumateraUtara",
        "jakarta": "DKIJakarta"
    }
    prov_key = prov_map.get(province.lower(), province)
    url = f"https://data.bmkg.go.id/DataMKG/MEWS/DigitalForecast/DigitalForecast-{prov_key}.xml"
    return await fetch_bytes(url)

async def fetch_bmkg_point_forecast_json(adm4_code: str):
    """
    Mengambil data cuaca point forecast (v2) dari BMKG API.
    URL: https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4={code}
    """
    url = "https://api.bmkg.go.id/publik/prakiraan-cuaca"
    params = {"adm4": adm4_code}
    
    # Header minimal supaya tidak diblok
    headers = {
        "User-Agent": "MHEWS-Bot/3.0",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as c:
        r = await c.get(url, params=params)
        r.raise_for_status()
        return r.json()
