
import base64
import hashlib
import html
import json
import math
import os
import re
import tempfile
from io import BytesIO

from streamlit.components.v1 import html as html_iframe
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import google.auth
import requests
import streamlit as st
import yt_dlp
from google import genai
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.genai import types
from google.genai.types import HttpOptions, Part
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from weasyprint import HTML as WeasyHTML
except Exception:
    WeasyHTML = None

# ==========================================
# 0. Basic Settings
# ==========================================
PROJECT_ID = "gen-lang-client-0384252392"
LOCATION = "global"

TEXT_MODEL_ID = "gemini-3.1-pro-preview"
IMAGE_MODEL_CANDIDATES = [
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-2.5-flash-image",
]

LYRIA_LOCATION = "us-central1"
LYRIA_MODEL_ID = "lyria-002"

DOWNLOAD_FORMAT = "bestvideo[ext=mp4]/best[ext=mp4]/bestvideo/best"
MAX_SELECTED_FRAMES = 5  # 1 hero + 4 supporting
MAX_CHAT_CONTEXT_IMAGES = 12
MAX_MAGAZINE_UPLOADED_IMAGES = 12
UPLOADED_IMAGES_PER_PAGE = 4
MAX_UPLOADED_IMAGE_SIDE = 1600

# ==========================================
# 1. Page Config + CSS
# ==========================================
st.set_page_config(page_title="AI Visual Zine Editor", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans:wght@300;400;700;900&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans', sans-serif;
}

.block-container {
    padding-top: 2.1rem;
    padding-bottom: 2rem;
    max-width: 95%;
}

.title-wrap h1 {
    font-size: 2.7rem;
    line-height: 1.08;
    margin-bottom: 0.35rem;
}

.kicker {
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-size: 0.8rem;
    opacity: 0.72;
    margin-bottom: 0.45rem;
}

.meta-line {
    opacity: 0.75;
    font-size: 0.92rem;
    margin-top: 0.4rem;
}

.small-note {
    opacity: 0.68;
    font-size: 0.88rem;
    margin-top: 0.5rem;
}

.section-label {
    font-size: 0.92rem;
    font-weight: 700;
    margin-top: 1rem;
    margin-bottom: 0.6rem;
    letter-spacing: 0.02em;
}

.issue-deck {
    font-size: 1.04rem;
    opacity: 0.86;
    margin-bottom: 0.9rem;
}

.pull-quote {
    font-size: 1.12rem;
    font-style: italic;
    opacity: 0.9;
    padding: 0.9rem 1rem;
    border-left: 3px solid rgba(255,255,255,0.25);
    margin: 0.6rem 0 1rem 0;
    background: rgba(255,255,255,0.03);
    border-radius: 0.35rem;
}

.caption-card {
    opacity: 0.84;
    font-size: 0.88rem;
    margin-top: 0.3rem;
    margin-bottom: 1rem;
}

