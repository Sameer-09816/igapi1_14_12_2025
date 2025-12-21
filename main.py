import re
import httpx
import urllib.parse
from fastapi import FastAPI, HTTPException, Query
from bs4 import BeautifulSoup
from contextlib import asynccontextmanager

# --- Lifespan Manager for Efficiency ---
# Reuses the same connection pool for all requests
http_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Referer": "https://snapinsta.to/",
            "Origin": "https://snapinsta.to",
        },
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    )
    yield
    await http_client.aclose()

app = FastAPI(title="Pro Instagram Downloader", lifespan=lifespan)

# --- De-obfuscator Logic ---

def _0xe0c(d, e, f):
    charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    h, i = charset[0:e], charset[0:f]
    j = 0
    for index, char in enumerate(reversed(d)):
        if char in h: j += h.index(char) * (e ** index)
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
        t, e, r = int(t), int(e), ""
        i = 0
        while i < len(h):
            s = ""
            while i < len(h) and h[i] != n[e]:
                s += h[i]
                i += 1
            for j in range(len(n)): s = s.replace(n[j], str(j))
            try: r += chr(int(_0xe0c(s, e, 10)) - t)
            except: pass
            i += 1
        decoded = urllib.parse.unquote(r)
        return decoded.replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')
    except: return js_content

def clean_url(url: str) -> str:
    if not url: return ""
    return re.sub(r'^[\"\\]+|[\"\\]+$', '', url.strip())

# --- API Logic ---

@app.get("/api/download")
async def download(url: str = Query(..., description="Instagram URL")):
    if "instagram.com" not in url:
        raise HTTPException(status_code=400, detail="Invalid Instagram URL")

    username = "unknown"
    user_match = re.search(r"instagram\.com/([^/]+)/", url)
    if user_match: username = user_match.group(1)

    try:
        # Step 1: Verify URL
        v_res = await http_client.post("https://snapinsta.to/api/userverify", data={"url": url})
        v_data = v_res.json()
        token = v_data.get("token")
        
        # Step 2: Search Media
        s_res = await http_client.post("https://snapinsta.to/api/ajaxSearch", data={
            "q": url, "t": "media", "v": "v2", "lang": "en", "cftoken": token
        })
        raw_data = s_res.json().get("data", "")
        
        if "eval(function" in raw_data:
            html = decode_snapinsta(raw_data)
        else:
            html = raw_data

        # Step 3: Parse
        # Using 'lxml' for speed
        soup = BeautifulSoup(html, "lxml")
        media_results = []
        items = soup.find_all("div", class_="download-items") or soup.find_all("li")

        for item in items:
            btn = item.find("a", class_="abutton")
            if not btn: continue
            
            media_type = "video" if "video" in btn.get_text().lower() else "image"
            
            # Highest quality check
            select = item.find("select")
            media_url = select.find("option")["value"] if select and select.find("option") else btn.get("href", "")
            
            img = item.find("img")
            thumb_url = ""
            if img:
                thumb_url = img.get("data-src") or img.get("src") or img.get("data-lazy-src")
            
            if not thumb_url or "loader.gif" in thumb_url:
                thumb_url = media_url

            media_results.append({
                "caption": "Instagram Content",
                "media_url": clean_url(media_url),
                "source_type": "post",
                "thumbnail_url": clean_url(thumb_url),
                "timestamp": "N/A",
                "type": media_type
            })

        return {
            "media": media_results,
            "media_count": len(media_results),
            "requested_url": url,
            "source_of_data": "GetMedia",
            "status": "ok",
            "username": username
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"API Error: {str(e)}")
