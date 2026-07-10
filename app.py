"""Voice Note Transcriber — Streamlit app.

Upload voice notes (including WhatsApp .opus / .m4a) and get back editable text
transcriptions you can download as .txt (or all together as a .zip).

Engines:
  * OpenAI   — /v1/audio/transcriptions (gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1)
  * ElevenLabs Scribe — /v1/speech-to-text (scribe_v2)

Optimised for English + Urdu + Pashto, including code-switched (mixed) speech.
"""

import io
import zipfile
from pathlib import Path

import requests
import streamlit as st

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"

OPENAI_MODELS = ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"]

# UI label -> per-engine language code.
# OpenAI expects ISO 639-1 (en/ur/ps); ElevenLabs expects ISO 639-3 (eng/urd/pus).
# None = let the engine auto-detect (best for mixed-language notes).
LANGUAGES = {
    "Auto-detect (recommended)": {"openai": None, "elevenlabs": None},
    "English": {"openai": "en", "elevenlabs": "eng"},
    "Urdu": {"openai": "ur", "elevenlabs": "urd"},
    "Pashto": {"openai": "ps", "elevenlabs": "pus"},
}

DEFAULT_PROMPT = (
    "The audio may mix Urdu, Pashto, and English. Transcribe verbatim, "
    "keeping each language in its natural script."
)

UPLOAD_TYPES = ["mp3", "wav", "m4a", "ogg", "opus", "flac", "webm", "mp4", "aac", "amr"]

# Formats that speech-to-text APIs often reject/handle poorly -> transcode to mp3
# first (needs ffmpeg via pydub). Everything else is sent as-is.
CONVERT_EXTS = {"opus", "amr"}

# ffmpeg demuxer name to use when decoding. It is NOT always the file extension:
# a WhatsApp ".opus" note is Ogg-encapsulated, so ffmpeg needs "ogg" (there is no
# demuxer literally named "opus"). Anything not listed falls back to auto-detect.
DECODE_FORMAT = {"opus": "ogg"}

MIME_BY_EXT = {
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg",
    "opus": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm", "mp4": "audio/mp4",
    "aac": "audio/aac", "amr": "audio/amr",
}

