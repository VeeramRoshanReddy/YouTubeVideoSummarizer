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
from youtube_transcript_api.formatters import TextFormatter
import asyncio
import random
from typing import Optional, Dict, Any

# Load environment variables from .env
load_dotenv()

app = FastAPI(
    title="YouTube Video Summarizer API",
    description="API for summarizing YouTube videos using captions or audio transcription",
    version="2.0.0"
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

# Updated OpenAI client initialization
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except ImportError:
        # Fallback for older OpenAI versions
        openai.api_key = OPENAI_API_KEY
        openai_client = None

class VideoRequest(BaseModel):
    url: str

class AuthRequest(BaseModel):
    code: str
    redirect_uri: str

# ROOT PATH FIX - Add this endpoint
@app.get("/")
async def root():
    return {
        "message": "YouTube Video Summarizer API v2.0 is running!",
        "endpoints": {
            "summarize": "/summarize (POST)",
            "summary_by_id": "/summary/{video_id} (POST)",
            "auth": "/auth (POST)",
            "docs": "/docs (GET)"
        },
        "status": "operational"
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
        response = requests.post(token_url, data=token_data, timeout=10)
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

async def fetch_captions_with_retry(video_id: str, max_retries: int = 3) -> Optional[str]:
    """Fetch captions/transcripts from YouTube video with retry mechanism"""
    
    for attempt in range(max_retries):
        try:
            # Add random delay to avoid rate limiting
            if attempt > 0:
                delay = random.uniform(1, 3)
                await asyncio.sleep(delay)
            
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
                        continue
            
            # Combine all transcript entries
            if transcript_data:
                # Use TextFormatter for better formatting
                formatter = TextFormatter()
                caption_text = formatter.format_transcript(transcript_data)
                
                if caption_text and len(caption_text.strip()) > 50:
                    return caption_text.strip()
            
        except Exception as e:
            print(f"Attempt {attempt + 1} failed: {str(e)}")
            
            # Try fallback method on last attempt
            if attempt == max_retries - 1:
                try:
                    transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
                    caption_text = ' '.join([entry['text'] for entry in transcript_list])
                    
                    if caption_text and len(caption_text.strip()) > 50:
                        return caption_text.strip()
                        
                except Exception as e2:
                    print(f"Fallback method also failed: {str(e2)}")
    
    return None

def download_audio_improved(url: str, output_path: str) -> Optional[str]:
    """Download audio from YouTube video with improved options"""
    
    # Updated strategies with better format selection
    strategies = [
        # Strategy 1: High quality audio extraction
        {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[height<=480]',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'extractaudio': True,
            'audioformat': 'mp3',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '128',
            }],
            'prefer_ffmpeg': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
        },
        # Strategy 2: Simple format without post-processing
        {
            'format': 'bestaudio[filesize<50M]/best[height<=360][filesize<50M]',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
        },
        # Strategy 3: Lowest quality for restricted videos
        {
            'format': 'worst[ext=mp4]/worst',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
        },
        # Strategy 4: Audio-only with size limit
        {
            'format': 'bestaudio[filesize<30M]',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }
    ]
    
    for i, strategy in enumerate(strategies):
        try:
            print(f"Trying download strategy {i + 1}")
            
            # Add additional options for server environments
            strategy.update({
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'http_chunk_size': 10485760,  # 10MB chunks
            })
            
            with yt_dlp.YoutubeDL(strategy) as ydl:
                ydl.download([url])
                
            # Return the path to the extracted audio file
            possible_extensions = ['.mp3', '.wav', '.m4a', '.webm', '.ogg', '.mp4', '.opus']
            for ext in possible_extensions:
                audio_file = output_path + ext
                if os.path.exists(audio_file):
                    file_size = os.path.getsize(audio_file)
                    print(f"Downloaded audio file: {audio_file} ({file_size} bytes)")
                    return audio_file
                    
        except Exception as e:
            print(f"Strategy {i + 1} failed: {str(e)}")
            continue
    
    return None

def transcribe_audio_improved(audio_path: str) -> Optional[str]:
    """Transcribe audio using Whisper with improved settings"""
    try:
        # Check file size
        file_size = os.path.getsize(audio_path)
        print(f"Transcribing audio file: {audio_path} ({file_size} bytes)")
        
        # Use different models based on file size
        if file_size > 50 * 1024 * 1024:  # 50MB
            model_name = "tiny"
        elif file_size > 25 * 1024 * 1024:  # 25MB
            model_name = "base"
        else:
            model_name = "small"
            
        print(f"Using Whisper model: {model_name}")
        model = whisper.load_model(model_name)
        result = model.transcribe(
            audio_path,
            language='en',
            task='transcribe',
            fp16=False,
            verbose=False,
            word_timestamps=False,
            temperature=0.0,
            best_of=1,
            beam_size=1,
        )
        
        transcript = result['text'].strip() if result and result.get('text') else None
        print(f"Transcription completed. Length: {len(transcript) if transcript else 0} characters")
        
        return transcript
        
    except Exception as e:
        print(f"Transcription failed: {str(e)}")
        return None

