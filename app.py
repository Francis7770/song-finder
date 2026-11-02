import asyncio
import ipaddress
import logging
import time
import uuid
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import streamlit as st
from supabase import create_client
import yt_dlp
from shazamio import Shazam


MAX_AUDIO_SIZE = 10 * 1024 * 1024
MAX_SOURCE_SIZE = 100 * 1024 * 1024
MAX_DURATION = 12

logger = logging.getLogger(__name__)


@st.cache_resource
def get_supabase_client():
    """Create one server-side Supabase client for this Streamlit process."""
    return create_client(
        st.secrets["SUPABASE_URL"],
        st.secrets["SUPABASE_KEY"],
    )


def get_visitor_ip():
    """Return the client IP only when Streamlit exposes it directly."""
    try:
        direct_ip = getattr(st.context, "ip_address", None)
        if direct_ip:
            return str(ipaddress.ip_address(direct_ip))
    except (ValueError, AttributeError):
        pass
    except Exception:
        logger.exception("Could not read visitor IP from request context")
    # Do not guess from forwarded headers: unless a trusted proxy is known
    # to overwrite them, visitors may be able to spoof those values.
    return None


def get_platform(url):
    host = (urlparse(url).hostname or "").lower()
    if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
        return "YouTube"
    if host == "instagram.com" or host.endswith(".instagram.com"):
        return "Instagram"
    return "Other"


def save_search_record(record):
    """Insert a search row. Database failures must not stop song recognition."""
    try:
        response = get_supabase_client().table("search_history").insert(record).execute()
        if response.data:
            return response.data[0].get("id")
    except Exception:
        logger.exception("Could not save Song Finder search record")
    return None


def update_search_record(search_id, updates):
    if not search_id:
        return
    try:
        get_supabase_client().table("search_history").update(updates).eq("id", search_id).execute()
    except Exception:
        logger.exception("Could not update Song Finder search record")


def mark_download_clicked(search_id):
    update_search_record(search_id, {"download_clicked": True})


st.set_page_config(
    page_title="Song Finder",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def is_supported_url(url):
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()

        allowed_domains = {
            "youtube.com",
            "youtu.be",
            "youtube-nocookie.com",
            "instagram.com",
        }

        return (
            parsed.scheme in ("http", "https")
            and any(
                host == domain or host.endswith("." + domain)
                for domain in allowed_domains
            )
        )
    except (ValueError, AttributeError):
        return False


def identify_with_shazam(audio_path):
    async def recognize():
        shazam = Shazam()
        return await shazam.recognize(str(audio_path))

    return asyncio.run(recognize())


def extract_audio_from_url(url, temp_dir):
    temp_path = Path(temp_dir)
    output_template = str(temp_path / "source.%(ext)s")
    audio_path = temp_path / "song_finder_audio.mp3"
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 25,
        "retries": 1,
        "max_filesize": MAX_SOURCE_SIZE,
        "ffmpeg_location": ffmpeg_path,
    }

    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)

            source_file = None
            requested = info.get("requested_downloads") or []

            if requested and requested[0].get("filepath"):
                source_file = Path(requested[0]["filepath"])

            if source_file is None and info.get("filepath"):
                source_file = Path(info["filepath"])

            if source_file is None:
                source_file = Path(downloader.prepare_filename(info))

            if not source_file.exists():
                candidates = [
                    file for file in temp_path.glob("source.*")
                    if file != audio_path
                ]
                if candidates:
                    source_file = candidates[0]
                else:
                    raise RuntimeError(
                        "The source audio could not be located after download."
                    )

    except yt_dlp.utils.DownloadError as error:
        raise RuntimeError(
            "We couldn't access this video. Check that the link is public "
            "and try again. Some platform links may not be available."
        ) from error

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(source_file),
        "-t", str(MAX_DURATION),
        "-vn",
        "-ac", "2",
        "-ar", "44100",
        "-b:a", "96k",
        str(audio_path),
    ]

    conversion = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=90,
    )

    if conversion.returncode != 0:
        raise RuntimeError(
            "We couldn't extract audio from this video. "
            "Please try another public video with a soundtrack."
        )

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("No audio track was found in this video.")

    if audio_path.stat().st_size > MAX_AUDIO_SIZE:
        raise RuntimeError("The extracted audio is larger than the 10 MB limit.")

    return audio_path


