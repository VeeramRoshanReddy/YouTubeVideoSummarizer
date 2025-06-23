# YouTube Video Summarizer

A full-stack web application to generate concise AI-powered summaries of YouTube videos. Users authenticate with Google, submit a YouTube link, and receive a summary generated from captions or audio transcription using advanced AI models.

---

## Features
- **Google OAuth** authentication (frontend)
- **Paste any YouTube video URL** to get a summary
- **Captions extraction** via YouTube Data API and YouTube Transcript API
- **Fallback to audio transcription** using Whisper if captions are unavailable
- **Summarization** using OpenAI GPT models
- **No persistent storage**—stateless, privacy-friendly
- **Modern UI** (HTML, CSS, JS)

---

## Project Structure

```
YouTube_Video_Summarizer/
├── backend/
│   ├── main.py              # FastAPI backend
│   └── requirements.txt     # Backend dependencies
├── frontend/
│   ├── index.html           # Main HTML file
│   ├── script.js            # Frontend logic
│   └── styles.css           # Styling
├── .gitignore
└── README.md
```

---

## Backend (FastAPI)
- **Location:** `/backend`
- **Deploy on:** Render (or any Python server)
- **Main entry:** `main.py`
- **Dependencies:** See `requirements.txt`
- **Environment Variables:**
  - `YOUTUBE_DATA_API_KEY` (YouTube Data API v3 key)
  - `OPENAI_API_KEY` (OpenAI API key)
  - `GOOGLE_CLIENT_ID` (Google OAuth client ID)
  - `GOOGLE_CLIENT_SECRET` (Google OAuth client secret)

### Setup & Run Locally
```bash
cd backend
pip install -r requirements.txt
# Create a .env file with the required keys
uvicorn main:app --reload
```

### API Endpoints
- `POST /summarize` — `{ "url": "YOUTUBE_VIDEO_URL" }` → `{ "summary": "..." }`
- `POST /summary/{video_id}` — Summarize by YouTube video ID
- `POST /auth` — Exchange Google OAuth code for access token
- `GET /` — API status/info

---

## Frontend (Static HTML/JS/CSS)
- **Location:** `/frontend`
- **Deploy on:** Vercel (or any static host)
- **Main entry:** `index.html`
- `No build step required`
- **Config:** Set `CLIENT_ID`, `REDIRECT_URI`, and `API_ENDPOINT` in `script.js`

### Usage
- User signs in with Google
- Pastes a YouTube URL
- Receives a summary in the UI

---

## Deployment
- **Frontend:** Deploy `/frontend` to Vercel (drag-and-drop or via Git integration)
- **Backend:** Deploy `/backend` to Render (Python web service)

---

## .env Example (Backend)
```
YOUTUBE_DATA_API_KEY=your_youtube_data_api_key
OPENAI_API_KEY=your_openai_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
```

---

## .gitignore
- Ignores Python cache, virtual environments, .env, and editor files
- Ignores frontend build artifacts (if any)

---

## License
MIT (or specify your license) 