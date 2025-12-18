from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
import re

app = FastAPI(title="Instagram Downloader API")

# Headers to mimic a browser request
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://snapinsta.to/",
    "Origin": "https://snapinsta.to",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest"
}

# --- Pydantic Models for Response Structure ---
class MediaItem(BaseModel):
    caption: Optional[str] = None
    media_url: str
    source_type: str = "post"
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

# --- Helper Functions ---
def extract_username(url: str) -> str:
    """Extracts username from Instagram URL."""
    try:
        match = re.search(r'instagram\.com/([^/?]+)', url)
        return match.group(1) if match else "unknown"
    except:
        return "unknown"

async def get_snapinsta_token(client: httpx.AsyncClient, url: str):
    """Fetches the verification token asynchronously."""
    verify_url = "https://snapinsta.to/api/userverify"
    payload = {"url": url}
    
    try:
        resp = await client.post(verify_url, data=payload, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            return None
        return data.get("token")
    except Exception:
        return None

async def get_media_html(client: httpx.AsyncClient, url: str, token: str):
    """Fetches the HTML content asynchronously."""
    search_url = "https://snapinsta.to/api/ajaxSearch"
    payload = {
        "q": url,
        "t": "media",
        "v": "v2",
        "lang": "en",
        "cftoken": token
    }
    
    try:
        resp = await client.post(search_url, data=payload, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            return None
        return data.get("data")
    except Exception:
        return None

# --- Main API Endpoint ---
@app.get("/api/download", response_model=APIResponse)
async def download_media(url: str = Query(..., description="Instagram Post URL")):
    
    # Use httpx for non-blocking requests with a reasonable timeout
    async with httpx.AsyncClient(timeout=15.0) as client:
        
        # 1. Get Token
        token = await get_snapinsta_token(client, url)
        username = extract_username(url)

        if not token:
            return APIResponse(
                media=[], media_count=0, requested_url=url, 
                status="error", username=username
            )

        # 2. Get Data
        html_content = await get_media_html(client, url, token)
        if not html_content:
            return APIResponse(
                media=[], media_count=0, requested_url=url, 
                status="error", username=username
            )

        # 3. Parse HTML (Using lxml for speed)
        soup = BeautifulSoup(html_content, 'lxml')
        media_items = []
        
        # Find all media containers
        items = soup.find_all(class_="download-items")

        for item in items:
            # --- FIX FOR THUMBNAIL URL (Lazy Loading) ---
            img_tag = item.find("img")
            thumb_url = None
            
            if img_tag:
                # Priority 1: 'data-src' (used for lazy loaded images)
                if img_tag.get("data-src"):
                    thumb_url = img_tag["data-src"]
                # Priority 2: 'src' (standard images)
                elif img_tag.get("src"):
                    thumb_url = img_tag["src"]

                # If thumb_url is still just the loader gif, try to find another attribute
                if thumb_url and "/loader.gif" in thumb_url:
                    # In extremely rare cases, if data-src is missing but it's a loader, set to None
                    # usually data-src is present though.
                    pass 

            # Extract Download Link (The main button)
            btn_div = item.find(class_="download-items__btn")
            link_tag = btn_div.find("a", class_="abutton") if btn_div else None
            
            if not link_tag:
                continue # Skip if no download link found
                
            final_media_url = link_tag.get("href")

            # Determine Media Type based on icon class
            icon_tag = item.find("i", class_="icon")
            icon_class = icon_tag.get("class", []) if icon_tag else []
            
            media_type = "unknown"
            if "icon-video" in icon_class:
                media_type = "video"
            elif "icon-dlimage" in icon_class:
                media_type = "image"
            
            # Construct item
            media_items.append(MediaItem(
                caption=None, # SnapInsta does not provide caption
                media_url=final_media_url,
                source_type="post",
                thumbnail_url=thumb_url,
                timestamp=None, # SnapInsta does not provide timestamp
                type=media_type
            ))

        # 4. Return Final Response
        return APIResponse(
            media=media_items,
            media_count=len(media_items),
            requested_url=url,
            source_of_data="GetMedia",
            status="ok",
            username=username
        )

# For running locally without Docker:
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
