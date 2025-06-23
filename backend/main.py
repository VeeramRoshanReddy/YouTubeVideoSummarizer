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
    """Fetch captions/transcripts from YouTube video"""
    try:
        print(f"Fetching captions for video ID: {video_id}")
        
        # Try to get available transcript languages first
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try English transcripts first
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            transcript_data = transcript.fetch()
            print("Found English transcript")
        except:
            # If English not available, try any available transcript
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                transcript_data = transcript.fetch()
                print("Found generated English transcript")
            except:
                # Get any available transcript
                available_transcripts = list(transcript_list)
                if available_transcripts:
                    transcript_data = available_transcripts[0].fetch()
                    print(f"Found transcript in language: {available_transcripts[0].language_code}")
                else:
                    print("No transcripts available")
                    return None
        
        # Combine all transcript entries
        if transcript_data:
            caption_text = ' '.join([entry['text'] for entry in transcript_data])
            
            if caption_text and len(caption_text.strip()) > 50:
                print(f"Caption text length: {len(caption_text)} characters")
                return caption_text.strip()
        
        print("Caption text too short or empty")
        return None
                
    except Exception as e:
        print(f"Error fetching captions: {str(e)}")
        # Fallback to simple method
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            caption_text = ' '.join([entry['text'] for entry in transcript_list])
            
            if caption_text and len(caption_text.strip()) > 50:
                print("Fallback method successful")
                return caption_text.strip()
                
        except Exception as e2:
            print(f"Fallback method also failed: {str(e2)}")
            pass
    
    return None

def download_audio(url, output_path):
    """Download audio from YouTube video"""
    print(f"Attempting to download audio from: {url}")
    
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
    
    for i, strategy in enumerate(strategies):
        try:
            print(f"Trying download strategy {i+1}/{len(strategies)}")
            with yt_dlp.YoutubeDL(strategy) as ydl:
                ydl.download([url])
                
            # Return the path to the extracted audio file
            possible_extensions = ['.mp3', '.wav', '.m4a', '.webm', '.ogg', '.mp4']
            for ext in possible_extensions:
                audio_file = output_path + ext
                if os.path.exists(audio_file):
                    print(f"Audio downloaded successfully: {audio_file}")
                    return audio_file
        except Exception as e:
            print(f"Strategy {i+1} failed: {str(e)}")
            continue
    
    print("All download strategies failed")
    return None

def transcribe_audio(audio_path):
    """Transcribe audio using Whisper"""
    try:
        print(f"Transcribing audio file: {audio_path}")
        model = whisper.load_model("tiny")  # Use tiny model for faster processing
        result = model.transcribe(
            audio_path,
            language='en',
            task='transcribe',
            fp16=False,
            verbose=False
        )
        transcript = result['text'].strip() if result['text'] else None
        if transcript:
            print(f"Transcription successful, length: {len(transcript)} characters")
        else:
            print("Transcription resulted in empty text")
        return transcript
    except Exception as e:
        print(f"Transcription failed: {str(e)}")
        return None

def summarize_text(text):
    """Summarize text using OpenAI"""
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    print(f"Summarizing text of length: {len(text)} characters")
    
    system_prompt = "You are a helpful assistant that creates concise, informative summaries of YouTube video content. Focus on the main points, key takeaways, and important information discussed in the video. Structure your summary with clear sections and bullet points when appropriate."
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
        summary = response.choices[0].message.content
        print("OpenAI summarization successful")
        return summary
    except Exception as e1:
        print(f"New OpenAI client failed: {str(e1)}")
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
            summary = response['choices'][0]['message']['content']
            print("Fallback OpenAI method successful")
            return summary
        except Exception as e2:
            print(f"Fallback OpenAI method also failed: {str(e2)}")
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e2)}")

async def process_video_summary(video_id: str):
    """Process video summary - main logic"""
    print(f"Processing video summary for ID: {video_id}")
    
    # Validate video ID format
    if not video_id or len(video_id) != 11 or not video_id.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")
    
    # Get video info
    try:
        print("Fetching video information from YouTube API")
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            raise HTTPException(status_code=404, detail="Video not found or is private/unavailable")
        
        video_info = video_response['items'][0]['snippet']
        title = video_info.get('title', 'Unknown Title')
        print(f"Video found: {title}")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching video info: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch video information from YouTube API")

    # Try captions first (most reliable and preferred method)
    print("Attempting to fetch captions...")
    captions = fetch_captions(video_id)
    
    if captions and len(captions.strip()) > 100:
        try:
            print("Captions found, generating summary...")
            summary = summarize_text(captions)
            return {
                "success": True,
                "summary": summary,
                "method": "captions",
                "video_id": video_id,
                "title": title
            }
        except Exception as e:
            print(f"Caption summarization failed: {str(e)}")
            # Continue to audio transcription
    else:
        print("No suitable captions found, trying audio transcription...")
    
    # Fallback to audio transcription if captions not available
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_base_path = os.path.join(tmpdir, f"audio_{video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            print("Attempting audio download and transcription...")
            audio_file = download_audio(video_url, audio_base_path)
            
            if not audio_file or not os.path.exists(audio_file):
                raise HTTPException(
                    status_code=422, 
                    detail="Unable to access video content. This video may have copyright restrictions, be region-blocked, have no available captions, or be a music video without spoken content."
                )
            
            transcript = transcribe_audio(audio_file)
            
            if not transcript or len(transcript.strip()) < 50:
                raise HTTPException(
                    status_code=422, 
                    detail="Could not extract meaningful spoken content from video. The video may contain mostly music, have poor audio quality, or be too short."
                )
            
            print("Audio transcription successful, generating summary...")
            summary = summarize_text(transcript)
            
            return {
                "success": True,
                "summary": summary,
                "method": "audio_transcription",
                "video_id": video_id,
                "title": title,
                "note": "Summary generated from audio transcription as captions were not available"
            }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Audio transcription failed: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Unable to process video content. The video may have restricted access, contain no spoken content, or the audio could not be processed."
        )

@app.post("/summarize")
async def summarize_video(request: VideoRequest):
    """Summarize video from URL"""
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    print(f"Received summarize request for URL: {request.url}")
    
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL format")

    print(f"Extracted video ID: {video_id}")
    return await process_video_summary(video_id)

@app.post("/summary/{video_id}")
async def summarize_video_by_id(video_id: str):
    """Summarize video by video ID"""
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    print(f"Received summary request for video ID: {video_id}")
    
    if not video_id or len(video_id) != 11:
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")

    return await process_video_summary(video_id)

# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "youtube_api": "configured" if YOUTUBE_DATA_API_KEY else "missing",
        "openai_api": "configured" if OPENAI_API_KEY else "missing"
    }