def summarize_text_improved(text: str) -> str:
    """Summarize text using OpenAI with improved prompts and error handling"""
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    # Truncate text if too long to avoid token limits
    max_chars = 12000  # Approximately 3000 tokens
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    
    system_prompt = """You are an expert content summarizer specializing in YouTube videos. Create a comprehensive, well-structured summary that captures the key information, main arguments, and important details from the video transcript.

Your summary should:
- Begin with a brief overview of the video's main topic
- Organize content into clear, logical sections with headers
- Use bullet points for key takeaways and important facts
- Include any specific examples, statistics, or quotes that are particularly noteworthy
- Maintain the original context and meaning
- Be detailed enough to be valuable while remaining concise"""

    user_prompt = f"Please create a detailed, structured summary of this YouTube video transcript:\n\n{text}"
    
    try:
        # Try newer OpenAI client first
        if openai_client:
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo-16k",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=800,
                temperature=0.2,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
            )
            summary = response.choices[0].message.content
            return summary
        else:
            # Fallback to older OpenAI API format
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo-16k",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=800,
                temperature=0.2,
            )
            summary = response['choices'][0]['message']['content']
            return summary
            
    except Exception as e1:
        # Try with standard model as fallback
        try:
            if openai_client:
                response = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt[:8000]}  # Further truncate for standard model
                    ],
                    max_tokens=600,
                    temperature=0.3,
                )
                summary = response.choices[0].message.content
                return summary
            else:
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt[:8000]}
                    ],
                    max_tokens=600,
                    temperature=0.3,
                )
                summary = response['choices'][0]['message']['content']
                return summary
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e2)}")

async def get_video_info_safe(video_id: str) -> Dict[str, Any]:
    """Safely get video information from YouTube API"""
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        
        video_response = youtube.videos().list(
            part='snippet,status',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            raise HTTPException(status_code=404, detail="Video not found, is private, or has been removed")
        
        video_info = video_response['items'][0]
        snippet = video_info.get('snippet', {})
        status = video_info.get('status', {})
        
        # Check if video is available
        if not status.get('uploadStatus') == 'processed':
            raise HTTPException(status_code=422, detail="Video is not fully processed or available")
        
        if status.get('privacyStatus') == 'private':
            raise HTTPException(status_code=403, detail="Video is private and cannot be accessed")
        
        title = snippet.get('title', 'Unknown Title')
        description = snippet.get('description', '')
        duration = video_info.get('contentDetails', {}).get('duration', '')
        
        return {
            'title': title,
            'description': description,
            'duration': duration,
            'channel': snippet.get('channelTitle', 'Unknown Channel'),
            'published_at': snippet.get('publishedAt', ''),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to fetch video information from YouTube API")

async def process_video_summary(video_id: str) -> Dict[str, Any]:
    """Process video summary - main logic with improved error handling"""
    
    # Validate video ID format
    if not video_id or len(video_id) != 11 or not video_id.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")
    
    # Get video info
    video_info = await get_video_info_safe(video_id)
    title = video_info['title']

    # Try captions first (most reliable and preferred method)
    captions = await fetch_captions_with_retry(video_id, max_retries=3)
    
    if captions and len(captions.strip()) > 100:
        try:
            summary = summarize_text_improved(captions)
            return {
                "success": True,
                "summary": summary,
                "method": "captions",
                "video_id": video_id,
                "title": title,
                "video_info": video_info
            }
        except Exception as e:
            # Continue to audio transcription
            print(f"Caption summarization failed: {str(e)}")
    else:
        print("No captions available, trying audio transcription")
    
    # Fallback to audio transcription if captions not available
    try:
        with tempfile.TemporaryDirectory(prefix="youtube_audio_") as tmpdir:
            audio_base_path = os.path.join(tmpdir, f"audio_{video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            audio_file = download_audio_improved(video_url, audio_base_path)
            
            if not audio_file or not os.path.exists(audio_file):
                raise HTTPException(
                    status_code=422, 
                    detail="Unable to access video content. This video may have copyright restrictions, be region-blocked, have no available captions, or be a music video without spoken content."
                )
            
            transcript = transcribe_audio_improved(audio_file)
            
            if not transcript or len(transcript.strip()) < 50:
                raise HTTPException(
                    status_code=422, 
                    detail="Could not extract meaningful spoken content from video. The video may contain mostly music, have poor audio quality, or be too short."
                )
            
            summary = summarize_text_improved(transcript)
            
            return {
                "success": True,
                "summary": summary,
                "method": "audio_transcription",
                "video_id": video_id,
                "title": title,
                "video_info": video_info,
                "note": "Summary generated from audio transcription as captions were not available"
            }
    except HTTPException:
        raise
    except Exception as e:
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
    
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL format")

    return await process_video_summary(video_id)

@app.post("/summary/{video_id}")
async def summarize_video_by_id(video_id: str):
    """Summarize video by video ID"""
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    if not video_id or len(video_id) != 11:
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")

    return await process_video_summary(video_id)