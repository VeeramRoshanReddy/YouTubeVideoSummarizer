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
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi

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
    """Extract video ID from various YouTube URL formats"""
    if not url:
        return None
    
    # Clean the URL
    url = url.strip()
    
    # Parse the URL
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    
    # Handle different YouTube URL patterns
    
    # 1. Standard watch URL: https://www.youtube.com/watch?v=VIDEO_ID
    # 4. Watch URL with additional parameters: https://www.youtube.com/watch?v=VIDEO_ID&t=120s
    # 5. Watch URL with playlist: https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID
    # 8. Mobile watch link: https://m.youtube.com/watch?v=VIDEO_ID
    # 9. Music or domain variants: https://music.youtube.com/watch?v=VIDEO_ID
    if parsed.path == '/watch' and parsed.query:
        query_params = parse_qs(parsed.query)
        if 'v' in query_params:
            video_id = query_params['v'][0]
            if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
                return video_id
    
    # 2. Shortened URL: https://youtu.be/VIDEO_ID
    elif parsed.netloc == 'youtu.be' and parsed.path:
        video_id = parsed.path.lstrip('/')
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    # 3. Embed URL: https://www.youtube.com/embed/VIDEO_ID
    elif parsed.path.startswith('/embed/'):
        video_id = parsed.path.split('/embed/')[1].split('?')[0]
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    # 7. Shorts URL: https://www.youtube.com/shorts/VIDEO_ID
    elif parsed.path.startswith('/shorts/'):
        video_id = parsed.path.split('/shorts/')[1].split('?')[0]
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    # 6. Playlist URL (extract first video if available)
    elif parsed.path == '/playlist' and parsed.query:
        # For playlist URLs without a specific video, we can't extract a video ID
        # This would require additional API calls to get the first video from the playlist
        return None
    
    # Fallback: Try regex patterns for edge cases
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/.*[?&]v=)([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$'  # Direct video ID
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            if len(video_id) == 11:
                return video_id
    
    return None

def fetch_captions(video_id):
    try:
        # Try to get available transcript languages first
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try English transcripts first
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            transcript_data = transcript.fetch()
        except:
            # If English not available, try any available transcript
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                transcript_data = transcript.fetch()
            except:
                # Get any available transcript
                available_transcripts = list(transcript_list)
                if available_transcripts:
                    transcript_data = available_transcripts[0].fetch()
                else:
                    return None
        
        # Combine all transcript entries
        if transcript_data:
            caption_text = ' '.join([entry['text'] for entry in transcript_data])
            
            if caption_text and len(caption_text.strip()) > 50:
                return caption_text.strip()
                
    except Exception as e:
        # Fallback to simple method
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            caption_text = ' '.join([entry['text'] for entry in transcript_list])
            
            if caption_text and len(caption_text.strip()) > 50:
                return caption_text.strip()
                
        except Exception:
            pass
    
    return None

def download_audio(url, output_path):
    # Try multiple download strategies
    strategies = [
        # Strategy 1: Standard audio extraction
        {
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
        },
        # Strategy 2: Simple format without post-processing
        {
            'format': 'bestaudio/best',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        },
        # Strategy 3: Lowest quality for restricted videos
        {
            'format': 'worst[ext=mp4]/worst',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }
    ]
    
    for strategy in strategies:
        try:
            with yt_dlp.YoutubeDL(strategy) as ydl:
                ydl.download([url])
                
            # Return the path to the extracted audio file
            possible_extensions = ['.mp3', '.wav', '.m4a', '.webm', '.ogg', '.mp4']
            for ext in possible_extensions:
                audio_file = output_path + ext
                if os.path.exists(audio_file):
                    return audio_file
        except Exception:
            continue
    
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

def summarize_text(text, content_type="transcript"):
    """Summarize text with focus on transcript/caption content only"""
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    # Only handle transcript/caption content
    system_prompt = "You are a helpful assistant that creates concise, informative summaries of YouTube video content. Focus on the main points, key takeaways, and important information discussed in the video."
    user_prompt = f"Please summarize this YouTube video transcript in a clear, structured way:\n\n{text}"
    
    try:
        # Try newer OpenAI client first
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
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
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=600,
                temperature=0.3,
            )
            return response['choices'][0]['message']['content']
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")