.chat-hint {
    opacity: 0.72;
    font-size: 0.93rem;
    margin-bottom: 0.8rem;
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Session State
# ==========================================
def ensure_session_state() -> None:
    defaults = {
        "current_url": None,
        "metadata": None,
        "analysis": None,
        "candidate_frames": [],
        "selected_frames": [],
        "selected_reason": "",
        "messages": [],
        "published_issue": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_video_state(clear_url: bool = False) -> None:
    st.session_state.metadata = None
    st.session_state.analysis = None
    st.session_state.candidate_frames = []
    st.session_state.selected_frames = []
    st.session_state.selected_reason = ""
    st.session_state.messages = []
    st.session_state.published_issue = None
    if clear_url:
        st.session_state.current_url = None


ensure_session_state()

# ==========================================
# 3. Vertex Client
# ==========================================
@st.cache_resource
def get_client():
    return genai.Client(
        vertexai=True,
        project=PROJECT_ID,
        location=LOCATION,
        http_options=HttpOptions(api_version="v1"),
    )

# ==========================================
# 4. Utility Helpers
# ==========================================
def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "issue"


def build_conversation_transcript(messages: List[Dict[str, Any]], limit: int = 14) -> str:
    if not messages:
        return "No follow-up conversation yet."

    trimmed = messages[-limit:]
    lines: List[str] = []

    for item in trimmed:
        role = str(item.get("role", "user")).upper()
        content = str(item.get("content", "") or "").strip()
        images = item.get("images", []) or []
        image_note = f" [attached {len(images)} image(s)]" if images else ""

        if content:
            lines.append(f"{role}{image_note}: {content}")
        else:
            lines.append(f"{role}{image_note}: [image-only message]")

    return "\n".join(lines)



def parse_json_from_model_output(raw_text: Optional[str]) -> Dict[str, Any]:
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return {}

    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
        if not cleaned:
            return {}

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    json_obj_match = re.search(r"\{[\s\S]*\}", cleaned)
    if json_obj_match:
        try:
            return json.loads(json_obj_match.group(0))
        except json.JSONDecodeError:
            return {}

    return {}


def infer_audio_mime_type(audio_bytes: Optional[bytes]) -> str:
    if not audio_bytes:
        return "audio/wav"

    header = audio_bytes[:16]

    if header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE":
        return "audio/wav"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    if header.startswith(b"fLaC"):
        return "audio/flac"
    if header.startswith(b"ID3") or header[:2] in (b"\xFF\xFB", b"\xFF\xF3", b"\xFF\xF2"):
        return "audio/mpeg"

    if header.startswith(b"\x1A\x45\xDF\xA3"):
        return "audio/webm"

    return "audio/wav"


def normalize_uploaded_image(uploaded_file: Any) -> Optional[Dict[str, Any]]:
    mime_type = getattr(uploaded_file, "type", None) or "application/octet-stream"
    if not mime_type.startswith("image/"):
        return None

    raw = uploaded_file.getvalue()
    if not raw:
        return None

    file_name = getattr(uploaded_file, "name", "Attached image")

    try:
        with Image.open(BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)

            if img.mode in ("RGBA", "LA"):
                background = Image.new("RGBA", img.size, (255, 255, 255, 255))
                background.alpha_composite(img.convert("RGBA"))
                img = background.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            resampling = getattr(Image, "Resampling", Image).LANCZOS
            img.thumbnail((MAX_UPLOADED_IMAGE_SIDE, MAX_UPLOADED_IMAGE_SIDE), resampling)

            out = BytesIO()
            img.save(out, format="JPEG", quality=88, optimize=True)

            return {
                "name": file_name,
                "mime_type": "image/jpeg",
                "data": out.getvalue(),
                "width": img.width,
                "height": img.height,
                "sha1": hashlib.sha1(out.getvalue()).hexdigest(),
            }
    except (UnidentifiedImageError, OSError, ValueError):
        return {
            "name": file_name,
            "mime_type": mime_type,
            "data": raw,
            "sha1": hashlib.sha1(raw).hexdigest(),
        }


def collect_conversation_images(
    messages: List[Dict[str, Any]],
    max_images: int = MAX_CHAT_CONTEXT_IMAGES,
) -> List[Dict[str, Any]]:
    image_entries: List[Dict[str, Any]] = []
    seen_hashes = set()

    for message in reversed(messages):
        for image in reversed(message.get("images", []) or []):
            data = image.get("data", b"")
            if not data:
                continue
            image_hash = image.get("sha1") or hashlib.sha1(data).hexdigest()
            if image_hash in seen_hashes:
                continue
            seen_hashes.add(image_hash)
            image_entries.append({
                "role": message.get("role", "user"),
                "name": image.get("name", "reference image"),
                "mime_type": image.get("mime_type", "image/jpeg"),
                "data": data,
                "sha1": image_hash,
            })
            if len(image_entries) >= max_images:
                break
        if len(image_entries) >= max_images:
            break

    image_entries.reverse()
    return image_entries


def collect_recent_image_parts(
    messages: List[Dict[str, Any]],
    max_images: int = MAX_CHAT_CONTEXT_IMAGES,
) -> List[Any]:
    image_entries = collect_conversation_images(messages, max_images=max_images)

    parts: List[Any] = []
    for idx, image in enumerate(image_entries, start=1):
        data = image.get("data", b"")
        mime_type = image.get("mime_type", "image/jpeg")
        if not data:
            continue
        parts.append(
            f"Reference image {idx} from the conversation ({image.get('role', 'user')} attachment: {image.get('name', 'image')}). This image is already available inside the app and can be placed directly into the final magazine layout. Use it as visual evidence when relevant."
        )
        parts.append(Part.from_bytes(data=data, mime_type=mime_type))

    return parts


def collect_magazine_uploaded_images(
    messages: List[Dict[str, Any]],
    max_images: int = MAX_MAGAZINE_UPLOADED_IMAGES,
) -> List[Dict[str, Any]]:
    return collect_conversation_images(messages, max_images=max_images)


def render_message_images(images: List[Dict[str, Any]]) -> None:
    if not images:
        return

    if len(images) == 1:
        image = images[0]
        st.image(BytesIO(image["data"]), caption=image.get("name", "Attached image"), width="stretch")
        return

    cols = st.columns(min(3, len(images)))
    for idx, image in enumerate(images):
        with cols[idx % len(cols)]:
            st.image(BytesIO(image["data"]), caption=image.get("name", "Attached image"), width="stretch")


def extract_inline_image_bytes(response: Any) -> Optional[bytes]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                return inline_data.data
    return None


def fallback_display_title(metadata: Dict[str, Any]) -> str:
    raw = (metadata.get("title") or "").strip()
    uploader = (metadata.get("uploader") or "").strip()

    cleaned = re.sub(
        r"(?i)\b(official(\s+music)?\s+video|official\s+mv|performance\s+video|visualizer|m\/v|mv|audio|full\s+show|full\s+film|runway\s+show)\b",
        "",
        raw,
    )
    cleaned = re.sub(r"[\[\(].*?[\]\)]", "", cleaned)
    cleaned = re.sub(r"['\"]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -?:")

    if not cleaned and uploader:
        return uploader
    if not cleaned:
        return "Untitled Video"

    parts = re.split(r"\s[-–—:]\s", cleaned, maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) <= 6:
        left = parts[0].strip()
        right = parts[1].strip().strip('"').strip("'")
        if left and right:
            return f"{left} - {right}"

    if uploader and uploader.lower() not in cleaned.lower():
        return f"{uploader} - {cleaned}"

    return cleaned
def clean_body_markdown(text: str) -> str:
    text = (text or "").strip()

    text = re.sub(r"(?im)^\s*1\.\s*The Core Concept\s*$", "## 1. The Core Concept", text)
    text = re.sub(r"(?im)^\s*2\.\s*Visual\s*&\s*Styling Critique\s*$", "## 2. Visual & Styling Critique", text)
    text = re.sub(r"(?im)^\s*3\.\s*Editor's Verdict\s*$", "## 3. Editor's Verdict", text)

    markers = [
        "## 1. The Core Concept",
        "# 1. The Core Concept",
        "1. The Core Concept",
    ]
    starts = [text.find(m) for m in markers if m in text]
    if starts:
        text = text[min(starts):]

    return text.strip()

# ==========================================
# 5. Metadata
# ==========================================
@st.cache_data(show_spinner=False, ttl=3600)
def get_video_metadata(video_url: str) -> Dict[str, Any]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    if info.get("entries"):
        info = next((entry for entry in info["entries"] if entry), None) or info["entries"][0]

    return {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "channel": info.get("channel") or "",
        "duration": int(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail") or "",
    }

# ==========================================
# 6. CV Helpers
# ==========================================
@st.cache_resource
def get_face_cascade():
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            return None
        return cascade
    except Exception:
        return None


def _clamp_ts(ts: float, duration: float) -> float:
    if duration <= 0:
        return max(0.0, float(ts))
    return max(0.0, min(float(ts), max(duration - 0.25, 0.0)))


def _find_downloaded_video_file(base_dir: str) -> Optional[str]:
    exts = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
    candidates: List[str] = []

    for root, _, files in os.walk(base_dir):
        for file_name in files:
            path = os.path.join(root, file_name)
            if Path(path).suffix.lower() in exts:
                candidates.append(path)

    if not candidates:
        return None

    candidates.sort(key=lambda p: os.path.getsize(p), reverse=True)
    return candidates[0]


def _grab_frame(cap: cv2.VideoCapture, timestamp_sec: float) -> Optional[Any]:
    cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_sec) * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def _frame_quality_score(frame: Any) -> float:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    max_width = 640
    if w > max_width:
        scale = max_width / float(w)
        small_gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
    else:
        small_gray = gray
        scale = 1.0

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    contrast = float(gray.std())

    sharp_score = min(sharpness / 180.0, 2.0) * 35.0
    exposure_score = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0) * 20.0
    contrast_score = min(contrast / 64.0, 1.5) * 15.0

    face_bonus = 0.0
    cascade = get_face_cascade()

    if cascade is not None:
        faces = cascade.detectMultiScale(
            small_gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(max(20, int(w * scale) // 12), max(20, int(h * scale) // 12)),
        )

        if len(faces) > 0:
            x_s, y_s, fw_s, fh_s = max(faces, key=lambda f: f[2] * f[3])

            x = int(x_s / scale)
            y = int(y_s / scale)
            fw = int(fw_s / scale)
            fh = int(fh_s / scale)

            margins = [
                x / w,
                y / h,
                (w - (x + fw)) / w,
                (h - (y + fh)) / h,
            ]
            min_margin = min(margins)

            if min_margin < 0.01:
                face_bonus -= 25.0
            elif min_margin < 0.03:
                face_bonus -= 15.0
            elif min_margin < 0.06:
                face_bonus -= 6.0
            else:
                face_bonus += 8.0

            face_area = (fw * fh) / float(w * h)
            if face_area > 0.40:
                face_bonus -= 14.0
            elif face_area > 0.28:
                face_bonus -= 7.0
            elif 0.05 <= face_area <= 0.25:
                face_bonus += 6.0

    return sharp_score + exposure_score + contrast_score + face_bonus

# ==========================================
# 7. Gemini Analysis
# ==========================================
@st.cache_data(show_spinner=False, ttl=3600)
def analyze_video(video_url: str) -> Dict[str, Any]:
    metadata = get_video_metadata(video_url)
    duration = int(metadata.get("duration") or 0)
    safe_duration = duration if duration > 0 else 240

    fallback_candidates = [
        int(safe_duration * 0.08),
        int(safe_duration * 0.16),
        int(safe_duration * 0.24),
        int(safe_duration * 0.33),
        int(safe_duration * 0.42),
        int(safe_duration * 0.52),
        int(safe_duration * 0.63),
        int(safe_duration * 0.74),
        int(safe_duration * 0.86),
    ]

    schema = {
        "type": "object",
        "properties": {
            "video_type": {"type": "string"},
            "display_title": {"type": "string"},
            "candidate_timestamps_sec": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "body_markdown": {"type": "string"},
        },
        "required": [
            "video_type",
            "display_title",
            "candidate_timestamps_sec",
            "body_markdown",
        ],
    }

    prompt = f"""
You are an editorial critic specializing in music videos, fashion shows, runway films, and visually driven YouTube content.

Analyze the attached public YouTube video and return JSON only.

Known YouTube metadata:
- raw_title: {metadata["title"]}
- uploader: {metadata["uploader"]}
- channel: {metadata["channel"]}
- duration_sec: {safe_duration}

Rules:
1) Set video_type to one of these values:
   - music_video
   - fashion_show
   - other
2) display_title rules:
   - If music_video: use ARTIST - SONG TITLE
   - If fashion_show: use BRAND - SHOW TITLE or BRAND - SEASON / COLLECTION
   - If other: use a concise clean title grounded in the actual source title and entities
3) candidate_timestamps_sec must contain 8 to 12 timestamps in seconds.
4) Choose timestamps that are likely to produce good editorial frames.
5) For music videos, prefer visually representative, sharp, stable, non-transition frames.
6) For fashion shows, prefer complete looks, strong silhouettes, runway-defining moments, and frames where the outfit reads clearly.
7) Avoid awkwardly clipped faces, transition frames, or heavily blurred frames.
8) body_markdown must contain exactly these three markdown sections:
   ## 1. The Core Concept
   ## 2. Visual & Styling Critique
   ## 3. Editor's Verdict
9) Do not include any prefatory sentence such as:
   "Here is an editorial review..."
10) Do not repeat the title inside body_markdown.
11) Write the body in polished English.
"""

    response = get_client().models.generate_content(
        model=TEXT_MODEL_ID,
        contents=[
            Part.from_uri(
                file_uri=video_url,
                mime_type="video/mp4",
            ),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0.25,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.LOW
            ),
            max_output_tokens=2200,
        ),
    )

    data = parse_json_from_model_output(response.text)

    video_type = (data.get("video_type") or "other").strip().lower()
    if video_type not in {"music_video", "fashion_show", "other"}:
        video_type = "other"

    display_title = (data.get("display_title") or "").strip() or fallback_display_title(metadata)
    body_markdown = clean_body_markdown(data.get("body_markdown") or "")

    candidate_timestamps: List[int] = []
    for ts in data.get("candidate_timestamps_sec", []):
        try:
            value = int(ts)
        except Exception:
            continue
        value = max(0, min(value, max(safe_duration - 1, 0)))
        if value not in candidate_timestamps:
            candidate_timestamps.append(value)

    if len(candidate_timestamps) < 8:
        for ts in fallback_candidates:
            ts = max(0, min(ts, max(safe_duration - 1, 0)))
            if ts not in candidate_timestamps:
                candidate_timestamps.append(ts)

    candidate_timestamps = sorted(candidate_timestamps[:12])

    return {
        "video_type": video_type,
        "display_title": display_title,
        "body_markdown": body_markdown,
        "candidate_timestamps_sec": candidate_timestamps,
    }

# ==========================================
# 8. Frame Extraction
# ==========================================
@st.cache_data(show_spinner=False, ttl=3600)
def extract_candidate_frames(
    video_url: str,
    candidate_timestamps: List[int],
    metadata_duration: int,
) -> List[Dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = os.path.join(tmpdir, "source.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "format": DOWNLOAD_FORMAT,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(video_url, download=True)

        video_path = _find_downloaded_video_file(tmpdir)
        if not video_path:
            raise RuntimeError("No downloadable video file was found after yt-dlp finished.")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError("Downloaded video could not be opened for frame extraction.")

        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        detected_duration = (frame_count / fps) if fps and frame_count else 0
        safe_duration = float(detected_duration or metadata_duration or 240)

        probe_times: List[float] = []
        local_offsets = [-1.0, -0.5, 0.0, 0.5, 1.0]

        for base_ts in candidate_timestamps[:12]:
            for offset in local_offsets:
                probe_times.append(_clamp_ts(base_ts + offset, safe_duration))

        for frac in [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]:
            probe_times.append(_clamp_ts(safe_duration * frac, safe_duration))

        unique_times = sorted({round(ts, 1) for ts in probe_times})

        frames: List[Dict[str, Any]] = []

        for ts in unique_times:
            frame = _grab_frame(cap, ts)
            if frame is None:
                continue

            score = _frame_quality_score(frame)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 96])
            if not ok:
                continue

            frames.append({
                "timestamp_sec": round(float(ts), 1),
                "jpg_bytes": encoded.tobytes(),
                "score": round(float(score), 2),
            })

        cap.release()

        if not frames:
            return []

        frames.sort(key=lambda x: x["score"], reverse=True)

        diversified: List[Dict[str, Any]] = []
        for item in frames:
            if all(abs(item["timestamp_sec"] - kept["timestamp_sec"]) >= 1.5 for kept in diversified):
                diversified.append(item)
            if len(diversified) == 12:
                break

        return diversified if diversified else frames[:12]


def choose_editorial_frame_set(
    display_title: str,
    video_type: str,
    candidate_frames: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not candidate_frames:
        return {
            "hero_index": 0,
            "supporting_indices": [],
            "reason": "No candidate frames available.",
        }

    if len(candidate_frames) == 1:
        return {
            "hero_index": 0,
            "supporting_indices": [],
            "reason": "Only one usable frame was available.",
        }

    desired_support_count = min(MAX_SELECTED_FRAMES - 1, len(candidate_frames) - 1)

    schema = {
        "type": "object",
        "properties": {
            "hero_index": {"type": "integer"},
            "supporting_indices": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "reason": {"type": "string"},
        },
        "required": ["hero_index", "supporting_indices", "reason"],
    }

    prompt = f"""
You are choosing an editorial frame set for a visual zine layout.

Title:
{display_title}

Video type:
{video_type}

Select:
- 1 hero frame
- {desired_support_count} supporting frames

Selection priorities:
1) Strong hero frame with a clean, stable, representative composition
2) Sharp, readable frames with low motion blur
3) No transition frames
4) No awkward edge clipping
5) Visual diversity across the selected set
6) For fashion_show: prioritize complete looks, strong silhouettes, and variety across outfits or runway moments
7) For music_video: prioritize variety across memorable scenes, moods, and visual motifs

Return JSON only.
"""

    contents: List[Any] = [prompt]

    for i, item in enumerate(candidate_frames):
        contents.append(f"Candidate {i} - timestamp {item['timestamp_sec']} seconds")
        contents.append(Part.from_bytes(data=item["jpg_bytes"], mime_type="image/jpeg"))

    try:
        response = get_client().models.generate_content(
            model=TEXT_MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
                response_schema=schema,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                max_output_tokens=400,
            ),
        )
        data = parse_json_from_model_output(response.text)
    except Exception:
        data = {}

    try:
        hero_index = int(data.get("hero_index", 0))
    except Exception:
        hero_index = 0

    hero_index = max(0, min(hero_index, len(candidate_frames) - 1))

    supporting_indices: List[int] = []
    for idx in data.get("supporting_indices", []):
        try:
            value = int(idx)
        except Exception:
            continue
        if 0 <= value < len(candidate_frames) and value != hero_index and value not in supporting_indices:
            supporting_indices.append(value)
        if len(supporting_indices) == desired_support_count:
            break

    for idx in range(len(candidate_frames)):
        if idx != hero_index and idx not in supporting_indices:
            supporting_indices.append(idx)
        if len(supporting_indices) == desired_support_count:
            break

    reason = (data.get("reason") or "").strip() or "Selected the cleanest and most representative editorial frame set."

    return {
        "hero_index": hero_index,
        "supporting_indices": supporting_indices,
        "reason": reason,
    }


@st.cache_data(show_spinner=False, ttl=3600)
def download_thumbnail_fallback(image_url: str) -> Optional[bytes]:
    if not image_url:
        return None
    response = requests.get(image_url, timeout=20)
    response.raise_for_status()
    return response.content

# ==========================================
# 9. Conversational Editorial Layer
# ==========================================
def stream_editor_reply(
    metadata: Dict[str, Any],
    analysis: Dict[str, Any],
    messages: List[Dict[str, Any]],
    user_question: str,
):
    transcript = build_conversation_transcript(messages[:-1], limit=12)
    reference_image_parts = collect_recent_image_parts(messages, max_images=MAX_CHAT_CONTEXT_IMAGES)

    base_prompt = f"""
You are a sharp editorial critic and collaborative co-editor.

Current source video:
- display_title: {analysis["display_title"]}
- video_type: {analysis["video_type"]}
- raw_title: {metadata.get("title", "")}
- uploader: {metadata.get("uploader", "")}

Base editorial review:
{analysis["body_markdown"]}

Previous conversation:
{transcript}

Latest user message:
{user_question}

Instructions:
1) Reply as a smart co-editor, not as a chatbot.
2) Build on the existing analysis and the ongoing conversation.
3) Stay grounded in the source video and avoid inventing scenes or claims.
4) Use any attached conversation images as reference material when they are relevant.
5) Any attached conversation images already exist inside the app and can be placed directly into the final magazine layout. Never claim that you cannot insert or include them.
6) If the user asks which uploaded looks or frames should be included, answer as an editor making concrete layout recommendations grounded in the uploaded images.
7) Default length: 3 to 6 substantial paragraphs unless the user asks for brevity.
8) Complete the final sentence cleanly.
9) Do not add generic prefatory phrases like:
   "Here is my answer" or "Sure, I'd be happy to help."
"""

    full_reply = ""

    for attempt in range(2):
        if attempt == 0:
            contents: List[Any] = [base_prompt] + reference_image_parts
        else:
            continuation_prompt = f"""
The previous assistant reply was cut off by the output limit.
Continue the exact same reply from where it stopped.
Do not repeat earlier sentences.
Do not restart the answer.
Start with the next unfinished phrase or sentence and finish cleanly.

Reply so far:
{full_reply[-4000:]}
"""
            contents = [base_prompt] + reference_image_parts + [continuation_prompt]

        last_chunk = None
        for chunk in get_client().models.generate_content_stream(
            model=TEXT_MODEL_ID,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.5,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                max_output_tokens=2200,
            ),
        ):
            last_chunk = chunk
            chunk_text = chunk.text or ""
            if chunk_text:
                full_reply += chunk_text
                yield chunk_text

        finish_reason = ""
        if last_chunk is not None:
            candidates = getattr(last_chunk, "candidates", None) or []
            if candidates:
                finish_reason = str(getattr(candidates[0], "finish_reason", ""))

        if "MAX_TOKENS" not in finish_reason.upper():
            break

    return

# ==========================================
# 10. Publishing Layer
# ==========================================
def publish_issue(
    metadata: Dict[str, Any],
    analysis: Dict[str, Any],
    selected_frames: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    num_frames = max(1, len(selected_frames))
    uploaded_images = collect_magazine_uploaded_images(
        messages,
        max_images=MAX_MAGAZINE_UPLOADED_IMAGES,
    )
    num_uploaded_images = len(uploaded_images)
    transcript = build_conversation_transcript(messages, limit=20)

    schema = {
        "type": "object",
        "properties": {
            "issue_title": {"type": "string"},
            "deck": {"type": "string"},
            "cover_line": {"type": "string"},
            "pull_quote": {"type": "string"},
            "visual_prompt": {"type": "string"},
            "final_markdown": {"type": "string"},
            "frame_captions": {
                "type": "array",
                "minItems": num_frames,
                "maxItems": num_frames,
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "label": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                    "required": ["index", "label", "caption"],
                },
            },
            "uploaded_image_notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "label": {"type": "string"},
                        "caption": {"type": "string"},
                    },
                    "required": ["index", "label", "caption"],
                },
            },
        },
        "required": [
            "issue_title",
            "deck",
            "cover_line",
            "pull_quote",
            "visual_prompt",
            "final_markdown",
            "frame_captions",
            "uploaded_image_notes",
        ],
    }

    prompt = f"""
You are publishing the final issue of a digital webzine.

Source video:
- display_title: {analysis["display_title"]}
- video_type: {analysis["video_type"]}
- raw_title: {metadata.get("title", "")}
- uploader: {metadata.get("uploader", "")}

Base analysis:
{analysis["body_markdown"]}

Conversation transcript:
{transcript}

Available visual assets:
- selected_source_frames: {num_frames}
- uploaded_conversation_images: {num_uploaded_images}

Instructions:
1) Integrate the strongest ideas from the conversation, but stay grounded in the source video.
2) If the conversation added a specific critical angle, let that angle shape the issue.
3) The issue must feel publishable, not like a chatbot answer.
4) issue_title should be concise and magazine-ready.
5) deck should be one elegant sentence.
6) cover_line should be punchy and front-cover ready.
7) pull_quote should be one memorable sentence.
8) final_markdown must contain exactly these sections:
   ## Opening Spread
   ## Feature Essay
   ## Closing Note
9) Make the written issue substantially longer than a short editorial note:
   - Opening Spread: about 260 to 360 words
   - Feature Essay: about 900 to 1200 words
   - Closing Note: about 260 to 360 words
10) The overall written issue should feel at least roughly twice as substantial as a short review.
11) frame_captions must provide one label and one caption for each provided source frame.
12) uploaded_image_notes must provide one label and one caption for each uploaded conversation image index whenever uploaded images are available.
13) Treat uploaded conversation images as real magazine assets that can be placed directly into the final layout. Do not describe them as unavailable.
14) visual_prompt must describe a text-free decorative backdrop image for the final issue.
15) The visual prompt should lean sleek, editorial, high-fashion, digitally polished, and magazine-like.
16) Do not mention being an AI.
Return JSON only.
"""

    contents: List[Any] = [prompt]
    contents.extend(collect_recent_image_parts(messages, max_images=MAX_CHAT_CONTEXT_IMAGES))

    for idx, frame in enumerate(selected_frames):
        ts = frame.get("timestamp_sec")
        timestamp_label = "unknown" if ts is None else f"{ts} seconds"
        contents.append(f"Source frame {idx} - timestamp {timestamp_label}")
        contents.append(Part.from_bytes(data=frame["jpg_bytes"], mime_type="image/jpeg"))

    response = get_client().models.generate_content(
        model=TEXT_MODEL_ID,
        contents=contents,
        config=types.GenerateContentConfig(
            temperature=0.45,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.LOW
            ),
            max_output_tokens=5200,
        ),
    )

    data = parse_json_from_model_output(response.text)

    frame_captions = data.get("frame_captions", [])
    cleaned_captions: List[Dict[str, Any]] = []

    for item in frame_captions:
        try:
            idx = int(item.get("index", 0))
        except Exception:
            idx = 0
        idx = max(0, min(idx, num_frames - 1))
        cleaned_captions.append({
            "index": idx,
            "label": (item.get("label") or f"Frame {idx + 1}").strip(),
            "caption": (item.get("caption") or "").strip(),
        })

    cleaned_captions.sort(key=lambda x: x["index"])

    seen_indices = {item["index"] for item in cleaned_captions}
    for idx in range(num_frames):
        if idx not in seen_indices:
            cleaned_captions.append({
                "index": idx,
                "label": f"Frame {idx + 1}",
                "caption": "A selected editorial frame from the source video.",
            })

    cleaned_captions.sort(key=lambda x: x["index"])

    uploaded_image_notes = data.get("uploaded_image_notes", [])
    cleaned_uploaded_notes: List[Dict[str, Any]] = []

    for item in uploaded_image_notes:
        try:
            idx = int(item.get("index", 0))
        except Exception:
            idx = 0
        if not (0 <= idx < num_uploaded_images):
            continue
        cleaned_uploaded_notes.append({
            "index": idx,
            "label": (item.get("label") or f"Uploaded Look {idx + 1}").strip(),
            "caption": (item.get("caption") or "").strip(),
        })

    cleaned_uploaded_notes.sort(key=lambda x: x["index"])
    seen_uploaded_indices = {item["index"] for item in cleaned_uploaded_notes}
    for idx in range(num_uploaded_images):
        if idx not in seen_uploaded_indices:
            cleaned_uploaded_notes.append({
                "index": idx,
                "label": f"Uploaded Look {idx + 1}",
                "caption": "A conversation image included as a visual reference in the final issue.",
            })

    cleaned_uploaded_notes.sort(key=lambda x: x["index"])

    return {
        "issue_title": (data.get("issue_title") or analysis["display_title"]).strip(),
        "deck": (data.get("deck") or "").strip(),
        "cover_line": (data.get("cover_line") or "").strip(),
        "pull_quote": (data.get("pull_quote") or "").strip(),
        "visual_prompt": (data.get("visual_prompt") or "").strip(),
        "final_markdown": (data.get("final_markdown") or "").strip(),
        "frame_captions": cleaned_captions,
        "uploaded_image_notes": cleaned_uploaded_notes,
    }

