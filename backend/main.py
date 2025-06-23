from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import openai
import os
import tempfile
import subprocess
from googleapiclient.discovery import build
import yt_dlp
import whisper
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import requests
import json

# Load environment variables from .env
load_dotenv()

app = FastAPI(
    title="YouTube Video Summarizer API",
    description="API for summarizing YouTube videos using captions or audio transcription",
    version="1.0.0"
)

# CORS settings for frontend
origins = [
    "*",  # Allow all origins for now - restrict this in production
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Set your API keys here
YOUTUBE_DATA_API_KEY = os.getenv("YOUTUBE_DATA_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# Updated OpenAI client initialization (for newer versions)
if OPENAI_API_KEY:
    openai.api_key = OPENAI_API_KEY

class VideoRequest(BaseModel):
    url: str

class AuthRequest(BaseModel):
    code: str
    redirect_uri: str

# ROOT PATH FIX - Add this endpoint
@app.get("/")
async def root():
    return {
        "message": "YouTube Video Summarizer API is running!",
        "endpoints": {
            "summarize": "/summarize (POST)",
            "auth": "/auth (POST)",
            "docs": "/docs (GET)"
        }
    }

@app.post("/auth")
async def exchange_code_for_token(auth_request: AuthRequest):
    """Exchange OAuth authorization code for access token"""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Google OAuth credentials not configured")
    
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": auth_request.code,
        "grant_type": "authorization_code",
        "redirect_uri": auth_request.redirect_uri
    }
    
    try:
        response = requests.post(token_url, data=token_data)
        response.raise_for_status()
        token_info = response.json()
        
        return {
            "access_token": token_info.get("access_token"),
            "refresh_token": token_info.get("refresh_token"),
            "expires_in": token_info.get("expires_in"),
            "token_type": token_info.get("token_type", "Bearer")
        }
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")

def get_video_id(url):
    # Extract video ID from URL
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return None

def fetch_captions(video_id):
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        captions = youtube.captions().list(part='snippet', videoId=video_id).execute()
        if 'items' in captions and captions['items']:
            caption_id = captions['items'][0]['id']
            caption = youtube.captions().download(id=caption_id).execute()
            return caption.get('body', '')
        return ''
    except Exception:
        return ''

def download_audio(url, output_path):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_path,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def transcribe_audio(audio_path):
    model = whisper.load_model("base")
    result = model.transcribe(audio_path)
    return result['text']

def summarize_text(text):
    try:
        # Updated for newer OpenAI library versions
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Summarize the following YouTube video transcript in a concise way. Provide key points and main takeaways."},
                {"role": "user", "content": text}
            ],
            max_tokens=500,
            temperature=0.5,
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        # Fallback for newer OpenAI client
        try:
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Summarize the following YouTube video transcript in a concise way. Provide key points and main takeaways."},
                    {"role": "user", "content": text}
                ],
                max_tokens=500,
                temperature=0.5,
            )
            return response.choices[0].message.content
        except Exception as fallback_error:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(fallback_error)}")

@app.post("/summarize")
async def summarize_video(request: VideoRequest):
    # Validate API keys
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    # Try to fetch captions first
    try:
        captions = fetch_captions(video_id)
    except Exception:
        captions = ""

    if captions and captions.strip():
        # Summarize captions
        try:
            summary = summarize_text(captions)
            return {
                "summary": summary,
                "method": "captions",
                "video_id": video_id
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to summarize captions: {str(e)}")
    else:
        # Download audio and transcribe as fallback
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, f"audio_{video_id}")
            try:
                download_audio(request.url, audio_path)
                # The actual file will have .mp3 extension after processing
                mp3_path = f"{audio_path}.mp3"
                if os.path.exists(mp3_path):
                    transcript = transcribe_audio(mp3_path)
                    summary = summarize_text(transcript)
                    return {
                        "summary": summary,
                        "method": "transcription",
                        "video_id": video_id
                    }
                else:
                    raise HTTPException(status_code=500, detail="Audio file not created")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to process video: {str(e)}")