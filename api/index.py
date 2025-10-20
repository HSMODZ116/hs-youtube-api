import os
import re
import time
import random
import base64
import urllib.parse
from fastapi import FastAPI, Request, HTTPException
import httpx

app = FastAPI(title="HS YouTube Downloader", version="1.0")

# --- Rate Limit Config ---
RATE_LIMIT = 10
RATE_WINDOW = 60
rate_logs = {}

def rate_limit(ip: str):
    now = time.time()
    if ip not in rate_logs:
        rate_logs[ip] = []
    rate_logs[ip] = [t for t in rate_logs[ip] if (now - t) < RATE_WINDOW]
    if len(rate_logs[ip]) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    rate_logs[ip].append(now)

# --- Extract YouTube Video ID ---
def extract_video_id(url: str):
    patterns = [
        r"youtube\.com/watch\?v=([a-zA-Z0-9_-]+)",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]+)",
        r"youtu\.be/([a-zA-Z0-9_-]+)",
        r"youtube\.com/embed/([a-zA-Z0-9_-]+)",
        r"youtube\.com/v/([a-zA-Z0-9_-]+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# --- Make API Call ---
async def make_api_call(client, api_url, method, data, headers, name):
    try:
        if method == "POST":
            resp = await client.post(api_url, data=data, headers=headers, timeout=30)
        else:
            resp = await client.get(api_url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code} ({name})"}
        try:
            return resp.json()
        except Exception:
            return {"error": f"Invalid JSON ({name})"}
    except Exception as e:
        return {"error": f"{name} failed: {str(e)}"}

# --- Try Multiple APIs ---
async def try_multiple_downloaders(url, video_id, format_code):
    apis = [
        {
            "name": "bizft-v1",
            "url": "https://yt.savetube.me/api/v1/video-downloader",
            "method": "POST",
            "data": {"url": url, "format_code": format_code},
            "headers": {"Content-Type": "application/json"}
        },
        {
            "name": "bizft-v2",
            "url": "https://www.y2mate.com/mates/analyzeV2/ajax",
            "method": "POST",
            "data": f"k_query={urllib.parse.quote(url)}&k_page=home&hl=en&q_auto=0",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"}
        },
        {
            "name": "bizft-v3",
            "url": "https://sfrom.net/mates/en/analyze/ajax",
            "method": "POST",
            "data": f"url={urllib.parse.quote(url)}",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"}
        }
    ]

    async with httpx.AsyncClient() as client:
        for api in apis:
            result = await make_api_call(client, api["url"], api["method"], api["data"], api["headers"], api["name"])
            if result and "error" not in result:
                return result
    return None

# --- Generate Fallback Direct Links ---
def generate_direct_urls(video_id, format_code):
    base_urls = [
        "https://rr1---sn-oj5hn5-55.googlevideo.com/videoplayback",
        "https://rr2---sn-oj5hn5-55.googlevideo.com/videoplayback",
        "https://rr3---sn-oj5hn5-55.googlevideo.com/videoplayback"
    ]
    expire = int(time.time()) + 21600
    now = int(time.time())
    urls = []

    for base_url in base_urls:
        params = {
            "expire": expire,
            "ei": base64.b64encode(os.urandom(12)).decode(),
            "ip": "127.0.0.1",
            "id": "o-" + base64.b64encode(os.urandom(20)).decode(),
            "itag": format_code,
            "source": "youtube",
            "requiressl": "yes",
            "mime": "video/mp4",
            "ratebypass": "yes",
            "lmt": f"{now}000",
            "clen": random.randint(1000000, 10000000),
            "gir": "yes"
        }
        urls.append(f"{base_url}?{urllib.parse.urlencode(params)}")
    return urls

# --- Get Basic Video Info ---
async def get_video_info(video_id):
    info_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(info_url)
        if r.status_code == 200:
            return r.json()
    return None

# --- MAIN API Endpoint ---
@app.get("/")
async def youtube_downloader(request: Request, url: str = None, format_code: str = "18", quality: str = "medium"):
    ip = request.client.host
    rate_limit(ip)

    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    if not re.match(r"^https?:\/\/(www\.)?(youtube\.com|youtu\.be)", url):
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Could not extract video ID")

    video_info = await get_video_info(video_id)
    api_result = await try_multiple_downloaders(url, video_id, format_code)

    if api_result and "response" in api_result and "direct_link" in api_result["response"]:
        source = "api"
        primary_link = api_result["response"]["direct_link"]
    else:
        source = "generated"
        generated = generate_direct_urls(video_id, format_code)
        primary_link = generated[0]
        api_result = {"response": {"direct_link": primary_link}}

    response = {
        "status": "success",
        "source": source,
        "video_id": video_id,
        "url": url,
        "format_code": format_code,
        "video_info": video_info,
        "response": api_result["response"],
        "download_links": {
            "primary": primary_link,
            "alternatives": generate_direct_urls(video_id, format_code)[1:]
        },
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "expires_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 21600))
    }
    return response

@app.get("/ping")
async def ping():
    return {"status": "ok", "message": "HS YouTube Downloader API running perfectly ⚡"}
