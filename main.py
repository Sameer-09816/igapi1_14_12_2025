import re
import httpx
import urllib.parse
import logging
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from bs4 import BeautifulSoup
from contextlib import asynccontextmanager

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api")

# Persistent HTTP Client for efficiency
class State:
    client: httpx.AsyncClient = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the client on startup
    State.client = httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://snapinsta.to/",
            "Origin": "https://snapinsta.to",
        },
        timeout=20.0,
        follow_redirects=True
    )
    yield
    # Close client on shutdown
    await State.client.aclose()

app = FastAPI(title="Instagram Downloader API", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# --- DE-OBFUSCATOR LOGIC ---

def _base_convert(d, e, f):
    charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    h, i = charset[0:e], charset[0:f]
    j = 0
    for index, char in enumerate(reversed(d)):
        if char in h:
            j += h.index(char) * (e ** index)
    if j == 0: return "0"
    k = ""
    while j > 0:
        k = i[j % f] + k
        j = (j - (j % f)) // f
    return k

def decode_snapinsta(js_content: str) -> str:
    try:
        matches = re.search(r'}\("([^"]+)",(\d+),"([^"]+)",(\d+),(\d+),(\d+)\)\)', js_content)
        if not matches: return js_content
        h, u, n, t, e, r_val = matches.groups()
        t, e = int(t), int(e)
        r, i = "", 0
        while i < len(h):
            s = ""
            while i < len(h) and h[i] != n[e]:
                s += h[i]
                i += 1
            for j in range(len(n)):
                s = s.replace(n[j], str(j))
            try:
                r += chr(int(_base_convert(s, e, 10)) - t)
            except: pass
            i += 1
        decoded = urllib.parse.unquote(r)
        return decoded.replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')
    except Exception as e:
        logger.error(f"Decoding error: {e}")
        return js_content

# --- HELPERS ---

def clean_url(url: str) -> str:
    if not url: return ""
    return re.sub(r'^[\"\\]+|[\"\\]+$', '', url.strip())

def extract_username(url: str) -> str:
    match = re.search(r"instagram\.com/([^/?#&]+)", url)
    return match.group(1) if match else "instagram_user"

async def get_snapinsta_data(instagram_url: str):
    # Step 1: Verify URL
    v_res = await State.client.post("https://snapinsta.to/api/userverify", data={"url": instagram_url})
    v_json = v_res.json()
    if not v_json.get("success"):
        raise HTTPException(status_code=400, detail="SnapInsta verification failed")
    
    # Step 2: Fetch Data
    search_data = {
        "q": instagram_url, "t": "media", "v": "v2", 
        "lang": "en", "cftoken": v_json.get("token")
    }
    s_res = await State.client.post("https://snapinsta.to/api/ajaxSearch", data=search_data)
    s_json = s_res.json()
    
    raw_data = s_json.get("data", "")
    if "eval(function" in raw_data:
        return decode_snapinsta(raw_data)
    return raw_data

# --- ROUTES ---

@app.get("/api/download")
async def download(url: str = Query(..., example="https://www.instagram.com/p/DSXggmwEoKg/")):
    if "instagram.com" not in url:
        raise HTTPException(status_code=400, detail="Invalid Instagram URL")

    html_content = await get_snapinsta_data(url)
    soup = BeautifulSoup(html_content, "html.parser")
    
    media_results = []
    items = soup.find_all("div", class_="download-items") or soup.find_all("li")

    for item in items:
        btn = item.find("a", class_="abutton")
        if not btn: continue
        
        btn_text = btn.get_text(strip=True).lower()
        media_type = "video" if "video" in btn_text else "image"

        # Highest quality logic
        select_tag = item.find("select")
        media_url = select_tag.find("option")["value"] if select_tag and select_tag.find("option") else btn.get("href", "")

        # Thumbnail logic
        img_tag = item.find("img")
        thumb_url = ""
        if img_tag:
            thumb_url = img_tag.get("data-src") or img_tag.get("src") or ""
            if "loader.gif" in thumb_url: thumb_url = ""

        media_results.append({
            "caption": "Instagram Content",
            "media_url": clean_url(media_url),
            "source_type": "post",
            "thumbnail_url": clean_url(thumb_url) if thumb_url else clean_url(media_url),
            "timestamp": "N/A",
            "type": media_type
        })

    return {
        "media": media_results,
        "media_count": len(media_results),
        "requested_url": url,
        "source_of_data": "GetMedia",
        "status": "ok",
        "username": extract_username(url)
    }

@app.get("/health")
async def health():
    return {"status": "healthy"}