def generate_issue_visual(
    issue: Dict[str, Any],
    aspect_ratio: str,
) -> Dict[str, Any]:
    prompt = f"""
Create one premium decorative backdrop image for a digital webzine.

Creative direction:
- No text
- No letters
- No watermark
- No logo
- No readable typography
- This is not a literal screenshot recreation
- It should feel like a luxury magazine backdrop that complements the issue

Issue title:
{issue["issue_title"]}

Deck:
{issue["deck"]}

Cover line mood:
{issue["cover_line"]}

Pull quote mood:
{issue["pull_quote"]}

Visual direction:
{issue["visual_prompt"]}

Additional styling:
- luxury editorial composition
- polished design language
- cinematic texture
- clean hierarchy
- refined negative space
"""

    last_error = None

    for model_id in IMAGE_MODEL_CANDIDATES:
        try:
            response = get_client().models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=[types.Modality.TEXT, types.Modality.IMAGE],
                    image_config=types.ImageConfig(
                        aspect_ratio=aspect_ratio,
                        output_mime_type="image/jpeg",
                    ),
                ),
            )

            image_bytes = extract_inline_image_bytes(response)
            if image_bytes:
                return {
                    "image_bytes": image_bytes,
                    "model_id": model_id,
                }
        except Exception as exc:
            last_error = exc

    return {
        "image_bytes": None,
        "model_id": None,
        "error": str(last_error) if last_error else "Image generation returned no image.",
    }


