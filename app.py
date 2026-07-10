"""Voice Note Transcriber — Streamlit app.

Upload voice notes (including WhatsApp .opus / .m4a) and get back editable text
transcriptions you can download as .txt (or all together as a .zip).

Engines:
  * OpenAI   — /v1/audio/transcriptions (gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1)
  * ElevenLabs Scribe — /v1/speech-to-text (scribe_v2)

Multi-key failover: give each engine several API keys and the app automatically
rotates to the next key when one returns an auth / quota / rate-limit / server
error. Optimised for English + Urdu + Pashto, including code-switched speech.
"""

import io
import re
import zipfile
from pathlib import Path

import requests
import streamlit as st

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
# Groq hosts Whisper behind an OpenAI-compatible endpoint (keys start with gsk_).
GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

OPENAI_MODELS = ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"]
GROQ_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]

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

# Optional post-step: transliterate the transcript into Latin (Roman) script.
ROMANIZE_MODEL = "gpt-4o-mini"
ROMANIZE_SYSTEM = (
    "You convert a transcript into Latin (Roman) script. You TRANSLITERATE; you do "
    "NOT translate. Rules:\n"
    "- Keep English words and phrases exactly as they are.\n"
    "- Write Urdu as Roman Urdu and Pashto as Roman Pashto, using natural, readable "
    "spelling (the way people type in chat).\n"
    "- Preserve every word and the original order and code-switching. Do not add, "
    "remove, translate, or explain anything.\n"
    "- Output ONLY the converted text, nothing else."
)

# Devanagari/Urdu sentence terminators -> a plain period, so offline
# transliteration doesn't turn them into stray " / " marks.
DANDA_MAP = {"।": ".", "॥": ".", "۔": "."}

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

# Placeholder values shipped in secrets.toml — treated as "not set" so a
# real key (from another slot or the sidebar) is used instead.
PLACEHOLDER_MARKERS = ("REPLACE_WITH", "PASTE_YOUR", "YOUR_KEY_HERE")

# HTTP statuses where a *different* key might succeed (bad/expired key, no
# permission, rate limit / quota, or a transient server error). Any other 4xx
# is a request problem (e.g. bad audio) that another key won't fix, so it is
# surfaced immediately instead of burning through every key.
RETRYABLE_STATUSES = {401, 403, 408, 429, 500, 502, 503, 504}


