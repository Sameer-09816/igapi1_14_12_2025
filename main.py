import re
import httpx
import urllib.parse
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GzipMiddleware
from bs4 import BeautifulSoup

# --- UTILS & DECODER ---

def _0xe0c(d, e, f):
    charset = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
    h, i = charset[:e], charset[:f]
    j = 0
    for index, char in enumerate(reversed(d)):
        if char in h: j += h.index(char) * (e ** index)
    if j == 0: return "0"
    k = ""
    while j > 0:
        k = i[j % f] + k
        j //= f
    return k

def decode_snapinsta(js_content: str) -> str:
    try:
        matches = re.search(r'}\("([^"]+)",(\d+),"([^"]+)",(\d+),(\d+),(\d+)\)\)', js_content)
        if not matches: return js_content
        h, _, n, t, e, _ = matches.groups()
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
        return urllib.parse.unquote(r).replace('\\"', '"').replace('\\/', '/').replace('\\\\', '\\')
    except: return js_content

def clean_url(url: str) -> str:
    if not url: return ""
    url = url.strip(' "\'\\')
    return re.sub(r'^[\"\\]+|[\"\\]+$', '', url)

# --- FASTAPI STATE ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize a shared persistent client for connection pooling
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    timeout = httpx.Timeout(20.0, connect=5.0)
    app.state.client = httpx.AsyncClient(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://snapinsta.to/",
            "Origin": "https://snapinsta.to"
        },
        timeout=timeout,
        limits=limits,
        follow_redirects=True
    )
    yield
    await app.state.client.aclose()

app = FastAPI(title="Pro Instagram Downloader", lifespan=lifespan)
app.add_middleware(GzipMiddleware, minimum_size=1000)

# --- LOGIC ---

async def fetch_media(instagram_url: str):
    client = app.state.client
    
    # 1. Verify URL and get Token
    v_res = await client.post("https://snapinsta.to/api/userverify", data={"url": instagram_url})
    if v_res.status_code != 200 or not v_res.json().get("success"):
        raise HTTPException(status_code=400, detail="Could not verify Instagram URL")
    token = v_res.json().get("token")

    # 2. Get Media HTML/JS
    search_data = {"q": instagram_url, "t": "media", "v": "v2", "lang": "en", "cftoken": token}
    s_res = await client.post("https://snapinsta.to/api/ajaxSearch", data=search_data)
    raw_data = s_res.json().get("data", "")

    # 3. Decode if Video (Obfuscated)
    if "eval(function" in raw_data:
        raw_data = decode_snapinsta(raw_data)
    
    # 4. Parse using lxml for speed
    soup = BeautifulSoup(raw_data, "lxml")
    media_list = []
    items = soup.find_all("div", class_="download-items") or soup.find_all("li")

    for item in items:
        btn = item.find("a", class_="abutton")
        if not btn: continue
        
        media_type = "video" if "video" in btn.get_text().lower() else "image"
        
        # Quality Selection
        select = item.find("select")
        media_url = select.find("option")["value"] if select and select.find("option") else btn.get("href")
        
        # Thumbnail Extraction
        img = item.find("img")
        thumb = ""
        if img:
            thumb = img.get("data-src") or img.get("src") or img.get("data-lazy-src")
            if "loader.gif" in str(thumb): thumb = ""

        media_list.append({
            "caption": "Instagram Content",
            "media_url": clean_url(media_url),
            "source_type": "post",
            "thumbnail_url": clean_url(thumb) if thumb else clean_url(media_url),
            "timestamp": "N/A",
            "type": media_type
        })
    
    return media_list

@app.get("/api/download")
async def download(url: str = Query(..., example="https://www.instagram.com/p/DSXggmwEoKg/")):
    try:
        results = await fetch_media(url)
        return {
            "status": "ok",
            "media": results,
            "media_count": len(results),
            "username": re.search(r"instagram\.com/([^/]+)/", url).group(1) if "instagram.com" in url else "unknown",
            "requested_url": url,
            "source_of_data": "GetMedia"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