@st.cache_data(show_spinner=False, ttl=3600)
def build_bgm_blueprint(deck: str, final_markdown: str) -> Dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "genre_style": {"type": "string"},
            "mood_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 3,
                "maxItems": 5,
            },
            "instrumentation": {"type": "string"},
            "tempo_feel": {"type": "string"},
            "arrangement": {"type": "string"},
        },
        "required": [
            "genre_style",
            "mood_keywords",
            "instrumentation",
            "tempo_feel",
            "arrangement",
        ],
    }

    prompt = f"""
You are preparing a safe text-to-music prompt blueprint for Lyria 2.

Source editorial text:
Deck: {deck}
Editorial body excerpt:
{final_markdown[:2200]}

Return JSON only.

Rules:
1) Use US English only.
2) Output only generic musical descriptors.
3) Do not include artist names, brands, labels, song titles, collection names, people, companies, places, lyrics, or copyrighted works.
4) Do not imitate any named artist or existing song.
5) Focus on genre/style, mood, instrumentation, tempo feel, and arrangement.
6) Keep every field concise and safe for a music-generation prompt.
"""

    try:
        response = get_client().models.generate_content(
            model=TEXT_MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
                response_schema=schema,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                max_output_tokens=500,
            ),
        )
        data = parse_json_from_model_output(response.text)
    except Exception:
        data = {}

    moods = [
        str(item).strip().lower()
        for item in data.get("mood_keywords", [])
        if str(item).strip()
    ]
    moods = moods[:5]

    return {
        "genre_style": (data.get("genre_style") or "cinematic ambient editorial instrumental").strip(),
        "mood_keywords": moods or ["refined", "atmospheric", "immersive"],
        "instrumentation": (data.get("instrumentation") or "layered synthesizers, soft percussion, subtle strings").strip(),
        "tempo_feel": (data.get("tempo_feel") or "moderate tempo with a steady, unobtrusive pulse").strip(),
        "arrangement": (data.get("arrangement") or "slow build, elegant texture shifts, and a clean cinematic finish").strip(),
    }


def _call_lyria_request(prompt: str, negative_prompt: str) -> bytes:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(GoogleAuthRequest())

    endpoint = (
        f"https://{LYRIA_LOCATION}-aiplatform.googleapis.com/v1/"
        f"projects/{PROJECT_ID}/locations/{LYRIA_LOCATION}/publishers/google/models/{LYRIA_MODEL_ID}:predict"
    )

    payload = {
        "instances": [
            {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
            }
        ],
        "parameters": {
            "sample_count": 1,
        },
    }

    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
            "x-goog-user-project": PROJECT_ID,
        },
        json=payload,
        timeout=180,
    )

    if not response.ok:
        try:
            payload = response.json()
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = error.get("message") or json.dumps(payload, ensure_ascii=False)
        except Exception:
            message = response.text.strip() or "Unknown error"
        raise RuntimeError(f"Lyria request failed ({response.status_code}): {message}")

    data = response.json()

    def _extract_b64_audio(prediction: Any) -> Optional[str]:
        if not isinstance(prediction, dict):
            return None

        for key in (
            "audioContent",
            "audio",
            "audio_content",
            "audio_base64",
            "audio_b64",
            "base64Audio",
            "base64_audio",
            "bytesBase64Encoded",
            "b64",
            "content",
            "data",
        ):
            value = prediction.get(key)
            if isinstance(value, str) and value.strip():
                return value

        nested = prediction.get("audio")
        if isinstance(nested, dict):
            nested_value = nested.get("content") or nested.get("bytesBase64Encoded")
            if isinstance(nested_value, str) and nested_value.strip():
                return nested_value

        return None

    predictions = data.get("predictions", []) or data.get("outputs", [])
    if not predictions:
        raise RuntimeError("Lyria returned no predictions.")

    audio_b64 = _extract_b64_audio(predictions[0]) if predictions else None
    if not audio_b64:
        if predictions and isinstance(predictions[0], dict):
            keys = list(predictions[0].keys())
            raise RuntimeError(f"Lyria response did not include recognisable audio payload. prediction keys: {keys}")
        raise RuntimeError("Lyria response did not include recognisable audio payload.")

    try:
        return base64.b64decode(audio_b64)
    except Exception as exc:
        raise RuntimeError(f"Lyria response audioContent decode failed: {exc}")

