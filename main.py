import httpx
import re
import urllib.parse
from fastapi import FastAPI, HTTPException, Query
from bs4 import BeautifulSoup
from contextlib import asynccontextmanager

# --- LOGIC CLASS ---
class SaveClipAPI:
    def __init__(self):
        self.base_url = "https://saveclip.app"
        self.ajax_url = "https://v3.saveclip.app/api/ajaxSearch"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Referer": "https://saveclip.app/en",
            "X-Requested-With": "XMLHttpRequest"
        }

    def _base_decode(self, d, e, f):
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"
        h, i = alphabet[:e], alphabet[:f]
        decimal_value = sum(h.index(c) * (e ** idx) for idx, c in enumerate(reversed(d)) if c in h)
        if decimal_value == 0: return "0"
        res = ""
        while decimal_value > 0:
            res = i[decimal_value % f] + res
            decimal_value //= f
        return res

    def _decrypt_js(self, data_field):
        if data_field.strip().startswith("<ul") or "download-box" in data_field:
            return data_field
        try:
            args_match = re.search(r'\("(.+?)",(\d+),"(.+?)",(\d+),(\d+),(\d+)\)', data_field)
            if not args_match: return data_field
            h, u, n, t, e, r = args_match.groups()
            u, t, e, r = int(u), int(t), int(e), int(r)
            decoded_str = ""
            i, delimiter = 0, n[e]
            while i < len(h):
                seg = ""
                while i < len(h) and h[i] != delimiter:
                    seg += h[i]
                    i += 1
                for j in range(len(n)): seg = seg.replace(n[j], str(j))
                if seg: decoded_str += chr(int(self._base_decode(seg, e, 10)) - t)
                i += 1
            decoded_unquoted = urllib.parse.unquote(decoded_str)
            html_in_js = re.search(r'innerHTML\s*=\s*"(.*?)";', decoded_unquoted)
            if html_in_js:
                return html_in_js.group(1).replace('\\"', '"').replace('\\/', '/')
            return decoded_unquoted
        except:
            return data_field

    def _parse_html_to_json(self, html_content, requested_url):
        if "\\u" in html_content:
            html_content = html_content.encode().decode('unicode_escape')
        
        soup = BeautifulSoup(html_content, "html.parser")
        media_list = []
        
        for li in soup.find_all("li"):
            format_icon = li.find("i", class_="icon")
            is_video = "icon-dlvideo" in str(format_icon)
            
            img_tag = li.find("img")
            thumbnail = ""
            if img_tag:
                thumbnail = img_tag.get("data-src") or img_tag.get("src") or ""
                if thumbnail.startswith("/"): thumbnail = self.base_url + thumbnail

            media_url = ""
            options = li.find_all("option")
            if options:
                media_url = options[0]["value"]
            else:
                for link in li.find_all("a", href=True):
                    if is_video and "thumbnail" in link.text.lower(): continue
                    if "download" in link.text.lower():
                        media_url = link["href"]
                        break
            
            if media_url:
                media_list.append({
                    "caption": "", 
                    "media_url": media_url,
                    "source_type": "post",
                    "thumbnail_url": thumbnail,
                    "timestamp": "recent",
                    "type": "video" if is_video else "image"
                })

        user_match = re.search(r'instagram\.com/([^/]+)', requested_url)
        username = user_match.group(1) if user_match else "unknown"

        return {
            "media": media_list,
            "media_count": len(media_list),
            "requested_url": requested_url,
            "source_of_data": "GetMedia",
            "status": "ok",
            "username": username
        }

# --- FASTAPI SETUP ---
save_clip = SaveClipAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize a global async client for connection pooling
    app.state.client = httpx.AsyncClient(headers=save_clip.headers, timeout=20.0, follow_redirects=True)
    yield
    await app.state.client.aclose()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/api/download")
async def get_media(url: str = Query(..., description="Instagram URL")):
    client = app.state.client
    try:
        # 1. Get Tokens
        home = await client.get(f"{save_clip.base_url}/en")
        tokens = re.findall(r'k_(?:exp|token)\s*=\s*["\']([^"\']+)["\']', home.text)
        if len(tokens) < 2: raise Exception("Token generation failed")
        
        # 2. Verify
        verify = await client.post(f"{save_clip.base_url}/api/userverify", data={"url": url})
        cftoken = verify.json().get("token")

        # 3. Search
        payload = {"k_exp": tokens[0], "k_token": tokens[1], "q": url, "t": "media", "lang": "en", "v": "v2", "cftoken": cftoken}
        search_req = await client.post(save_clip.ajax_url, data=payload)
        search_res = search_req.json()
        
        # 4. Decrypt & Parse
        html = save_clip._decrypt_js(search_res.get("data", ""))
        return save_clip._parse_html_to_json(html, url)
        
    except Exception as e:
        return {"status": "error", "message": str(e)}