def get_song_metadata(song):
    album = None
    release_date = None

    for section in song.get("sections", []):
        for item in section.get("metadata", []):
            key = (item.get("title") or "").strip().lower()
            value = item.get("text")

            if key == "album" and value:
                album = value
            elif key in ("released", "release date") and value:
                release_date = value

    return album, release_date


def get_listening_links(song):
    links = []
    seen = set()

    for provider in song.get("hub", {}).get("providers", []):
        provider_name = (provider.get("type") or "").lower()

        for action in provider.get("actions", []):
            uri = action.get("uri")
            if not uri:
                continue

            uri_lower = uri.lower()

            if "spotify" in provider_name or "spotify" in uri_lower:
                service = "Spotify"
            elif (
                "apple" in provider_name
                or "apple" in uri_lower
                or "itunes" in uri_lower
            ):
                service = "Apple Music"
            else:
                continue

            if service not in seen:
                links.append((service, uri))
                seen.add(service)

    return links


def display_song(song):
    title = song.get("title") or "Unknown title"
    artist = song.get("subtitle") or "Unknown artist"
    images = song.get("images") or {}
    cover = images.get("coverarthq") or images.get("coverart")
    album, release_date = get_song_metadata(song)

    st.markdown(
        '<div class="result-eyebrow">YOUR MATCH</div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        image_col, details_col = st.columns([1, 2.2], gap="large")

        with image_col:
            if cover:
                st.image(cover, use_container_width=True)
            else:
                st.markdown(
                    '<div class="cover-placeholder">♫</div>',
                    unsafe_allow_html=True,
                )

        with details_col:
            st.markdown(
                f'<div class="matched-label">✓  SONG IDENTIFIED</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="song-title">{title}</div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="song-artist">{artist}</div>',
                unsafe_allow_html=True,
            )

            if album:
                st.markdown(
                    f'<div class="song-meta"><span>ALBUM</span>{album}</div>',
                    unsafe_allow_html=True,
                )

            if release_date:
                st.markdown(
                    f'<div class="song-meta"><span>RELEASED</span>{release_date}</div>',
                    unsafe_allow_html=True,
                )

            song_url = song.get("url")
            if song_url:
                st.link_button(
                    "Open on Shazam ↗",
                    song_url,
                    use_container_width=True,
                )

        links = get_listening_links(song)
        if links:
            st.markdown('<div class="listen-heading">LISTEN TO THE TRACK</div>',
                        unsafe_allow_html=True)
            link_cols = st.columns(len(links))
            for col, (service, uri) in zip(link_cols, links):
                with col:
                    st.link_button(
                        f"Listen on {service} ↗",
                        uri,
                        use_container_width=True,
                    )


def inject_styles():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');

        :root {
            --green: #174D35;
            --green-hover: #236847;
            --ink: #172B21;
            --muted: #718078;
            --line: #E5EBE6;
            --canvas: #F5F7F5;
        }

        html, body, [class*="css"] {
            font-family: 'DM Sans', sans-serif;
        }

        .stApp {
            background: var(--canvas);
            color: var(--ink);
        }

        [data-testid="stHeader"] {
            background: rgba(245, 247, 245, 0.92);
        }

        [data-testid="stToolbar"] {
            right: 1rem;
        }

        .main .block-container {
            max-width: 1040px;
            padding: 2rem 2rem 4rem;
        }

        #MainMenu, footer {
            visibility: hidden;
        }

        .brand-row {
            display: flex;
            align-items: center;
            gap: 10px;
            color: var(--ink);
            font-family: 'Manrope', sans-serif;
            font-size: 18px;
            font-weight: 800;
            letter-spacing: -0.5px;
            padding: 4px 0 26px;
        }

        .brand-icon {
            width: 36px;
            height: 36px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 11px;
            background: #E5EFE8;
            color: var(--green);
            font-size: 20px;
        }

        .hero {
            text-align: center;
            padding: 54px 12px 34px;
        }

        .hero-pill {
            display: inline-block;
            padding: 7px 13px;
            border: 1px solid #D9E7DD;
            border-radius: 30px;
            background: #ECF3EE;
            color: var(--green);
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.25px;
        }

        .hero h1 {
            margin: 20px 0 12px;
            color: var(--ink);
            font-family: 'Manrope', sans-serif;
            font-size: clamp(38px, 6vw, 58px);
            line-height: 1.08;
            font-weight: 800;
            letter-spacing: -2.5px;
        }

        .hero-copy {
            max-width: 590px;
            margin: 0 auto;
            color: #68766E;
            font-size: 16px;
            line-height: 1.7;
        }

        .input-heading {
            color: var(--ink);
            font-size: 14px;
            font-weight: 700;
            margin: 0 0 7px;
        }

        .input-help {
            color: #87928B;
            font-size: 12px;
            margin-bottom: 13px;
        }

        [data-testid="stTextInput"] input {
            min-height: 54px;
            border: 1px solid #DCE5DE;
            border-radius: 12px;
            background: #FFFFFF;
            color: var(--ink);
            padding: 0 16px;
            font-size: 14px;
            box-shadow: 0 2px 8px rgba(24, 54, 36, 0.025);
        }

        [data-testid="stTextInput"] input:focus {
            border-color: #7FA68D;
            box-shadow: 0 0 0 3px rgba(23, 77, 53, 0.09);
        }

        [data-testid="stTextInput"] input::placeholder {
            color: #A5AEA8;
        }

        div.stButton > button,
        div.stDownloadButton > button,
        div.stLinkButton > a {
            min-height: 48px;
            border: 1px solid var(--green) !important;
            border-radius: 11px !important;
            background: var(--green) !important;
            color: #FFFFFF !important;
            font-weight: 700 !important;
            box-shadow: 0 5px 13px rgba(23, 77, 53, 0.12);
            transition: all .18s ease;
        }

        div.stButton > button *,
        div.stDownloadButton > button *,
        div.stLinkButton > a * {
            color: #FFFFFF !important;
        }

        div.stButton > button:hover,
        div.stDownloadButton > button:hover,
        div.stLinkButton > a:hover {
            background: var(--green-hover) !important;
            border-color: var(--green-hover) !important;
            color: #FFFFFF !important;
            transform: translateY(-1px);
        }

        div.stButton > button:focus,
        div.stDownloadButton > button:focus {
            box-shadow: 0 0 0 3px rgba(23, 77, 53, 0.18) !important;
        }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: #FFFFFF;
            box-shadow: 0 8px 28px rgba(24, 54, 36, 0.045);
        }

        .result-eyebrow, .listen-heading {
            color: #829087;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 1.5px;
            margin: 22px 0 12px;
        }

        .matched-label {
            display: inline-block;
            color: var(--green);
            background: #EAF3ED;
            border-radius: 20px;
            padding: 6px 10px;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: 0.8px;
            margin: 4px 0 14px;
        }

        .song-title {
            color: var(--ink);
            font-family: 'Manrope', sans-serif;
            font-size: 27px;
            line-height: 1.2;
            font-weight: 800;
            letter-spacing: -0.8px;
            overflow-wrap: anywhere;
        }

        .song-artist {
            color: #718078;
            font-size: 16px;
            margin: 7px 0 18px;
        }

        .song-meta {
            color: #35463B;
            font-size: 13px;
            margin: 8px 0;
        }

        .song-meta span {
            display: block;
            color: #98A29B;
            font-size: 9px;
            font-weight: 800;
            letter-spacing: 1.1px;
            margin-bottom: 3px;
        }

        .cover-placeholder {
            min-height: 190px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 14px;
            background: #EAF1EC;
            color: var(--green);
            font-size: 70px;
        }

        [data-testid="stImage"] img {
            border-radius: 14px;
        }

        [data-testid="stAlert"] {
            border-radius: 12px;
        }

        .feature-strip {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 10px 22px;
            color: #78857D;
            font-size: 12px;
            margin: 22px 0 0;
        }

        .feature-strip span::before {
            content: "✓";
            color: var(--green);
            font-weight: 800;
            margin-right: 6px;
        }

        .footer-note {
            border-top: 1px solid var(--line);
            margin-top: 54px;
            padding-top: 20px;
            text-align: center;
            color: #9AA49D;
            font-size: 11px;
            line-height: 1.7;
        }

        @media (max-width: 700px) {
            .main .block-container {
                padding: 1rem 1rem 3rem;
            }
            .hero {
                padding: 38px 4px 25px;
            }
            .hero h1 {
                letter-spacing: -1.5px;
            }
            
/* Responsive layout for tablets and mobile */
@media screen and (max-width: 768px) {

    .main .block-container {
        max-width: 100%;
        padding: 1.2rem 1rem 2.5rem;
    }

    .brand-row {
        padding-bottom: 12px;
        font-size: 16px;
    }

    .hero {
        padding: 38px 5px 25px;
    }

    .hero h1 {
        font-size: clamp(34px, 9vw, 46px);
        line-height: 1.12;
        letter-spacing: -1.5px;
    }

    .hero-copy {
        font-size: 14px;
        padding: 0 5px;
    }

    [data-testid="stTextInput"] {
        width: 100%;
    }

    [data-testid="stTextInput"] input {
        width: 100%;
        min-height: 52px;
        font-size: 14px;
    }

    div.stButton > button,
    div.stDownloadButton > button,
    div.stLinkButton > a {
        width: 100%;
        min-height: 48px;
    }

    .song-title {
        font-size: 23px;
    }

    .song-artist {
        font-size: 14px;
    }

    .feature-strip {
        gap: 9px 13px;
        font-size: 11px;
    }

    .footer-note {
        margin-top: 35px;
        padding: 18px 8px;
    }
}

/* Small phones */
@media screen and (max-width: 480px) {

    .main .block-container {
        padding: 0.8rem 0.85rem 2rem;
    }

    .hero {
        padding-top: 30px;
    }

    .hero h1 {
        font-size: 36px;
    }

    .hero-pill {
        font-size: 9px;
    }

    .feature-strip {
        flex-direction: column;
        align-items: center;
    }

    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 14px;
    }
}

        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_styles()

