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
import re

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
            "summary_by_id": "/summary/{video_id} (POST)",
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
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/.*[?&]v=)([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$'  # Direct video ID
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def fetch_captions(video_id):
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        
        # Get list of available captions
        captions_response = youtube.captions().list(
            part='snippet',
            videoId=video_id
        ).execute()
        
        if not captions_response.get('items'):
            return None
            
        # Try to find English captions first, then any auto-generated, then any available
        caption_tracks = captions_response['items']
        
        preferred_caption = None
        auto_caption = None
        any_caption = None
        
        for track in caption_tracks:
            snippet = track['snippet']
            language = snippet.get('language', '').lower()
            track_kind = snippet.get('trackKind', '')
            
            if language in ['en', 'en-us', 'en-gb'] and track_kind == 'standard':
                preferred_caption = track
                break
            elif language in ['en', 'en-us', 'en-gb'] and track_kind == 'ASR':
                auto_caption = track
            elif not any_caption:
                any_caption = track
        
        # Use the best available caption
        selected_caption = preferred_caption or auto_caption or any_caption
        
        if selected_caption:
            # Download the caption
            caption_content = youtube.captions().download(
                id=selected_caption['id'],
                tfmt='srt'  # Request SRT format
            ).execute()
            
            if isinstance(caption_content, bytes):
                caption_text = caption_content.decode('utf-8')
            else:
                caption_text = str(caption_content)
            
            # Clean SRT format - remove timestamps and formatting
            import re
            # Remove SRT formatting (numbers, timestamps, empty lines)
            caption_text = re.sub(r'\d+\n\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}\n', '', caption_text)
            caption_text = re.sub(r'\n\s*\n', ' ', caption_text)  # Replace multiple newlines with space
            caption_text = re.sub(r'<[^>]+>', '', caption_text)  # Remove HTML tags
            caption_text = caption_text.strip()
            
            return caption_text if caption_text else None
            
    except Exception:
        pass
    
    return None

def download_audio(url, output_path):
    try:
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'prefer_ffmpeg': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
        # Return the path to the extracted audio file
        possible_extensions = ['.mp3', '.wav', '.m4a', '.webm', '.ogg']
        for ext in possible_extensions:
            audio_file = output_path + ext
            if os.path.exists(audio_file):
                return audio_file
        
        return None
    except Exception:
        return None

def transcribe_audio(audio_path):
    try:
        model = whisper.load_model("tiny")  # Use tiny model for faster processing
        result = model.transcribe(
            audio_path,
            language='en',
            task='transcribe',
            fp16=False,
            verbose=False
        )
        return result['text'].strip() if result['text'] else None
    except Exception:
        return None

def summarize_text(text):
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    try:
        # Try newer OpenAI client first
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that creates concise, informative summaries of YouTube video content. Focus on the main points, key takeaways, and important information discussed in the video."},
                {"role": "user", "content": f"Please summarize this YouTube video transcript in a clear, structured way:\n\n{text}"}
            ],
            max_tokens=600,
            temperature=0.3,
        )
        return response.choices[0].message.content
    except Exception:
        # Fallback to older OpenAI API format
        try:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that creates concise, informative summaries of YouTube video content. Focus on the main points, key takeaways, and important information discussed in the video."},
                    {"role": "user", "content": f"Please summarize this YouTube video transcript in a clear, structured way:\n\n{text}"}
                ],
                max_tokens=600,
                temperature=0.3,
            )
            return response['choices'][0]['message']['content']
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

@app.post("/summarize")
async def summarize_video(request: VideoRequest):
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    return await process_video_summary(video_id)

@app.post("/summary/{video_id}")
async def summarize_video_by_id(video_id: str):
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    if not video_id or len(video_id) != 11:
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID")

    return await process_video_summary(video_id)

async def process_video_summary(video_id: str):
    # Get video info
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            raise HTTPException(status_code=404, detail="Video not found")
        
        video_info = video_response['items'][0]
        video_title = video_info['snippet'].get('title', 'Unknown')
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch video information")

    # Try captions first
    captions = fetch_captions(video_id)
    
    if captions and len(captions.strip()) > 50:  # Ensure we have substantial caption content
        try:
            summary = summarize_text(captions)
            return {
                "summary": summary,
                "method": "captions",
                "video_id": video_id,
                "title": video_title
            }
        except Exception:
            pass  # Continue to audio transcription if caption summarization fails
    
    # Fallback to audio transcription
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_base_path = os.path.join(tmpdir, f"audio_{video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            audio_file = download_audio(video_url, audio_base_path)
            
            if not audio_file or not os.path.exists(audio_file):
                raise HTTPException(status_code=422, detail="Could not extract audio from video. Video may be restricted or unavailable.")
            
            transcript = transcribe_audio(audio_file)
            
            if not transcript or len(transcript.strip()) < 20:
                raise HTTPException(status_code=422, detail="Could not extract meaningful content from video")
            
            summary = summarize_text(transcript)
            
            return {
                "summary": summary,
                "method": "transcription",
                "video_id": video_id,
                "title": video_title
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to process video. Please try again or check if the video is available.")