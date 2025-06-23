# YouTube Video Summarizer Backend

This is a FastAPI backend for summarizing YouTube videos. It extracts captions using the YouTube Data API or transcribes audio using Whisper if captions are unavailable, then summarizes the content using an AI model (e.g., OpenAI GPT).

## Features
- Accepts YouTube video URLs and returns concise summaries
- Uses YouTube Data API for captions
- Falls back to audio transcription (yt-dlp, ffmpeg, Whisper) if captions are missing
- Summarizes using OpenAI GPT

## Setup

1. **Clone the repository**
2. **Install dependencies:**
   ```
   cd backend
   pip install -r requirements.txt
   ```
3. **Set up environment variables:**
   - Create a `.env` file in the root directory (see below)
4. **Run the server:**
   ```
   uvicorn main:app --reload
   ```

## Environment Variables
Create a `.env` file in the root directory with the following:

```
YOUTUBE_API_KEY=your_youtube_api_key
OPENAI_API_KEY=your_openai_api_key
```

## API Usage
- **POST** `/summarize`
  - Request body: `{ "url": "YOUTUBE_VIDEO_URL" }`
  - Response: `{ "summary": "..." }`

## Notes
- Requires `ffmpeg` installed on your system.
- The frontend is deployed separately and communicates with this backend via HTTP. 