@st.cache_data(show_spinner=False, ttl=3600)
def generate_issue_bgm(issue_title: str, deck: str, final_markdown: str) -> Optional[bytes]:
    blueprint = build_bgm_blueprint(deck=deck, final_markdown=final_markdown)
    mood_line = ", ".join(blueprint["mood_keywords"])

    primary_prompt = (
        f"A {blueprint['genre_style']} instrumental track. "
        f"Mood: {mood_line}. "
        f"Instrumentation: {blueprint['instrumentation']}. "
        f"Tempo and rhythm: {blueprint['tempo_feel']}. "
        f"Arrangement: {blueprint['arrangement']}. "
        "High-quality production, elegant, immersive, and suitable for reading."
    )

    fallback_prompt = (
        "A refined cinematic ambient instrumental for a luxury digital editorial. "
        "Atmospheric, polished, modern, and immersive. "
        "Layered synthesizers, soft percussion, restrained strings, and a clean subtle pulse. "
        "No vocals. Suitable for reading."
    )

    negative_prompt = (
        "vocals, singing, rap, speech, spoken word, crowd noise, applause, harsh distortion, "
        "artist imitation, copyrighted melody, named artist style, exact song recreation"
    )

    last_error = None
    for prompt in [primary_prompt, fallback_prompt]:
        try:
            return _call_lyria_request(prompt=prompt, negative_prompt=negative_prompt)
        except Exception as exc:
            last_error = exc

    if last_error:
        raise RuntimeError(str(last_error))
    raise RuntimeError("Lyria request failed for an unknown reason.")

def build_issue_export_text(
    analysis: Dict[str, Any],
    issue: Dict[str, Any],
    selected_frames: List[Dict[str, Any]],
) -> str:
    lines = [
        f"# {issue['issue_title']}",
        "",
        issue["deck"],
        "",
        f"**Cover line:** {issue['cover_line']}",
        "",
        f"> {issue['pull_quote']}",
        "",
        issue["final_markdown"],
        "",
        "## Frame Notes",
        "",
    ]

    caption_map = {item["index"]: item for item in issue.get("frame_captions", [])}
    for idx, frame in enumerate(selected_frames):
        label = caption_map.get(idx, {}).get("label", f"Frame {idx + 1}")
        caption = caption_map.get(idx, {}).get("caption", "")
        timestamp = frame.get("timestamp_sec")
        timestamp_text = "unknown time" if timestamp is None else f"{timestamp}s"
        lines.append(f"### {label} ({timestamp_text})")
        lines.append("")
        lines.append(caption)
        lines.append("")

    lines.append("## Original Review")
    lines.append("")
    lines.append(analysis["body_markdown"])
    lines.append("")
    return "\n".join(lines)