# -----------------------------------------------------------------------------
# Key handling (multi-key with failover)
# -----------------------------------------------------------------------------
def get_ext(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def _secret(name):
    """Read a secret without exploding when no secrets.toml exists at all."""
    try:
        return st.secrets.get(name, None)
    except Exception:
        return None


def _split_keys(value):
    """Normalise a secret/sidebar value into a list of individual keys.
    Accepts a TOML array (list) or a string with keys separated by newlines,
    commas or whitespace. API keys contain none of those, so splitting is safe."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return re.split(r"[\s,]+", str(value))


def _clean_keys(candidates):
    """Strip, drop empties/placeholders, and de-duplicate while keeping order."""
    seen, out = set(), []
    for k in candidates:
        k = (k or "").strip()
        if not k or any(m in k for m in PLACEHOLDER_MARKERS):
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def get_keys(plural_name, singular_name, sidebar_value):
    """Collect all usable keys for an engine, in priority order:
    secrets[PLURAL] (a list) -> secrets[SINGULAR] -> sidebar field."""
    candidates = []
    candidates += _split_keys(_secret(plural_name))
    candidates += _split_keys(_secret(singular_name))
    candidates += _split_keys(sidebar_value)
    return _clean_keys(candidates)


def _mask(key: str) -> str:
    return f"…{key[-4:]}" if len(key) >= 4 else "…"


# -----------------------------------------------------------------------------
# Audio conversion
# -----------------------------------------------------------------------------
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


# -----------------------------------------------------------------------------
# Transcription (with per-request key failover)
# -----------------------------------------------------------------------------
def _format_api_error(engine: str, resp: requests.Response) -> str:
    detail = resp.text
    try:
        payload = resp.json()
        err = payload.get("error", payload)
        detail = err.get("message", err) if isinstance(err, dict) else err
    except Exception:
        pass
    return f"{engine} returned {resp.status_code}: {detail}"


def _run_with_failover(make_request, keys, engine, dead, extract=None):
    """Try each key until one returns HTTP 200.

    `make_request(key)` must build and send a fresh request (the upload body is
    single-use, so it is rebuilt per attempt) and return a requests.Response.
    `extract(response)` pulls the result out of a 200 (defaults to the
    transcription APIs' {"text": ...} shape). Keys that fail with a retryable
    error are added to the shared `dead` set so the rest of a batch skips them.
    Returns (result_text, key_label)."""
    if not keys:
        raise RuntimeError(f"No {engine} API key configured.")
    extract = extract or (lambda r: r.json().get("text", ""))

    # Skip keys already known-dead this batch; if all are dead, try them anyway.
    order = [(i, k) for i, k in enumerate(keys, 1) if k not in dead] or list(enumerate(keys, 1))
    errors = []
    for i, key in order:
        label = f"key {i}/{len(keys)} ({_mask(key)})"
        try:
            resp = make_request(key)
        except requests.RequestException as exc:
            errors.append(f"{label}: network error: {exc}")
            dead.add(key)
            continue

        if resp.status_code == 200:
            return extract(resp), label

        msg = _format_api_error(engine, resp)
        if resp.status_code in RETRYABLE_STATUSES:
            errors.append(f"{label}: {msg}")
            dead.add(key)
            continue
        # Non-retryable (e.g. 400 bad audio): another key won't help.
        raise RuntimeError(f"{engine} request failed — {msg}\n(tried {label})")

    raise RuntimeError(
        f"All {len(keys)} {engine} key(s) failed:\n" + "\n".join("• " + e for e in errors)
    )


def transcribe_openai_compatible(raw, filename, ext, keys, model, lang, prompt, dead,
                                 url=OPENAI_URL, engine="OpenAI"):
    """Transcribe via any OpenAI-compatible /audio/transcriptions endpoint.
    OpenAI itself and Groq's Whisper share the exact same request shape
    (Bearer auth + multipart: file, model, response_format, language, prompt)."""
    mime = MIME_BY_EXT.get(ext, "application/octet-stream")
    data = {"model": model, "response_format": "json"}
    if lang:
        data["language"] = lang
    if prompt:
        data["prompt"] = prompt

    def make_request(key):
        return requests.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (filename, io.BytesIO(raw), mime)},
            data=data,
            timeout=300,
        )

    return _run_with_failover(make_request, keys, engine, dead)


def transcribe_elevenlabs(raw, filename, ext, keys, lang, dead):
    mime = MIME_BY_EXT.get(ext, "application/octet-stream")
    data = {"model_id": "scribe_v2", "tag_audio_events": "false"}
    if lang:
        data["language_code"] = lang

    def make_request(key):
        return requests.post(
            ELEVENLABS_URL,
            headers={"xi-api-key": key},
            files={"file": (filename, io.BytesIO(raw), mime)},
            data=data,
            timeout=300,
        )

    return _run_with_failover(make_request, keys, "ElevenLabs", dead)


def romanize_offline(text: str) -> str:
    """Transliterate to Latin script with no API call (unidecode). Great for
    Devanagari output; weaker on Arabic script (which drops short vowels)."""
    from unidecode import unidecode

    for src, dst in DANDA_MAP.items():
        text = text.replace(src, dst)
    return re.sub(r"[ \t]+", " ", unidecode(text)).strip()


def romanize_text(text, keys, dead, model=ROMANIZE_MODEL):
    """Transliterate a transcript into Latin (Roman) script via OpenAI chat,
    reusing the same multi-key failover. English stays English; Urdu -> Roman
    Urdu; Pashto -> Roman Pashto. Returns the converted text."""
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": ROMANIZE_SYSTEM},
            {"role": "user", "content": text},
        ],
    }

    def make_request(key):
        return requests.post(
            OPENAI_CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )

    result, _ = _run_with_failover(
        make_request, keys, "OpenAI (romanize)", dead,
        extract=lambda r: r.json()["choices"][0]["message"]["content"].strip(),
    )
    return result


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
engine = st.sidebar.radio(
    "Transcription engine", ["OpenAI", "ElevenLabs Scribe", "Groq Whisper"])

_FAILOVER_HELP = ("Optional fallback keys, added after any in secrets.toml. "
                  "Used for automatic failover.")

if engine == "OpenAI":
    model = st.sidebar.selectbox("OpenAI model", OPENAI_MODELS)
    extra = st.sidebar.text_area(
        "Extra OpenAI key(s) — one per line", height=70, help=_FAILOVER_HELP)
    keys = get_keys("OPENAI_API_KEYS", "OPENAI_API_KEY", extra)
elif engine == "Groq Whisper":
    model = st.sidebar.selectbox("Groq model", GROQ_MODELS)
    extra = st.sidebar.text_area(
        "Extra Groq key(s) — one per line", height=70, help=_FAILOVER_HELP)
    keys = get_keys("GROQ_API_KEYS", "GROQ_API_KEY", extra)
else:
    model = None
    extra = st.sidebar.text_area(
        "Extra ElevenLabs key(s) — one per line", height=70, help=_FAILOVER_HELP)
    keys = get_keys("ELEVENLABS_API_KEYS", "ELEVENLABS_API_KEY", extra)

if len(keys) > 1:
    st.sidebar.success(f"🔑 {len(keys)} keys loaded — failover enabled.")
elif len(keys) == 1:
    st.sidebar.info("🔑 1 key loaded. Add more (secrets or above) for failover.")
else:
    st.sidebar.error("No API key found. Add keys to secrets.toml or the box above.")

# OpenAI keys power the optional romanization step even when transcribing with
# ElevenLabs (ElevenLabs' own extra-keys box must not feed the OpenAI list).
openai_keys = get_keys(
    "OPENAI_API_KEYS", "OPENAI_API_KEY", extra if engine == "OpenAI" else "")

romanize = st.sidebar.checkbox(
    "Romanize output (Roman Urdu / Pashto + English)", value=True,
    help="Transliterates the transcript into Latin script so Urdu and Pashto come "
         "out in Roman form instead of Arabic/Devanagari.",
)
romanize_method = "Offline (free)"
if romanize:
    romanize_method = st.sidebar.radio(
        "Romanize method",
        ["Offline (free)", "OpenAI (higher quality)"],
        help="Offline works instantly with no API or credits. OpenAI produces more "
             "natural Roman spelling but needs OpenAI credits (falls back to offline).",
    )
    if romanize_method.startswith("OpenAI") and not openai_keys:
        st.sidebar.caption("⚠️ No OpenAI key found — will use offline transliteration.")

language_label = st.sidebar.selectbox("Language", list(LANGUAGES.keys()))
lang_codes = LANGUAGES[language_label]

st.sidebar.info(
    "**Keep Auto-detect for mixed-language voice notes.** When Urdu, Pashto and "
    "English are code-switched in one recording, forcing a single language can "
    "push the whole transcript into the wrong script."
)

st.title("🎙️ Voice Note Transcriber")
st.caption("English · Urdu · Pashto — including code-switched (mixed) speech.")

if engine in ("OpenAI", "Groq Whisper"):
    prompt = st.text_area(
        "Context prompt (OpenAI & Groq)", value=DEFAULT_PROMPT, height=90,
        help="Steers spelling, names and mixed-language handling. Ignored by ElevenLabs.",
    )
else:
    prompt = None
    st.caption("ℹ️ The context prompt is used only by the OpenAI and Groq engines.")

uploaded = st.file_uploader(
    "Upload voice notes",
    type=UPLOAD_TYPES,
    accept_multiple_files=True,
    help="WhatsApp .opus / .m4a supported. .opus and .amr are auto-converted to mp3.",
)

st.markdown("**🎤 …or record straight from your microphone**")
recorded = st.audio_input("Press to record, press again to stop — then hit Transcribe.")

# Combine uploaded files and the mic recording into one list of (name, bytes).
sources = [(uf.name, uf.getvalue()) for uf in (uploaded or [])]
if recorded is not None:
    sources.append(("mic-recording.wav", recorded.getvalue()))

if not keys:
    st.warning(
        "No API key found. Add one or more keys in `.streamlit/secrets.toml` "
        "(or the sidebar) to enable transcription."
    )

transcribe_clicked = st.button(
    "Transcribe", type="primary", disabled=not (sources and keys)
)

# --- Run transcription, store results in session_state ------------------------
if transcribe_clicked and sources and keys:
    # Clear any stale edited-transcript widget state from a previous run.
    for k in [k for k in st.session_state if k.startswith("txt_")]:
        del st.session_state[k]

    dead = set()  # keys that failed (retryably) — skipped for the rest of this batch
    results = []
    progress = st.progress(0.0, text="Starting…")
    for idx, (name, raw) in enumerate(sources):
        progress.progress(idx / len(sources), text=f"Transcribing {name}…")
        ext = get_ext(name)
        send_bytes, send_ext, note = maybe_convert_to_mp3(raw, ext)
        send_name = Path(name).with_suffix("." + send_ext).name

        entry = {"name": name, "audio": raw, "mime": MIME_BY_EXT.get(ext),
                 "note": note, "text": "", "used": None, "error": None,
                 "romanized": False, "romanize_error": None}
        try:
            if engine == "OpenAI":
                entry["text"], entry["used"] = transcribe_openai_compatible(
                    send_bytes, send_name, send_ext, keys, model,
                    lang_codes["openai"], prompt, dead, OPENAI_URL, "OpenAI")
            elif engine == "Groq Whisper":
                entry["text"], entry["used"] = transcribe_openai_compatible(
                    send_bytes, send_name, send_ext, keys, model,
                    lang_codes["openai"], prompt, dead, GROQ_URL, "Groq")
            else:
                entry["text"], entry["used"] = transcribe_elevenlabs(
                    send_bytes, send_name, send_ext, keys, lang_codes["elevenlabs"], dead)
        except Exception as exc:
            entry["error"] = str(exc)

        # Optional: transliterate the transcript into Roman/Latin script.
        if romanize and entry["error"] is None and entry["text"].strip():
            progress.progress(idx / len(sources), text=f"Romanizing {name}…")
            use_openai = romanize_method.startswith("OpenAI") and openai_keys
            if use_openai:
                try:
                    entry["text"] = romanize_text(entry["text"], openai_keys, dead)
                    entry["romanized"] = "OpenAI"
                except Exception as exc:
                    entry["text"] = romanize_offline(entry["text"])
                    entry["romanized"] = "offline"
                    entry["romanize_error"] = f"OpenAI romanize failed, used offline ({exc})"
            else:
                entry["text"] = romanize_offline(entry["text"])
                entry["romanized"] = "offline"
        results.append(entry)

    progress.progress(1.0, text="Done.")
    st.session_state["results"] = results
    st.session_state["engine_used"] = engine

# --- Render results -----------------------------------------------------------
results = st.session_state.get("results", [])
if results:
    engine_used = st.session_state.get("engine_used", "")
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

        if item.get("used"):
            st.caption(f"✅ Transcribed with {engine_used} · {item['used']}")
        if item.get("romanized"):
            st.caption(f"🔤 Romanized to Latin script · {item['romanized']} method.")
        if item.get("romanize_error"):
            st.caption(f"⚠️ {item['romanize_error']}")
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
