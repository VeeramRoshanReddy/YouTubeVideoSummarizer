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
    
    url = url.strip()
    
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    
    if parsed.path == '/watch' and parsed.query:
        query_params = parse_qs(parsed.query)
        if 'v' in query_params:
            video_id = query_params['v'][0]
            if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
                return video_id
    
    elif parsed.netloc == 'youtu.be' and parsed.path:
        video_id = parsed.path.lstrip('/')
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    elif parsed.path.startswith('/embed/'):
        video_id = parsed.path.split('/embed/')[1].split('?')[0]
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    elif parsed.path.startswith('/shorts/'):
        video_id = parsed.path.split('/shorts/')[1].split('?')[0]
        if len(video_id) == 11 and video_id.replace('-', '').replace('_', '').isalnum():
            return video_id
    
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})',
        r'(?:youtube\.com/.*[?&]v=)([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            if len(video_id) == 11:
                return video_id
    
    return None

def get_video_info(video_id: str) -> Dict[str, Any]:
    """Get video information from YouTube API"""
    try:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
        
        video_response = youtube.videos().list(
            part='snippet,status',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            return None
        
        video_info = video_response['items'][0]
        snippet = video_info.get('snippet', {})
        
        return {
            'title': snippet.get('title', 'Unknown Title'),
            'description': snippet.get('description', ''),
            'channel': snippet.get('channelTitle', 'Unknown Channel'),
            'published_at': snippet.get('publishedAt', ''),
        }
        
    except Exception:
        return None

def extract_captions(video_id: str) -> Optional[str]:
    """Extract captions/transcripts from YouTube video"""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try manual transcripts first
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            transcript_data = transcript.fetch()
        except:
            # Try auto-generated transcripts
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                transcript_data = transcript.fetch()
            except:
                # Try any available transcript
                try:
                    available_transcripts = list(transcript_list)
                    if available_transcripts:
                        transcript_data = available_transcripts[0].fetch()
                    else:
                        return None
                except:
                    return None
        
        if transcript_data:
            try:
                formatter = TextFormatter()
                caption_text = formatter.format_transcript(transcript_data)
                
                if caption_text and len(caption_text.strip()) > 50:
                    return caption_text.strip()
            except:
                # Fallback: manually join transcript entries
                caption_text = ' '.join([entry.get('text', '') for entry in transcript_data if entry.get('text')])
                if caption_text and len(caption_text.strip()) > 50:
                    return caption_text.strip()
        
    except Exception:
        pass
    
    return None

def extract_audio_and_transcribe(video_id: str) -> Optional[str]:
    """Download audio using yt-dlp and transcribe using Whisper"""
    try:
        with tempfile.TemporaryDirectory(prefix="youtube_audio_") as tmpdir:
            audio_base_path = os.path.join(tmpdir, f"audio_{video_id}")
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            
            # Download audio
            audio_file = download_audio(video_url, audio_base_path)
            
            if not audio_file or not os.path.exists(audio_file):
                return None
            
            # Transcribe audio
            transcript = transcribe_audio(audio_file)
            
            if transcript and len(transcript.strip()) > 50:
                return transcript.strip()
                
    except Exception:
        pass
    
    return None

def download_audio(url: str, output_path: str) -> Optional[str]:
    """Download audio from YouTube video using yt-dlp"""
    strategies = [
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
        },
        {
            'format': 'bestaudio[filesize<50M]/best[height<=360][filesize<50M]',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        },
        {
            'format': 'worst[ext=mp4]/worst',
            'outtmpl': output_path + '.%(ext)s',
            'quiet': True,
            'no_warnings': True,
        }
    ]
    
    for strategy in strategies:
        try:
            strategy.update({
                'socket_timeout': 30,
                'retries': 2,
                'fragment_retries': 2,
            })
            
            with yt_dlp.YoutubeDL(strategy) as ydl:
                ydl.download([url])
                
            possible_extensions = ['.mp3', '.wav', '.m4a', '.webm', '.ogg', '.mp4', '.opus']
            for ext in possible_extensions:
                audio_file = output_path + ext
                if os.path.exists(audio_file):
                    return audio_file
                    
        except Exception:
            continue
    
    return None

