from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
from googleapiclient.discovery import build
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
import requests
import re
from urllib.parse import urlparse, parse_qs
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from typing import Optional, Dict, Any
import google.generativeai as genai

# Load environment variables from .env
load_dotenv()

app = FastAPI(
    title="YouTube Video Summarizer API",
    description="API for summarizing YouTube videos using captions or descriptions",
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

# Initialize Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-pro')
else:
    gemini_model = None

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
    
    # Handle different YouTube URL formats
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
    
    # Regex patterns for additional formats
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
            print(f"No video found for ID: {video_id}")
            return None
        
        video_info = video_response['items'][0]
        snippet = video_info.get('snippet', {})
        
        return {
            'title': snippet.get('title', 'Unknown Title'),
            'description': snippet.get('description', ''),
            'channel': snippet.get('channelTitle', 'Unknown Channel'),
            'published_at': snippet.get('publishedAt', ''),
        }
        
    except Exception as e:
        print(f"YouTube API error for video {video_id}: {str(e)}")
        return None

def extract_captions(video_id: str) -> Optional[str]:
    """Extract captions/transcripts from YouTube video"""
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript_data = None
        
        # Try manual transcripts first (higher quality)
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            transcript_data = transcript.fetch()
            print(f"Found manual transcript for video {video_id}")
        except:
            # Try auto-generated transcripts
            try:
                transcript = transcript_list.find_generated_transcript(['en', 'en-US', 'en-GB'])
                transcript_data = transcript.fetch()
                print(f"Found auto-generated transcript for video {video_id}")
            except:
                # Try any available transcript
                try:
                    available_transcripts = list(transcript_list)
                    if available_transcripts:
                        transcript_data = available_transcripts[0].fetch()
                        print(f"Found transcript in {available_transcripts[0].language_code} for video {video_id}")
                    else:
                        print(f"No transcripts available for video {video_id}")
                        return None
                except Exception as e:
                    print(f"Error accessing transcript list for video {video_id}: {str(e)}")
                    return None
        
        if transcript_data:
            try:
                # Try using TextFormatter first
                formatter = TextFormatter()
                caption_text = formatter.format_transcript(transcript_data)
                
                if caption_text and len(caption_text.strip()) > 50:
                    return caption_text.strip()
            except:
                # Fallback: manually join transcript entries
                caption_text = ' '.join([entry.get('text', '') for entry in transcript_data if entry.get('text')])
                if caption_text and len(caption_text.strip()) > 50:
                    return caption_text.strip()
        
        print(f"No usable caption content found for video {video_id}")
        return None
        
    except Exception as e:
        print(f"Caption extraction failed for video {video_id}: {str(e)}")
        return None

def summarize_text(text: str, content_type: str) -> str:
    """Summarize text using Google Gemini"""
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text content to summarize")
    
    if not gemini_model:
        raise HTTPException(status_code=500, detail="Gemini API not configured")
    
    # Limit text length to prevent token overflow
    max_chars = 30000  # Gemini can handle more text than GPT-3.5
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    
    if content_type == "description":
        prompt = f"""You are an expert content summarizer. Create a comprehensive summary based on the YouTube video description provided. Extract key information, main topics, and important details mentioned in the description.

Your summary should:
- Identify the main topic and purpose of the video
- Extract key points and important information
- Organize content clearly with headers if needed
- Include any specific details, links, or resources mentioned
- Maintain context and meaning from the description

YouTube video description to summarize:

{text}

Please provide a detailed, structured summary:"""
    else:
        prompt = f"""You are an expert content summarizer specializing in YouTube videos. Create a comprehensive, well-structured summary that captures the key information, main arguments, and important details from the video content.

Your summary should:
- Begin with a brief overview of the video's main topic
- Organize content into clear, logical sections with headers
- Use bullet points for key takeaways and important facts
- Include any specific examples, statistics, or quotes that are particularly noteworthy
- Maintain the original context and meaning
- Be detailed enough to be valuable while remaining concise

YouTube video transcript to summarize:

{text}

Please provide a detailed, structured summary:"""
    
    try:
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=800,
                top_p=0.8,
                top_k=40
            )
        )
        
        if response.text:
            return response.text.strip()
        else:
            raise HTTPException(status_code=500, detail="Gemini API returned empty response")
            
    except Exception as e:
        print(f"Gemini API error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Gemini API error: {str(e)}")

async def process_video_summary(video_id: str) -> Dict[str, Any]:
    """Process video summary with priority: captions -> description"""
    
    if not video_id or len(video_id) != 11:
        raise HTTPException(status_code=400, detail="Invalid YouTube video ID format")
    
    # Get video info first
    video_info = get_video_info(video_id)
    if not video_info:
        raise HTTPException(status_code=404, detail="Video not found or unavailable")
    
    title = video_info['title']
    content_found = None
    method_used = None
    
    # Priority 1: Try captions first
    try:
        captions = extract_captions(video_id)
        if captions and len(captions.strip()) > 100:
            content_found = captions
            method_used = "captions"
    except Exception as e:
        print(f"Caption extraction failed for video {video_id}: {str(e)}")
        pass
    
    # Priority 2: Use description as fallback if captions not available
    if not content_found:
        description = video_info.get('description', '')
        if description and len(description.strip()) > 50:
            content_found = description
            method_used = "description"
        else:
            print(f"No usable description found for video {video_id}")
    
    # Final check - ensure we have some content to work with
    if not content_found:
        raise HTTPException(
            status_code=404, 
            detail="No accessible content found. Video may have restricted captions and insufficient description."
        )
    
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
                result["note"] = "Summary generated from video description as captions were not available"
                
            return result
            
        except Exception as e:
            print(f"Summarization failed for video {video_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to generate summary: {str(e)}")
    
    # This should not be reached due to the check above, but kept as safety net
    raise HTTPException(status_code=500, detail="Unexpected error in content processing")

@app.post("/summarize")
async def summarize_video(request: VideoRequest):
    """Summarize video from URL"""
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")
    
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL format")

    return await process_video_summary(video_id)

@app.post("/summary/{video_id}")
async def summarize_video_by_id(video_id: str):
    """Summarize video by video ID"""
    if not YOUTUBE_DATA_API_KEY:
        raise HTTPException(status_code=500, detail="YouTube Data API key not configured")
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")
    
    return await process_video_summary(video_id)