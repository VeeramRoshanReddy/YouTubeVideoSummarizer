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

# Load environment variables from .env
load_dotenv()

app = FastAPI()

# Set your API keys here
YOUTUBE_DATA_API_KEY = os.getenv("YOUTUBE_DATA_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

class VideoRequest(BaseModel):
    url: str

def get_video_id(url):
    # Extract video ID from URL
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    if match:
        return match.group(1)
    return None

def fetch_captions(video_id):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_DATA_API_KEY)
    captions = youtube.captions().list(part='snippet', videoId=video_id).execute()
    if 'items' in captions and captions['items']:
        caption_id = captions['items'][0]['id']
        caption = youtube.captions().download(id=caption_id).execute()
        return caption.get('body', '')
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
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "Summarize the following YouTube video transcript in a concise way."},
            {"role": "user", "content": text}
        ],
        max_tokens=500,
        temperature=0.5,
    )
    return response['choices'][0]['message']['content']

@app.post("/summarize")
async def summarize_video(request: VideoRequest):
    video_id = get_video_id(request.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    # Try to fetch captions
    try:
        captions = fetch_captions(video_id)
    except Exception as e:
        captions = ""

    if captions and captions.strip():
        # Summarize captions
        summary = summarize_text(captions)
        return {"summary": summary}
    else:
        # Download audio and transcribe
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.mp3")
            try:
                download_audio(request.url, audio_path)
                transcript = transcribe_audio(audio_path)
                summary = summarize_text(transcript)
                return {"summary": summary}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to process video: {str(e)}")