def transcribe_audio(audio_path: str) -> Optional[str]:
    """Transcribe audio using Whisper"""
    try:
        if not os.path.exists(audio_path):
            return None
            
        file_size = os.path.getsize(audio_path)
        
        # Skip if file is too small (likely corrupted)
        if file_size < 1024:  # 1KB minimum
            return None
        
        if file_size > 50 * 1024 * 1024:
            model_name = "tiny"
        elif file_size > 25 * 1024 * 1024:
            model_name = "base"
        else:
            model_name = "small"
            
        model = whisper.load_model(model_name)
        result = model.transcribe(
            audio_path,
            language='en',
            task='transcribe',
            fp16=False,
            verbose=False,
            temperature=0.0,
            no_speech_threshold=0.6,
        )
        
        transcript_text = result['text'].strip() if result and result.get('text') else None
        return transcript_text if transcript_text and len(transcript_text) > 20 else None
        
    except Exception:
        return None

def summarize_text(text: str, content_type: str) -> str:
    """Summarize text using OpenAI"""
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    max_chars = 12000
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    
    if content_type == "description":
        system_prompt = """You are an expert content summarizer. Create a comprehensive summary based on the YouTube video description provided. Extract key information, main topics, and important details mentioned in the description.

Your summary should:
- Identify the main topic and purpose of the video
- Extract key points and important information
- Organize content clearly with headers if needed
- Include any specific details, links, or resources mentioned
- Maintain context and meaning from the description"""
    else:
        system_prompt = """You are an expert content summarizer specializing in YouTube videos. Create a comprehensive, well-structured summary that captures the key information, main arguments, and important details from the video content.

Your summary should:
- Begin with a brief overview of the video's main topic
- Organize content into clear, logical sections with headers
- Use bullet points for key takeaways and important facts
- Include any specific examples, statistics, or quotes that are particularly noteworthy
- Maintain the original context and meaning
- Be detailed enough to be valuable while remaining concise"""

    user_prompt = f"Please create a detailed, structured summary of this YouTube video {content_type}:\n\n{text}"
    
    try:
        if openai_client:
            response = openai_client.chat.completions.create(
                model="gpt-3.5-turbo-16k",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=800,
                temperature=0.2,
            )
            return response.choices[0].message.content
        else:
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo-16k",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=800,
                temperature=0.2,
            )
            return response['choices'][0]['message']['content']
            
    except Exception:
        try:
            if openai_client:
                response = openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt[:8000]}
                    ],
                    max_tokens=600,
                    temperature=0.3,
                )
                return response.choices[0].message.content
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
                return response['choices'][0]['message']['content']
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OpenAI API error: {str(e)}")

async def process_video_summary(video_id: str) -> Dict[str, Any]:
    """Process video summary with priority: captions -> audio -> description"""
    
    if not video_id or len(video_id) != 11:
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")
    
    # Get video info first
    video_info = get_video_info(video_id)
    if not video_info:
        raise HTTPException(status_code=404, detail="Video not found or unavailable")
    
    title = video_info['title']
    content_found = None
    method_used = None
    
    # Priority 1: Try captions
    try:
        captions = extract_captions(video_id)
        if captions and len(captions.strip()) > 100:
            content_found = captions
            method_used = "captions"
    except Exception:
        pass
    
    # Priority 2: Try audio transcription (only if captions failed)
    if not content_found:
        try:
            transcript = extract_audio_and_transcribe(video_id)
            if transcript and len(transcript.strip()) > 100:
                content_found = transcript
                method_used = "audio_transcription"
        except Exception:
            pass
    
    # Priority 3: Use description as fallback (only if both above failed)
    if not content_found:
        description = video_info.get('description', '')
        if description and len(description.strip()) > 50:
            content_found = description
            method_used = "description"
    
    # If we have content, summarize it
    if content_found and method_used:
        try:
            content_type = "description" if method_used == "description" else "transcript"
            summary = summarize_text(content_found, content_type)
            
            result = {
                "success": True,
                "summary": summary,
                "method": method_used,
                "video_id": video_id,
                "title": title,
                "video_info": video_info
            }
            
            if method_used == "description":
                result["note"] = "Summary generated from video description as captions and audio were not accessible"
            elif method_used == "audio_transcription":
                result["note"] = "Summary generated from audio transcription as captions were not available"
                
            return result
            
        except Exception as e:
            # If summarization fails, still try to return something useful
            raise HTTPException(status_code=500, detail="Failed to generate summary from available content")
    
    # If all methods fail
    raise HTTPException(status_code=500, detail="No accessible content found for summarization")

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
    
    return await process_video_summary(video_id)