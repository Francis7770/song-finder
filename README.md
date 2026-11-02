# song-finder
# Song Finder

**Song Finder is a web-based music recognition platform that identifies songs from YouTube and Instagram video URLs.**

Users can submit a video link, automatically extract its audio, and discover the song with relevant metadata and streaming links.

## Overview

Song Finder simplifies music discovery by allowing users to identify songs directly from video content without manually searching for lyrics or audio recordings.

The application processes video URLs, extracts audio samples, and uses music recognition technology to identify tracks.

## Features

- Music identification from YouTube video URLs
- Music identification from Instagram video URLs
- Automatic audio extraction from video content
- Music recognition powered by ShazamIO
- Song title and artist identification
- Album information and release date when available
- Song artwork display
- Direct links to Shazam, Spotify, and Apple Music
- Audio sample extraction and download
- Anonymous search activity tracking
- Supabase database integration
- Responsive interface for desktop and mobile devices

## Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic |
| Streamlit | Web application framework |
| ShazamIO | Music recognition |
| yt-dlp | Video and audio extraction |
| FFmpeg | Audio processing |
| Supabase | PostgreSQL database and analytics |

## Application Workflow

1. The user submits a YouTube or Instagram video URL.
2. The application retrieves the audio from the video.
3. FFmpeg extracts a short audio sample.
4. ShazamIO processes the sample and identifies the music.
5. Song metadata and listening links are displayed.
6. Search activity is recorded in Supabase.

## Installation and Setup

### Prerequisites

- Python 3.12 or compatible version
- Git
- Supabase project

### Clone the Repository

```bash
git clone https://github.com/francis7770/song-finder.git
cd song-finder
```

### Create a Virtual Environment

```bash
python -m venv .venv
```

### Activate the Environment

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure Environment Secrets

Create `.streamlit/secrets.toml`:

```toml
SUPABASE_URL = "your-supabase-project-url"
SUPABASE_KEY = "your-supabase-secret-key"
```

Ensure that secrets are never committed to the repository.

### Run the Application

```bash
streamlit run app.py
```

The application will be available at the local Streamlit URL.

## Database

Song Finder uses Supabase PostgreSQL to record search activity, including:

- Visitor session identifiers
- IP addresses when available
- Search timestamps
- Source platforms
- Identified song information
- Search status
- Processing duration
- Audio download activity

## Deployment

Song Finder can be deployed using Streamlit Community Cloud.

Deployment requires:

- A GitHub repository
- A configured `requirements.txt`
- Supabase credentials configured through deployment secrets
- The application entry point `app.py`

## Privacy and Security

- Database credentials are maintained in server-side secrets.
- Search records are stored in Supabase.
- IP addresses may be collected when available.
- Search functionality does not require user registration.

## Disclaimer

Song identification accuracy depends on audio quality, video availability, and recognition service capabilities.

Users are responsible for ensuring that they have permission to access and process the video content they submit.

Song Finder is an independent project and is not affiliated with Shazam, YouTube, Instagram, Spotify, or Apple Music.

## Author

Francis