st.markdown(
    """
    <div class="brand-row">
        <span class="brand-icon">♫</span>
        <span>song finder</span>
    </div>

    <section class="hero">
        <div class="hero-pill">MUSIC DISCOVERY, MADE SIMPLE</div>
        <h1>Find the song<br>behind the video.</h1>
        <p class="hero-copy">
            Heard something you love in a video?
            Paste the link and discover the track in seconds.
        </p>
    </section>
    """,
    unsafe_allow_html=True,
)

with st.container():
    st.markdown(
        '<div class="input-heading">Video link</div>'
        '<div class="input-help">Paste a public YouTube or Instagram video URL</div>',
        unsafe_allow_html=True,
    )

    video_url = st.text_input(
        "Video URL",
        placeholder="https://www.youtube.com/watch?v=...",
        label_visibility="collapsed",
    )

    find_clicked = st.button(
        "♫    Find my song",
        type="primary",
        use_container_width=True,
    )

st.markdown(
    """
    <div class="feature-strip">
        <span>YouTube links</span>
        <span>Instagram links</span>
        <span>Song details</span>
        <span>Audio sample download</span>
    </div>
    """,
    unsafe_allow_html=True,
)

if find_clicked:
    st.session_state.pop("extracted_audio", None)
    st.session_state.pop("identified_song", None)
    st.session_state["current_search_id"] = None

    search_started = time.perf_counter()
    search_id = None
    cleaned_url = video_url.strip()

    if cleaned_url:
        if "visitor_session_id" not in st.session_state:
            st.session_state["visitor_session_id"] = str(uuid.uuid4())

        search_id = save_search_record({
            "visitor_session_id": st.session_state["visitor_session_id"],
            "ip_address": get_visitor_ip(),
            "platform": get_platform(cleaned_url),
            "search_status": "Failed",
            "download_clicked": False,
        })
        st.session_state["current_search_id"] = search_id

    if not video_url.strip():
        st.warning("Paste a video link to get started.")

    elif not is_supported_url(video_url):
        st.error("Please enter a valid YouTube or Instagram URL.")

    else:
        try:
            with st.spinner("Finding the music in your video..."):
                with tempfile.TemporaryDirectory() as temp_dir:
                    audio_path = extract_audio_from_url(
                        video_url.strip(),
                        temp_dir,
                    )
                    audio_bytes = audio_path.read_bytes()
                    result_data = identify_with_shazam(audio_path)

            st.session_state["extracted_audio"] = audio_bytes

            if not isinstance(result_data, dict):
                st.error("The recognition service returned an unexpected result.")
            else:
                song = result_data.get("track")
                if song:
                    st.session_state["identified_song"] = song
                else:
                    update_search_record(search_id, {
                        "search_status": "Failed",
                        "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
                    })
                    st.warning(
                        "We couldn't identify a song in this clip. "
                        "Try a video with clearer music."
                    )
                if song:
                    update_search_record(search_id, {
                        "song_name": song.get("title") or "Unknown title",
                        "artist_name": song.get("subtitle") or "Unknown artist",
                        "search_status": "Success",
                        "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
                    })
                elif not song:
                    # The failed result was already recorded above.
                    pass

        except subprocess.TimeoutExpired:
            update_search_record(search_id, {
                "search_status": "Failed",
                "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
            })
            st.error("Processing took too long. Please try another video.")
        except (OSError, ValueError) as error:
            update_search_record(search_id, {
                "search_status": "Failed",
                "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
            })
            st.error(f"Could not process this video: {error}")
        except RuntimeError as error:
            update_search_record(search_id, {
                "search_status": "Failed",
                "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
            })
            st.error(str(error))
        except Exception as error:
            update_search_record(search_id, {
                "search_status": "Failed",
                "processing_time_ms": int((time.perf_counter() - search_started) * 1000),
            })
            st.error(
                "Something went wrong. Please try again. "
                f"Details: {type(error).__name__}: {error}"
            )

if st.session_state.get("identified_song"):
    display_song(st.session_state["identified_song"])

if st.session_state.get("extracted_audio"):
    st.markdown('<div class="result-eyebrow">YOUR AUDIO SAMPLE</div>',
                unsafe_allow_html=True)
    with st.container(border=True):
        audio_col, download_col = st.columns([1.6, 1], vertical_alignment="center")
        with audio_col:
            st.markdown("**12-second extracted audio**")
            st.caption("The audio sample taken from your video.")
        with download_col:
            st.download_button(
                "Download MP3 ↓",
                data=st.session_state["extracted_audio"],
                file_name="song_finder_audio.mp3",
                mime="audio/mpeg",
                use_container_width=True,
                key="download_extracted_audio",
                on_click=mark_download_clicked,
                args=(st.session_state.get("current_search_id"),),
            )

st.markdown(
    """
    <div class="footer-note">
        Song Finder · Discover music from videos.<br>
        Recognition powered by ShazamIO. Results depend on video access
        and audio quality. Only use content you have permission to access.
    </div>
    """,
    unsafe_allow_html=True,
)
