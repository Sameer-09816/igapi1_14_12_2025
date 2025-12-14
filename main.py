from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import httpx
import execjs
from bs4 import BeautifulSoup
import re
import codecs
from urllib.parse import urlparse
import asyncio
from contextlib import asynccontextmanager

# --- Configuration ---
BASE_URL = "https://snapinsta.to"
USER_VERIFY_URL = f"{BASE_URL}/api/userverify"
AJAX_SEARCH_URL = f"{BASE_URL}/api/ajaxSearch"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Referer": "https://snapinsta.to/",
    "Origin": "https://snapinsta.to",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "*/*"
}

# --- Pydantic Models (Strict JSON Structure) ---
class MediaItem(BaseModel):
    caption: Optional[str] = None
    media_url: str
    source_type: str
    thumbnail_url: Optional[str] = None
    timestamp: Optional[str] = None
    type: str

class APIResponse(BaseModel):
    media: List[MediaItem]
    media_count: int
    requested_url: str
    source_of_data: str = "GetMedia"
    status: str
    username: str

# --- Lifecycle Manager (Efficient Client Reuse) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create a global async client on startup
    app.state.client = httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True)
    yield
    # Close client on shutdown
    await app.state.client.aclose()

app = FastAPI(lifespan=lifespan)

# --- Helper Functions ---

def get_url_metadata(url: str):
    path = urlparse(url).path
    if "/reel/" in path: source_type = "reel"
    elif "/stories/" in path: source_type = "story"
    elif "/p/" in path: source_type = "post"
    else: source_type = "post"

    username = "unknown"
    parts = [p for p in path.split('/') if p]
    if len(parts) > 1 and parts[0] not in ['p', 'reel', 'stories', 'explore']:
        username = parts[0]
    return source_type, username

def decode_js_logic_sync(obfuscated_js: str):
    """Sync function to be run in thread pool"""
    try:
        split_index = obfuscated_js.rfind("eval(")
        if split_index == -1: return None
        setup = obfuscated_js[:split_index]
        execution = obfuscated_js[split_index:]
        final_script = f"function get_result() {{ var eval = function(x) {{ return x; }}; {setup} return {execution} }}"
        ctx = execjs.compile(final_script)
        return ctx.call("get_result")
    except Exception:
        return None

def extract_html_string(js_code: str):
    if not js_code: return None
    if js_code.strip().startswith("<"): return js_code
    match = re.search(r'innerHTML\s*=\s*"(.*?)";', js_code, re.DOTALL)
    if match:
        try: return codecs.decode(match.group(1), 'unicode_escape')
        except: pass
    return None

def parse_html(html_content: str, source_type_hint: str):
    try:
        soup = BeautifulSoup(html_content, 'lxml')
    except:
        soup = BeautifulSoup(html_content, 'html.parser')

    results = []
    scraped_username = None
    
    # Try to find username in the result HTML
    user_elem = soup.select_one('.download-top .abutton') 
    if user_elem and user_elem.get('href'):
        scraped_username = user_elem['href'].rstrip('/').split('/')[-1]

    items = soup.select('.download-items')
    
    # Fallback for different HTML structure
    if not items:
        box = soup.find(class_='download-box')
        if box: items = box.find_all('li')

    for item in items:
        # Robust link finding
        link_tag = None
        # 1. Try specific button class
        btn_div = item.select_one('.download-items__btn')
        if btn_div: link_tag = btn_div.find('a')
        
        # 2. Try .abutton class directly
        if not link_tag: link_tag = item.select_one('a.abutton')
        
        # 3. Generic fallback
        if not link_tag: link_tag = item.find('a', href=True)

        if link_tag and link_tag.get('href'):
            media_url = link_tag['href']
            
            # Robust thumbnail finding
            thumb_url = None
            thumb_div = item.select_one('.download-items__thumb')
            if thumb_div and thumb_div.find('img'):
                thumb_url = thumb_div.find('img')['src']

            # Determine type
            is_video = bool(item.select_one('.icon-dlvideo') or "mp4" in media_url or "video" in str(item).lower())
            
            if "javascript" not in media_url and media_url != "#":
                results.append(MediaItem(
                    caption=None,
                    media_url=media_url,
                    source_type=source_type_hint,
                    thumbnail_url=thumb_url,
                    timestamp=None,
                    type="video" if is_video else "image"
                ))

    return results, scraped_username

# --- API Endpoint ---

@app.get("/download", response_model=APIResponse)
async def download_media(url: str = Query(..., description="Instagram URL")):
    client: httpx.AsyncClient = app.state.client
    
    # 1. Metadata
    source_type, url_username = get_url_metadata(url)

    try:
        # 2. Verify (Async)
        r1 = await client.post(USER_VERIFY_URL, data={"url": url})
        if r1.status_code != 200:
            raise HTTPException(status_code=503, detail="Provider unavailable")
        
        try:
            r1_json = r1.json()
        except:
            raise HTTPException(status_code=500, detail="Invalid JSON from provider")

        if not r1_json.get("success"):
            raise HTTPException(status_code=400, detail="Verification failed or invalid URL")
        
        token = r1_json.get("token")

        # 3. Search (Async)
        payload = {
            "q": url, "t": "media", "v": "v2", "lang": "en", "cftoken": token
        }
        r2 = await client.post(AJAX_SEARCH_URL, data=payload)
        
        try:
            obfuscated_data = r2.json().get("data")
        except:
            raise HTTPException(status_code=500, detail="Invalid JSON from search")

        if not obfuscated_data:
            raise HTTPException(status_code=404, detail="No media found")

        # 4. Decode JS (Run blocking code in thread pool)
        # execjs is blocking, so we offload it to keep the server fast
        js_logic = await asyncio.to_thread(decode_js_logic_sync, obfuscated_data)
        
        if not js_logic:
            raise HTTPException(status_code=500, detail="De-obfuscation failed")

        html_content = extract_html_string(js_logic)
        if not html_content:
            raise HTTPException(status_code=500, detail="HTML extraction failed")

        # 5. Parse HTML
        media_list, html_username = parse_html(html_content, source_type)
        
        final_username = html_username if html_username else url_username

        if not media_list:
            raise HTTPException(status_code=404, detail="No media found in response")

        return APIResponse(
            media=media_list,
            media_count=len(media_list),
            requested_url=url,
            status="ok",
            username=final_username
        )

    except httpx.RequestError as e:
        raise HTTPException(status_code=504, detail=f"Request error: {str(e)}")
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/")
async def health_check():
    return {"status": "running", "docs": "/docs"}