def markdown_to_simple_html(markdown_text: str) -> str:
    blocks = re.split(r"\n\s*\n", (markdown_text or "").strip())
    html_blocks: List[str] = []

    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue

        lines = [line.rstrip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            continue

        if all(line.lstrip().startswith(("- ", "* ")) for line in lines):
            items = []
            for line in lines:
                item_text = line.lstrip()[2:].strip()
                items.append(f"<li>{html.escape(item_text)}</li>")
            html_blocks.append(f"<ul>{''.join(items)}</ul>")
            continue

        first = lines[0]
        if first.startswith("## "):
            html_blocks.append(f"<h2>{html.escape(first[3:].strip())}</h2>")
            if len(lines) > 1:
                para = " ".join(line.strip() for line in lines[1:])
                html_blocks.append(f"<p>{html.escape(para)}</p>")
        elif first.startswith("# "):
            html_blocks.append(f"<h1>{html.escape(first[2:].strip())}</h1>")
            if len(lines) > 1:
                para = " ".join(line.strip() for line in lines[1:])
                html_blocks.append(f"<p>{html.escape(para)}</p>")
        elif first.startswith("> "):
            quote = " ".join(line[2:].strip() if line.startswith("> ") else line.strip() for line in lines)
            html_blocks.append(f"<blockquote>{html.escape(quote)}</blockquote>")
        else:
            para = " ".join(line.strip() for line in lines)
            html_blocks.append(f"<p>{html.escape(para)}</p>")

    return "\n".join(html_blocks)


def bytes_to_data_uri(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('utf-8')}"


def split_issue_sections(markdown_text: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current_heading: Optional[str] = None

    for raw_line in (markdown_text or "").splitlines():
        heading_match = re.match(r"^##\s+(.*)", raw_line.strip())
        if heading_match:
            current_heading = heading_match.group(1).strip()
            sections.setdefault(current_heading, [])
            continue

        if current_heading is not None:
            sections[current_heading].append(raw_line)

    normalized: Dict[str, str] = {}
    for heading, lines in sections.items():
        normalized[heading] = "\n".join(lines).strip()
    return normalized


def _build_frame_card_html(frame: Dict[str, Any], idx: int, caption_map: Dict[int, Dict[str, Any]]) -> str:
    frame_uri = bytes_to_data_uri(frame["jpg_bytes"], "image/jpeg")
    caption_item = caption_map.get(idx, {})
    label = html.escape(caption_item.get("label", f"Frame {idx + 1}"))
    caption = html.escape(caption_item.get("caption", ""))
    timestamp = frame.get("timestamp_sec")
    timestamp_text = "Source frame" if timestamp is None else f"Source frame · {timestamp}s"

    return f"""
    <figure class="frame-card">
      <img src="{frame_uri}" alt="{label}">
      <figcaption>
        <div class="frame-label">{label}</div>
        <div class="frame-time">{html.escape(timestamp_text)}</div>
        <div class="frame-caption">{caption}</div>
      </figcaption>
    </figure>
    """


def _build_notes_card_html(frame: Dict[str, Any], idx: int, caption_map: Dict[int, Dict[str, Any]]) -> str:
    frame_uri = bytes_to_data_uri(frame["jpg_bytes"], "image/jpeg")
    caption_item = caption_map.get(idx, {})
    label = html.escape(caption_item.get("label", f"Frame {idx + 1}"))
    caption = html.escape(caption_item.get("caption", ""))
    timestamp = frame.get("timestamp_sec")
    timestamp_text = "Source frame" if timestamp is None else f"{timestamp}s"

    return f"""
    <div class="notes-card">
      <img src="{frame_uri}" alt="{label}">
      <div class="notes-text">
        <div class="notes-label">{label}</div>
        <div class="notes-time">{html.escape(timestamp_text)}</div>
        <div class="notes-caption">{caption}</div>
      </div>
    </div>
    """


def _build_story_section_html(section_title: str, section_body: str) -> str:
    if not section_body.strip():
        return ""
    html_body = markdown_to_simple_html(section_body)
    return f"""
    <div class="story-section">
      <div class="story-kicker">{html.escape(section_title)}</div>
      {html_body}
    </div>
    """


def build_issue_html(
    publication: Dict[str, Any],
    selected_frames: List[Dict[str, Any]],
    include_audio: bool = True,
) -> str:
    issue = publication["issue"]
    visual_bytes = publication.get("visual_bytes")
    bgm_bytes = publication.get("bgm_bytes")
    uploaded_images = publication.get("uploaded_images", []) or []
    caption_map = {item["index"]: item for item in issue.get("frame_captions", [])}
    uploaded_caption_map = {item["index"]: item for item in issue.get("uploaded_image_notes", [])}

    sections = split_issue_sections(issue.get("final_markdown", ""))
    opening_body = sections.get("Opening Spread", "")
    feature_body = sections.get("Feature Essay", issue.get("final_markdown", ""))
    closing_body = sections.get("Closing Note", "")

    hero_frame = selected_frames[0] if selected_frames else None
    opening_frames = selected_frames[1:3]
    feature_frames = selected_frames[3:5]

    hero_html = ""
    if hero_frame is not None:
        hero_html = _build_frame_card_html(hero_frame, 0, caption_map)

    backdrop_html = ""
    if visual_bytes:
        visual_uri = bytes_to_data_uri(visual_bytes, "image/jpeg")
        backdrop_html = f'<div class="backdrop-card"><img src="{visual_uri}" alt="Editorial backdrop"></div>'

    opening_frames_html = "".join(
        _build_frame_card_html(frame, idx + 1, caption_map)
        for idx, frame in enumerate(opening_frames)
    )

    feature_frames_html = "".join(
        _build_frame_card_html(frame, idx + 3, caption_map)
        for idx, frame in enumerate(feature_frames)
    )

    notes_html = "".join(
        _build_notes_card_html(frame, idx, caption_map)
        for idx, frame in enumerate(selected_frames)
    )

    audio_html = ""
    if include_audio and bgm_bytes:
        bgm_mime_type = infer_audio_mime_type(bgm_bytes)
        bgm_uri = bytes_to_data_uri(bgm_bytes, bgm_mime_type)
        audio_html = f"""
        <div class="audio-card">
          <div class="section-kicker">Issue soundtrack</div>
          <audio controls loop preload="metadata">
            <source src="{bgm_uri}" type="{bgm_mime_type}">
          </audio>
        </div>
        """
    elif bgm_bytes:
        audio_html = """
        <div class="audio-card muted">
          <div class="section-kicker">Issue soundtrack</div>
          <div class="audio-note">The soundtrack stays in the app and HTML export. The PDF version is visual-only.</div>
        </div>
        """

    opening_html = _build_story_section_html("Opening Spread", opening_body)
    feature_html = _build_story_section_html("Feature Essay", feature_body)
    closing_html = _build_story_section_html("Closing Note", closing_body)

    fallback_opening_visuals = opening_frames_html or hero_html or backdrop_html
    fallback_feature_visuals = feature_frames_html or opening_frames_html or hero_html or backdrop_html

    def build_uploaded_card_html(image: Dict[str, Any], idx: int) -> str:
        image_uri = bytes_to_data_uri(image["data"], image.get("mime_type", "image/jpeg"))
        note = uploaded_caption_map.get(idx, {})
        label = html.escape(note.get("label", f"Uploaded Look {idx + 1}"))
        caption = html.escape(note.get("caption", "A user-uploaded visual reference included in the issue."))
        name = html.escape(image.get("name", "Uploaded image"))
        return f"""
        <figure class="upload-card">
          <img src="{image_uri}" alt="{label}">
          <figcaption>
            <div class="frame-label">{label}</div>
            <div class="frame-time">{name}</div>
            <div class="frame-caption">{caption}</div>
          </figcaption>
        </figure>
        """

    gallery_pages: List[str] = []
    if uploaded_images:
        chunks = [
            uploaded_images[idx: idx + UPLOADED_IMAGES_PER_PAGE]
            for idx in range(0, len(uploaded_images), UPLOADED_IMAGES_PER_PAGE)
        ]

        for chunk_idx, chunk in enumerate(chunks):
            page_number = 5 + chunk_idx
            cards = []
            for local_idx, image in enumerate(chunk):
                global_idx = chunk_idx * UPLOADED_IMAGES_PER_PAGE + local_idx
                cards.append(build_uploaded_card_html(image, global_idx))

            gallery_pages.append(f"""
            <section class="sheet">
              <div class="panel gallery-panel">
                <div class="section-kicker">Reference Look Gallery</div>
                <h2 class="gallery-title">Uploaded Conversation Images</h2>
                <div class="gallery-deck">These user-supplied images are treated as direct layout assets for the final issue.</div>
                <div class="upload-gallery-grid">{''.join(cards)}</div>
              </div>
              <div class="page-number">{page_number:02d}</div>
            </section>
            """)

    cover_uploaded_note = ""
    if uploaded_images:
        cover_uploaded_note = f" · Includes {len(uploaded_images)} uploaded reference image(s)"

    pages_html = f"""
    <section class="sheet">
      <div class="cover-grid">
        <div class="panel cover-copy">
          <div class="kicker">{html.escape(publication.get('mode', 'Webzine'))} Issue</div>
          <h1>{html.escape(issue.get('issue_title', 'Untitled Issue'))}</h1>
          <div class="deck">{html.escape(issue.get('deck', ''))}</div>
          <div class="cover-line">{html.escape(issue.get('cover_line', ''))}</div>
          <div class="pull-quote">{html.escape(issue.get('pull_quote', ''))}</div>
          {audio_html}
          <div class="cover-meta">Generated by the AI Visual Zine Editor · PDF magazine layout{cover_uploaded_note}</div>
        </div>
        <div class="cover-visuals">
          {backdrop_html}
          {hero_html}
        </div>
      </div>
      <div class="page-number">01</div>
    </section>

    <section class="sheet">
      <div class="story-grid">
        <div class="panel">
          {opening_html}
        </div>
        <div class="visual-column">
          {fallback_opening_visuals}
        </div>
      </div>
      <div class="page-number">02</div>
    </section>

    <section class="sheet">
      <div class="essay-grid">
        <div class="panel essay-panel">
          {feature_html}
        </div>
        <div class="essay-side">
          <div class="mini-quote">{html.escape(issue.get('pull_quote', ''))}</div>
          {fallback_feature_visuals}
        </div>
      </div>
      <div class="page-number">03</div>
    </section>

    <section class="sheet">
      <div class="closing-grid">
        <div class="panel closing-panel">
          {closing_html}
          <div class="meta-block">
            <div><strong>Issue title:</strong> {html.escape(issue.get('issue_title', ''))}</div>
            <div><strong>Deck:</strong> {html.escape(issue.get('deck', ''))}</div>
            <div><strong>Cover line:</strong> {html.escape(issue.get('cover_line', ''))}</div>
            <div><strong>Export type:</strong> {html.escape(publication.get('mode', 'Webzine'))}</div>
          </div>
        </div>
        <div class="panel">
          <div class="section-kicker">Frame Notes</div>
          <div class="notes-grid">{notes_html}</div>
        </div>
      </div>
      <div class="page-number">04</div>
    </section>
    {''.join(gallery_pages)}
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(issue.get('issue_title', 'Editorial Issue'))}</title>
<style>
  @page {{ size: A4 portrait; margin: 0; }}

  * {{ box-sizing: border-box; }}

  body {{
    margin: 0;
    font-family: Arial, Helvetica, sans-serif;
    background: #08111d;
    color: #f4f0e8;
  }}

  .doc {{
    max-width: 210mm;
    margin: 0 auto;
    padding: 0;
  }}

  .sheet {{
    width: 210mm;
    min-height: 297mm;
    padding: 14mm 14mm 13mm;
    background:
      radial-gradient(circle at top left, rgba(202, 221, 255, 0.06), transparent 36%),
      linear-gradient(180deg, #0a1322 0%, #08111d 100%);
    position: relative;
    overflow: hidden;
    page-break-after: always;
  }}

  .sheet:last-child {{
    page-break-after: auto;
  }}

  .sheet::before {{
    content: "";
    position: absolute;
    inset: 9mm;
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 18px;
    pointer-events: none;
  }}

  .panel {{
    background: rgba(13, 21, 34, 0.84);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 18px;
    padding: 7mm;
    backdrop-filter: blur(10px);
  }}

  .kicker, .story-kicker, .section-kicker {{
    text-transform: uppercase;
    letter-spacing: 0.14em;
    font-size: 9.6px;
    color: #9db1cd;
  }}

  .kicker {{
    margin-bottom: 9px;
  }}

  .cover-grid {{
    display: grid;
    grid-template-columns: 0.94fr 1.06fr;
    gap: 8mm;
    align-items: stretch;
  }}

  .cover-copy h1 {{
    margin: 0 0 10px 0;
    font-size: 33px;
    line-height: 1.02;
  }}

  .deck {{
    font-size: 13px;
    color: #d2dae7;
    line-height: 1.58;
    margin-bottom: 13px;
  }}

  .cover-line {{
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    color: #9db1cd;
    margin-bottom: 15px;
  }}

  .pull-quote {{
    font-size: 18px;
    line-height: 1.42;
    font-style: italic;
    padding: 12px 14px;
    border-left: 3px solid rgba(255,255,255,0.16);
    background: rgba(255,255,255,0.04);
    border-radius: 12px;
    margin-bottom: 16px;
  }}

  .cover-meta {{
    margin-top: 10px;
    font-size: 10px;
    color: #9db1cd;
    line-height: 1.6;
  }}

  .cover-visuals {{
    display: grid;
    gap: 7mm;
  }}

  .backdrop-card img,
  .frame-card img,
  .notes-card img,
  .upload-card img {{
    display: block;
    width: 100%;
    border-radius: 14px;
  }}

  .audio-card {{
    margin-top: 14px;
    padding: 10px 12px;
    border-radius: 12px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
  }}

  .audio-card audio {{
    width: 100%;
    margin-top: 8px;
  }}

  .audio-note {{
    font-size: 12px;
    line-height: 1.5;
    color: #d2dae7;
    margin-top: 7px;
  }}

  .story-grid {{
    display: grid;
    grid-template-columns: 1.05fr 0.95fr;
    gap: 8mm;
    align-items: start;
  }}

  .story-section h2 {{
    margin: 0 0 8px 0;
    font-size: 23px;
    line-height: 1.15;
  }}

  .story-section p,
  .essay-panel p,
  .closing-panel p {{
    font-size: 12.7px;
    line-height: 1.72;
    color: #eef2f8;
    margin: 0 0 10px 0;
  }}

  .story-section ul,
  .essay-panel ul,
  .closing-panel ul {{
    margin: 0 0 11px 18px;
  }}

  .story-section li,
  .essay-panel li,
  .closing-panel li {{
    font-size: 12.5px;
    line-height: 1.65;
    margin-bottom: 5px;
  }}

  .story-section blockquote,
  .essay-panel blockquote,
  .closing-panel blockquote {{
    margin: 0 0 12px 0;
    padding-left: 12px;
    border-left: 3px solid rgba(255,255,255,0.16);
    color: #c7d4e6;
  }}

  .visual-column {{
    display: grid;
    gap: 6mm;
  }}

  .frame-card, .upload-card {{
    margin: 0;
    break-inside: avoid;
  }}

  .frame-card figcaption,
  .upload-card figcaption {{
    margin-top: 7px;
  }}

  .frame-label {{
    font-size: 12px;
    font-weight: 700;
    margin-bottom: 2px;
  }}

  .frame-time {{
    font-size: 10px;
    color: #9db1cd;
    margin-bottom: 5px;
  }}

  .frame-caption {{
    font-size: 11px;
    line-height: 1.5;
    color: #d4dde9;
  }}

  .essay-grid {{
    display: grid;
    grid-template-columns: 1.22fr 0.78fr;
    gap: 8mm;
    align-items: start;
  }}

  .essay-panel h2,
  .closing-panel h2,
  .gallery-title {{
    margin: 0 0 8px 0;
    font-size: 24px;
    line-height: 1.15;
  }}

  .essay-side {{
    display: grid;
    gap: 6mm;
  }}

  .mini-quote {{
    padding: 12px 14px;
    border-radius: 14px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    font-size: 14px;
    line-height: 1.55;
    color: #dce5f2;
  }}

  .closing-grid {{
    display: grid;
    grid-template-columns: 0.9fr 1.1fr;
    gap: 8mm;
    align-items: start;
  }}

  .notes-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 5mm;
  }}

  .notes-card {{
    display: grid;
    grid-template-columns: 42% 1fr;
    gap: 4mm;
    padding: 4mm;
    border-radius: 14px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    align-items: start;
    break-inside: avoid;
  }}

  .notes-label {{
    font-size: 11px;
    font-weight: 700;
    margin-bottom: 3px;
  }}

  .notes-time {{
    font-size: 9.5px;
    color: #9db1cd;
    margin-bottom: 5px;
  }}

  .notes-caption {{
    font-size: 10.5px;
    line-height: 1.45;
    color: #d7deea;
  }}

  .gallery-panel {{
    min-height: 255mm;
  }}

  .gallery-deck {{
    font-size: 12px;
    line-height: 1.55;
    color: #cfd8e7;
    margin-bottom: 10px;
  }}

  .upload-gallery-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 7mm;
    align-items: start;
  }}

  .meta-block {{
    margin-top: 14px;
    padding-top: 10px;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-size: 10px;
    color: #9db1cd;
    line-height: 1.65;
  }}

  .page-number {{
    position: absolute;
    bottom: 10mm;
    right: 16mm;
    font-size: 10px;
    color: rgba(157,177,205,0.75);
  }}

  @media screen and (max-width: 980px) {{
    .sheet {{
      width: auto;
      min-height: auto;
      margin: 0 0 20px 0;
      padding: 18px;
    }}
    .cover-grid,
    .story-grid,
    .essay-grid,
    .closing-grid,
    .notes-grid,
    .upload-gallery-grid {{
      grid-template-columns: 1fr;
    }}
    .cover-copy h1 {{
      font-size: 28px;
    }}
  }}
</style>
</head>
<body>
  <div class="doc">
    {pages_html}
  </div>
</body>
</html>"""


@st.cache_data(show_spinner=False, ttl=3600)
def html_to_pdf_bytes(issue_html: str) -> Optional[bytes]:
    if WeasyHTML is None:
        return None

    return WeasyHTML(string=issue_html).write_pdf(
        optimize_images=True,
        jpeg_quality=88,
        presentational_hints=True,
    )

# ==========================================
# 11. UI
# ==========================================
st.title("🖤 AI Visual Zine Editor")
st.write(
    "Paste a public YouTube URL to analyze the video, discuss it with an editorial AI, "
    "and publish the final result as a webzine."
)

with st.form("zine_form"):
    url_input = st.text_input("Enter YouTube URL:")
    analyze_btn = st.form_submit_button("Analyze")

# ==========================================
# 12. Analyze Flow
# ==========================================
if analyze_btn:
    if not url_input.strip():
        st.warning("Enter a YouTube URL first.")
    else:
        clean_url = url_input.strip()

        try:
            with st.spinner("Analyzing the source video and selecting an editorial frame set..."):
                metadata = get_video_metadata(clean_url)
                analysis = analyze_video(clean_url)

                candidate_frames: List[Dict[str, Any]] = []
                selected_frames: List[Dict[str, Any]] = []
                selected_reason = ""

                try:
                    candidate_frames = extract_candidate_frames(
                        clean_url,
                        analysis["candidate_timestamps_sec"],
                        int(metadata.get("duration") or 0),
                    )

                    if candidate_frames:
                        selection = choose_editorial_frame_set(
                            analysis["display_title"],
                            analysis["video_type"],
                            candidate_frames,
                        )
                        selected_reason = selection["reason"]

                        selected_indices = [selection["hero_index"]] + selection["supporting_indices"]
                        unique_indices: List[int] = []
                        seen = set()

                        for idx in selected_indices:
                            if 0 <= idx < len(candidate_frames) and idx not in seen:
                                unique_indices.append(idx)
                                seen.add(idx)

                        selected_frames = [candidate_frames[idx] for idx in unique_indices[:MAX_SELECTED_FRAMES]]
                except Exception as frame_error:
                    selected_reason = f"Frame extraction fallback: {frame_error}"

                if not selected_frames:
                    thumb = download_thumbnail_fallback(metadata.get("thumbnail", ""))
                    if thumb:
                        selected_frames = [{
                            "timestamp_sec": None,
                            "jpg_bytes": thumb,
                            "score": 0.0,
                        }]
                        if not selected_reason:
                            selected_reason = "Using the thumbnail because usable video frames were not available."

                st.session_state.current_url = clean_url
                st.session_state.metadata = metadata
                st.session_state.analysis = analysis
                st.session_state.candidate_frames = candidate_frames
                st.session_state.selected_frames = selected_frames
                st.session_state.selected_reason = selected_reason
                st.session_state.messages = []
                st.session_state.published_issue = None

        except Exception as e:
            st.error(f"An error occurred during analysis: {e}")

# ==========================================
# 13. Render Current Analysis
# ==========================================
if st.session_state.analysis and st.session_state.metadata:
    metadata = st.session_state.metadata
    analysis = st.session_state.analysis
    candidate_frames = st.session_state.candidate_frames
    selected_frames = st.session_state.selected_frames
    selected_reason = st.session_state.selected_reason

    safe_title = html.escape(analysis["display_title"])

    st.markdown(
        f"""
        <div class="title-wrap">
            <div class="kicker">Visual Zine Editorial</div>
            <h1>{safe_title}</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )

    overview_tab, conversation_tab, publish_tab = st.tabs(
        ["Overview", "Conversation", "Published Issue"]
    )

    with overview_tab:
        left, right = st.columns([1.05, 1.35], gap="large")

        with left:
            if selected_frames:
                hero_frame = selected_frames[0]
                hero_caption = (
                    f"Hero frame at {hero_frame['timestamp_sec']}s"
                    if hero_frame["timestamp_sec"] is not None
                    else "Hero image"
                )
                st.image(BytesIO(hero_frame["jpg_bytes"]), caption=hero_caption, width="stretch")

                support_frames = selected_frames[1:]
                if support_frames:
                    st.markdown("<div class='section-label'>Editorial Frame Set</div>", unsafe_allow_html=True)
                    grid_cols = st.columns(2)
                    for idx, item in enumerate(support_frames):
                        caption = (
                            f"Frame at {item['timestamp_sec']}s"
                            if item["timestamp_sec"] is not None
                            else "Supporting frame"
                        )
                        with grid_cols[idx % 2]:
                            st.image(BytesIO(item["jpg_bytes"]), caption=caption, width="stretch")
            else:
                st.warning("No usable images were found for this video.")

            if selected_reason:
                st.markdown(
                    f"<div class='small-note'>{html.escape(selected_reason)}</div>",
                    unsafe_allow_html=True,
                )

            if metadata.get("title"):
                st.markdown(
                    f"<div class='meta-line'>Raw YouTube title: {html.escape(metadata['title'])}</div>",
                    unsafe_allow_html=True,
                )

            if metadata.get("uploader"):
                st.markdown(
                    f"<div class='meta-line'>Uploader: {html.escape(metadata['uploader'])}</div>",
                    unsafe_allow_html=True,
                )

            st.markdown(
                f"<div class='meta-line'>Detected type: {html.escape(analysis['video_type'])}</div>",
                unsafe_allow_html=True,
            )

        with right:
            st.markdown(analysis["body_markdown"])

        if candidate_frames:
            with st.expander("See more candidate frames"):
                preview_cols = st.columns(3)
                selected_signature = {
                    (item.get("timestamp_sec"), len(item.get("jpg_bytes", b"")))
                    for item in selected_frames
                }

                for idx, item in enumerate(candidate_frames):
                    label = f"{item['timestamp_sec']}s · score {item['score']}"
                    signature = (item.get("timestamp_sec"), len(item.get("jpg_bytes", b"")))
                    if signature in selected_signature:
                        label += " · selected"

                    with preview_cols[idx % 3]:
                        st.image(BytesIO(item["jpg_bytes"]), width="stretch")
                        st.caption(label)

    with conversation_tab:
        st.markdown(
            "<div class='chat-hint'>Use the chat to refine the editorial angle before publishing. "
            "You can ask for symbolism, fashion history, visual grammar, ideology, performance logic, "
            "or comparisons with prior eras and projects. You can also attach reference images.</div>",
            unsafe_allow_html=True,
        )

        if not st.session_state.messages:
            st.info("No conversation yet. Start the discussion below.")

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                content = (msg.get("content") or "").strip()
                if content:
                    st.markdown(content)
                render_message_images(msg.get("images", []) or [])
                if not content and not (msg.get("images", []) or []):
                    st.caption("Empty message")

        chat_submission = st.chat_input(
            "Discuss the current video with the editor...",
            accept_file="multiple",
            file_type=["png", "jpg", "jpeg", "webp"],
            max_upload_size=15,
            key="conversation_input",
        )

        if chat_submission:
            if isinstance(chat_submission, str):
                user_text = chat_submission.strip()
                uploaded_files = []
            else:
                user_text = (chat_submission.text or "").strip()
                uploaded_files = list(chat_submission.files or [])

            uploaded_images: List[Dict[str, Any]] = []
            failed_image_uploads = 0
            for uploaded_file in uploaded_files:
                normalized = normalize_uploaded_image(uploaded_file)
                if normalized is None:
                    failed_image_uploads += 1
                    continue
                uploaded_images.append(normalized)

            if failed_image_uploads:
                st.warning(f"{failed_image_uploads} attached image file(s) could not be processed and were skipped.")

            if user_text or uploaded_images:
                user_message = {
                    "role": "user",
                    "content": user_text,
                    "images": uploaded_images,
                }
                st.session_state.messages.append(user_message)

                with st.chat_message("user"):
                    if user_text:
                        st.markdown(user_text)
                    render_message_images(uploaded_images)
                    if not user_text and uploaded_images:
                        st.caption("Image attachment")

                with st.chat_message("assistant"):
                    with st.spinner("Developing the next editorial angle..."):
                        reply = st.write_stream(
                            stream_editor_reply(
                                metadata=metadata,
                                analysis=analysis,
                                messages=st.session_state.messages,
                                user_question=user_text or "Please analyze the attached image(s) in relation to the current video.",
                            )
                        )

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": (reply or "").strip(),
                    "images": [],
                })
                st.rerun()

    with publish_tab:
        control_left, control_right = st.columns([1, 1])

        with control_left:
            issue_aspect_ratio = st.selectbox(
                "Backdrop aspect ratio",
                options=["3:4", "4:5", "9:16", "1:1", "16:9"],
                index=0,
                key="issue_aspect_ratio",
            )

        with control_right:
            generate_bgm = st.checkbox(
                "Generate background music (slower)",
                value=False,
                key="generate_bgm",
            )

        publish_btn = st.button("Publish Final Webzine", type="primary")
        if publish_btn:
            try:
                with st.spinner("Publishing the final issue..."):
                    issue = publish_issue(
                        metadata=metadata,
                        analysis=analysis,
                        selected_frames=selected_frames,
                        messages=st.session_state.messages,
                    )

                    visual_result = generate_issue_visual(
                        issue=issue,
                        aspect_ratio=issue_aspect_ratio,
                    )

                    bgm_bytes = None
                    bgm_error = None
                    bgm_mime_type = None

                    if generate_bgm:
                        try:
                            bgm_result = generate_issue_bgm(
                                issue_title=issue["issue_title"],
                                deck=issue["deck"],
                                final_markdown=issue["final_markdown"],
                            )
                            bgm_bytes = bgm_result
                            bgm_mime_type = infer_audio_mime_type(bgm_bytes)
                        except Exception as exc:
                            bgm_error = str(exc)

                    uploaded_images = collect_magazine_uploaded_images(
                        st.session_state.messages,
                        max_images=MAX_MAGAZINE_UPLOADED_IMAGES,
                    )

                    st.session_state.published_issue = {
                        "mode": "Webzine",
                        "aspect_ratio": issue_aspect_ratio,
                        "issue": issue,
                        "visual_bytes": visual_result.get("image_bytes"),
                        "image_model_id": visual_result.get("model_id"),
                        "image_error": visual_result.get("error"),
                        "bgm_bytes": bgm_bytes,
                        "bgm_mime_type": bgm_mime_type,
                        "bgm_error": bgm_error,
                        "uploaded_images": uploaded_images,
                    }
            except Exception as e:
                st.error(f"An error occurred during publishing: {e}")

        if st.session_state.published_issue:
            publication = st.session_state.published_issue
            issue = publication["issue"]
            visual_bytes = publication.get("visual_bytes")
            image_model_id = publication.get("image_model_id")
            image_error = publication.get("image_error")
            bgm_bytes = publication.get("bgm_bytes")
            bgm_error = publication.get("bgm_error")
            uploaded_images = publication.get("uploaded_images", []) or []

            st.divider()

            pub_left, pub_right = st.columns([1.05, 1.35], gap="large")

            with pub_left:
                if visual_bytes:
                    st.image(
                        BytesIO(visual_bytes),
                        caption=f"Generated editorial backdrop · {image_model_id}",
                        width="stretch",
                    )
                else:
                    st.warning("The issue text was published, but the generated backdrop was unavailable.")
                    if image_error:
                        st.caption(image_error)

                if bgm_bytes:
                    st.audio(
                        bgm_bytes,
                        format=publication.get("bgm_mime_type", "audio/wav"),
                        autoplay=True,
                        loop=True,
                        width="stretch",
                    )
                    st.caption("Generated with Lyria 2. Some browsers may still block autoplay until after a click.")
                elif bgm_error:
                    st.warning("The webzine published successfully, but background music generation failed.")
                    st.caption(bgm_error)

                st.markdown("<div class='section-label'>Selected Source Frames</div>", unsafe_allow_html=True)

                if selected_frames:
                    frame_cols = st.columns(2)
                    caption_map = {item["index"]: item for item in issue.get("frame_captions", [])}

                    for idx, frame in enumerate(selected_frames):
                        with frame_cols[idx % 2]:
                            st.image(BytesIO(frame["jpg_bytes"]), width="stretch")
                            caption_item = caption_map.get(idx, {})
                            label = caption_item.get("label", f"Frame {idx + 1}")
                            caption = caption_item.get("caption", "")
                            timestamp = frame.get("timestamp_sec")
                            ts_text = "unknown time" if timestamp is None else f"{timestamp}s"
                            st.markdown(
                                f"<div class='caption-card'><strong>{html.escape(label)}</strong><br>"
                                f"{html.escape(ts_text)}<br>{html.escape(caption)}</div>",
                                unsafe_allow_html=True,
                            )

                if uploaded_images:
                    st.markdown("<div class='section-label'>Uploaded Reference Images</div>", unsafe_allow_html=True)
                    uploaded_note_map = {item["index"]: item for item in issue.get("uploaded_image_notes", [])}
                    with st.expander(f"Show all {len(uploaded_images)} uploaded image(s) included in the issue"):
                        upload_cols = st.columns(2)
                        for idx, image in enumerate(uploaded_images):
                            with upload_cols[idx % 2]:
                                st.image(BytesIO(image["data"]), width="stretch")
                                note = uploaded_note_map.get(idx, {})
                                label = note.get("label", f"Uploaded Look {idx + 1}")
                                caption = note.get("caption", "")
                                name = image.get("name", "Uploaded image")
                                st.markdown(
                                    f"<div class='caption-card'><strong>{html.escape(label)}</strong><br>"
                                    f"{html.escape(name)}<br>{html.escape(caption)}</div>",
                                    unsafe_allow_html=True,
                                )

            with pub_right:
                st.markdown(
                    f"""
                    <div class="title-wrap">
                        <div class="kicker">{html.escape(publication["mode"])} Issue</div>
                        <h1>{html.escape(issue["issue_title"])}</h1>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if issue.get("deck"):
                    st.markdown(
                        f"<div class='issue-deck'>{html.escape(issue['deck'])}</div>",
                        unsafe_allow_html=True,
                    )

                if issue.get("cover_line"):
                    st.caption(f"Cover line: {issue['cover_line']}")

                if issue.get("pull_quote"):
                    st.markdown(
                        f"<div class='pull-quote'>{html.escape(issue['pull_quote'])}</div>",
                        unsafe_allow_html=True,
                    )

                st.markdown(issue["final_markdown"])

                export_text = build_issue_export_text(
                    analysis=analysis,
                    issue=issue,
                    selected_frames=selected_frames,
                )

                issue_html = build_issue_html(
                    publication=publication,
                    selected_frames=selected_frames,
                    include_audio=True,
                )
                pdf_issue_html = build_issue_html(
                    publication=publication,
                    selected_frames=selected_frames,
                    include_audio=False,
                )
                pdf_bytes = html_to_pdf_bytes(pdf_issue_html)

                preview_pages = 4 + math.ceil(len(uploaded_images) / UPLOADED_IMAGES_PER_PAGE) if uploaded_images else 4
                preview_height = max(2200, 830 * preview_pages)

                with st.expander("Preview export layout"):
                    html_iframe(issue_html, height=preview_height, scrolling=True)

                download_cols = st.columns(3)

                with download_cols[0]:
                    st.download_button(
                        "Download issue as Markdown",
                        data=export_text,
                        file_name=f"{slugify(issue['issue_title'])}.md",
                        mime="text/markdown",
                        width="stretch",
                    )

                with download_cols[1]:
                    st.download_button(
                        "Download issue as HTML",
                        data=issue_html,
                        file_name=f"{slugify(issue['issue_title'])}.html",
                        mime="text/html",
                        width="stretch",
                    )

                with download_cols[2]:
                    if pdf_bytes:
                        st.download_button(
                            "Download issue as PDF",
                            data=pdf_bytes,
                            file_name=f"{slugify(issue['issue_title'])}.pdf",
                            mime="application/pdf",
                            width="stretch",
                        )
                    else:
                        st.caption("PDF export unavailable. Install WeasyPrint to enable it.")

                if uploaded_images:
                    st.caption("HTML keeps the optional soundtrack player. PDF exports as a multi-page magazine layout, including gallery pages for the uploaded reference images.")
                else:
                    st.caption("HTML keeps the optional soundtrack player. PDF exports as a four-page magazine-style layout.")