# Placeholder values shipped in secrets.toml — treated as "not set" so the
# sidebar fallback field is used until the user pastes a real key.
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "PASTE_YOUR", "YOUR_KEY_HERE")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def get_ext(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def resolve_key(secret_name: str, fallback: str) -> str:
    """Prefer st.secrets; fall back to the sidebar field. Placeholder values in
    secrets.toml are ignored so they don't shadow a key typed in the sidebar."""
    try:
        value = st.secrets.get(secret_name, "")  # raises if no secrets file at all
    except Exception:
        value = ""
    if value and not any(m in value for m in PLACEHOLDER_MARKERS):
        return value
    return (fallback or "").strip()


def maybe_convert_to_mp3(raw: bytes, ext: str):
    """Return (bytes, ext, note). Transcode 'unusual' formats to mp3 via
    pydub/ffmpeg. On any failure (e.g. ffmpeg missing) fall back to the original
    bytes so transcription can still be attempted."""
    if ext not in CONVERT_EXTS:
        return raw, ext, None
    try:
        from pydub import AudioSegment

        # Prefer the mapped demuxer; if that fails, let ffmpeg auto-detect.
        try:
            segment = AudioSegment.from_file(io.BytesIO(raw), format=DECODE_FORMAT.get(ext, ext))
        except Exception:
            segment = AudioSegment.from_file(io.BytesIO(raw))
        buf = io.BytesIO()
        segment.export(buf, format="mp3")
        return buf.getvalue(), "mp3", f"Converted .{ext} → .mp3 for transcription."
    except Exception as exc:  # ffmpeg missing, or decode failure
        return raw, ext, (
            f"⚠️ Could not convert .{ext} (ffmpeg may be missing: {exc}). "
            "Sending the original file instead."
        )


def _format_api_error(engine: str, resp: requests.Response) -> str:
    detail = resp.text
    try:
        payload = resp.json()
        err = payload.get("error", payload)
        detail = err.get("message", err) if isinstance(err, dict) else err
    except Exception:
        pass
    return f"{engine} API returned {resp.status_code}: {detail}"


def transcribe_openai(raw, filename, ext, api_key, model, lang, prompt) -> str:
    files = {"file": (filename, io.BytesIO(raw), MIME_BY_EXT.get(ext, "application/octet-stream"))}
    data = {"model": model, "response_format": "json"}
    if lang:
        data["language"] = lang
    if prompt:
        data["prompt"] = prompt
    resp = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files=files,
        data=data,
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(_format_api_error("OpenAI", resp))
    return resp.json().get("text", "")


def transcribe_elevenlabs(raw, filename, ext, api_key, lang) -> str:
    files = {"file": (filename, io.BytesIO(raw), MIME_BY_EXT.get(ext, "application/octet-stream"))}
    data = {"model_id": "scribe_v2", "tag_audio_events": "false"}
    if lang:
        data["language_code"] = lang
    resp = requests.post(
        ELEVENLABS_URL,
        headers={"xi-api-key": api_key},
        files=files,
        data=data,
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(_format_api_error("ElevenLabs", resp))
    return resp.json().get("text", "")


def build_zip(items) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in items:
            zf.writestr(name, text)
    return buf.getvalue()


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Voice Note Transcriber", page_icon="🎙️", layout="centered")

st.sidebar.title("⚙️ Settings")
engine = st.sidebar.radio("Transcription engine", ["OpenAI", "ElevenLabs Scribe"])

if engine == "OpenAI":
    model = st.sidebar.selectbox("OpenAI model", OPENAI_MODELS)
    key_field = st.sidebar.text_input(
        "OpenAI API key (fallback)", type="password",
        help="Used only if OPENAI_API_KEY is not set in .streamlit/secrets.toml.",
    )
    api_key = resolve_key("OPENAI_API_KEY", key_field)
else:
    model = None
    key_field = st.sidebar.text_input(
        "ElevenLabs API key (fallback)", type="password",
        help="Used only if ELEVENLABS_API_KEY is not set in .streamlit/secrets.toml.",
    )
    api_key = resolve_key("ELEVENLABS_API_KEY", key_field)

language_label = st.sidebar.selectbox("Language", list(LANGUAGES.keys()))
lang_codes = LANGUAGES[language_label]

st.sidebar.info(
    "**Keep Auto-detect for mixed-language voice notes.** When Urdu, Pashto and "
    "English are code-switched in one recording, forcing a single language can "
    "push the whole transcript into the wrong script."
)

st.title("🎙️ Voice Note Transcriber")
st.caption("English · Urdu · Pashto — including code-switched (mixed) speech.")

if engine == "OpenAI":
    prompt = st.text_area(
        "Context prompt (OpenAI only)", value=DEFAULT_PROMPT, height=90,
        help="Steers spelling, names and mixed-language handling. Ignored by ElevenLabs.",
    )
else:
    prompt = None
    st.caption("ℹ️ The context prompt is only used by the OpenAI engine.")

uploaded = st.file_uploader(
    "Upload voice notes",
    type=UPLOAD_TYPES,
    accept_multiple_files=True,
    help="WhatsApp .opus / .m4a supported. .opus and .amr are auto-converted to mp3.",
)

if not api_key:
    st.warning(
        "No API key found. Add it in the sidebar, or set it in "
        "`.streamlit/secrets.toml`, to enable transcription."
    )

transcribe_clicked = st.button(
    "Transcribe", type="primary", disabled=not (uploaded and api_key)
)

# --- Run transcription, store results in session_state ------------------------
if transcribe_clicked and uploaded and api_key:
    # Clear any stale edited-transcript widget state from a previous run.
    for k in [k for k in st.session_state if k.startswith("txt_")]:
        del st.session_state[k]

    results = []
    progress = st.progress(0.0, text="Starting…")
    for idx, uf in enumerate(uploaded):
        progress.progress(idx / len(uploaded), text=f"Transcribing {uf.name}…")
        raw = uf.getvalue()
        ext = get_ext(uf.name)
        send_bytes, send_ext, note = maybe_convert_to_mp3(raw, ext)
        send_name = Path(uf.name).with_suffix("." + send_ext).name

        entry = {"name": uf.name, "audio": raw, "mime": MIME_BY_EXT.get(ext),
                 "note": note, "text": "", "error": None}
        try:
            if engine == "OpenAI":
                entry["text"] = transcribe_openai(
                    send_bytes, send_name, send_ext, api_key, model,
                    lang_codes["openai"], prompt)
            else:
                entry["text"] = transcribe_elevenlabs(
                    send_bytes, send_name, send_ext, api_key, lang_codes["elevenlabs"])
        except Exception as exc:
            entry["error"] = str(exc)
        results.append(entry)

    progress.progress(1.0, text="Done.")
    st.session_state["results"] = results

# --- Render results -----------------------------------------------------------
results = st.session_state.get("results", [])
if results:
    st.divider()
    st.header("Transcripts")
    zip_items = []
    for i, item in enumerate(results):
        st.subheader(f"📄 {item['name']}")
        if item.get("audio") is not None:
            st.audio(item["audio"], format=item.get("mime") or "audio/mpeg")
        if item.get("note"):
            st.caption(item["note"])
        if item.get("error"):
            st.error(item["error"])
            continue

        base = Path(item["name"]).stem + ".txt"
        edited = st.text_area("Transcript", value=item["text"], height=180, key=f"txt_{i}")
        st.download_button(
            "⬇️ Download .txt", data=edited.encode("utf-8"),
            file_name=base, mime="text/plain", key=f"dl_{i}")
        zip_items.append((base, edited))

    if len(zip_items) > 1:
        st.divider()
        st.download_button(
            "⬇️ Download all (.zip)", data=build_zip(zip_items),
            file_name="transcripts.zip", mime="application/zip", key="dl_zip")
