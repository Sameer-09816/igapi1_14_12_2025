from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
import re

app = FastAPI(title="Instagram Downloader API")

# Headers for SnapInsta
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
    try:
        match = re.search(r'instagram\.com/([^/?]+)', url)
        return match.group(1) if match else "unknown"
    except:
        return "unknown"

async def get_snapinsta_token(client: httpx.AsyncClient, url: str):
    """Fetches the verification token asynchronously."""
    verify_url = "https://snapinsta.to/api/userverify"
    payload = {"url": url}
    
    resp = await client.post(verify_url, data=payload, headers=HEADERS)
    data = resp.json()
    
    if not data.get("success"):
        return None
    return data.get("token")

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
    
    resp = await client.post(search_url, data=payload, headers=HEADERS)
    data = resp.json()
    
    if data.get("status") != "ok":
        return None
    return data.get("data")

# --- Main API Endpoint ---
@app.get("/api/download", response_model=APIResponse)
async def download_media(url: str = Query(..., description="Instagram Post URL")):
    
    # Use httpx for non-blocking requests
    # timeout=10.0 prevents the server from hanging if SnapInsta is slow
    async with httpx.AsyncClient(timeout=10.0) as client:
        
        # 1. Get Token
        token = await get_snapinsta_token(client, url)
        if not token:
            return APIResponse(
                media=[], media_count=0, requested_url=url, 
                status="error", username=extract_username(url)
            )

        # 2. Get Data
        html_content = await get_media_html(client, url, token)
        if not html_content:
            return APIResponse(
                media=[], media_count=0, requested_url=url, 
                status="error", username=extract_username(url)
            )

        # 3. Parse HTML (Using lxml for speed)
        soup = BeautifulSoup(html_content, 'lxml')
        media_items = []
        
        items = soup.find_all(class_="download-items")

        for item in items:
            # Extract Thumbnail
            img_tag = item.find("img")
            thumb_url = img_tag["src"] if img_tag else None

            # Extract Download Link
            btn_div = item.find(class_="download-items__btn")
            link_tag = btn_div.find("a", class_="abutton") if btn_div else None
            
            if not link_tag:
                continue # Skip if no download link
                
            final_url = link_tag.get("href")

            # Determine Type
            icon_tag = item.find("i", class_="icon")
            icon_class = icon_tag.get("class", []) if icon_tag else []
            
            media_type = "unknown"
            if "icon-video" in icon_class:
                media_type = "video"
            elif "icon-dlimage" in icon_class:
                media_type = "image"

            media_items.append(MediaItem(
                caption=None, # SnapInsta removes captions
                media_url=final_url,
                source_type="post",
                thumbnail_url=thumb_url,
                timestamp=None, # SnapInsta removes timestamps
                type=media_type
            ))

        # 4. Return Final Response
        return APIResponse(
            media=media_items,
            media_count=len(media_items),
            requested_url=url,
            source_of_data="GetMedia",
            status="ok",
            username=extract_username(url)
        )
