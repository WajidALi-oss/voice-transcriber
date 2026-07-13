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

from styles import load_styles, copy_button

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


def resolve_display_text(uid, native, want_roman, method, oa_keys):
    """Pick what to show for result `uid` from the CURRENT romanize toggle, so
    flipping it switches instantly with no re-transcription.

    The native transcript is always kept. Offline romanization is computed on
    the fly (instant, free); OpenAI romanization is computed once and cached in
    session_state (with offline fallback). Returns (text, note_or_None)."""
    if not want_roman or not native.strip():
        return native, None
    if method.startswith("OpenAI") and oa_keys:
        cache = f"roman_openai_{uid}"
        if cache not in st.session_state:
            try:
                st.session_state[cache] = (romanize_text(native, oa_keys, set()), "OpenAI method")
            except Exception as exc:
                st.session_state[cache] = (romanize_offline(native), f"offline (OpenAI failed: {exc})")
        return st.session_state[cache]
    return romanize_offline(native), "offline method"


def unique_name(name: str, used: set) -> str:
    """Return `name`, or `name (2)`, `name (3)`… if it's already been handed out.
    Prevents two sources with the same stem (e.g. an uploaded `note.mp3` and a
    `note.wav`, or an upload named like the mic clip) from silently overwriting
    each other in the per-file download and the combined .zip."""
    if name not in used:
        used.add(name)
        return name
    stem, _, ext = name.rpartition(".")
    n = 2
    while f"{stem} ({n}).{ext}" in used:
        n += 1
    out = f"{stem} ({n}).{ext}"
    used.add(out)
    return out


def build_zip(items) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in items:
            zf.writestr(name, text)
    return buf.getvalue()


# -----------------------------------------------------------------------------
# UI
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Voice Note Transcriber",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"about": "Voice Note Transcriber — English · Urdu · Pashto speech-to-text."},
)

load_styles()


with st.sidebar:
    st.markdown('<div class="side-head" style="font-size:.95rem;color:var(--vt-ink)">'
                '⚙️&nbsp; Settings</div>', unsafe_allow_html=True)

    st.markdown('<div class="side-head">🎛️ Transcription engine</div>', unsafe_allow_html=True)
    engine = st.radio(
        "Transcription engine", ["OpenAI", "ElevenLabs Scribe", "Groq Whisper"],
        label_visibility="collapsed")

    _FAILOVER_HELP = ("Optional fallback keys, added after any in secrets.toml. "
                      "Used for automatic failover.")
    if engine == "OpenAI":
        model = st.selectbox("OpenAI model", OPENAI_MODELS)
        extra = st.text_area("Extra OpenAI key(s) — one per line", height=70, help=_FAILOVER_HELP)
        keys = get_keys("OPENAI_API_KEYS", "OPENAI_API_KEY", extra)
    elif engine == "Groq Whisper":
        model = st.selectbox("Groq model", GROQ_MODELS)
        extra = st.text_area("Extra Groq key(s) — one per line", height=70, help=_FAILOVER_HELP)
        keys = get_keys("GROQ_API_KEYS", "GROQ_API_KEY", extra)
    else:
        model = None
        extra = st.text_area("Extra ElevenLabs key(s) — one per line", height=70, help=_FAILOVER_HELP)
        keys = get_keys("ELEVENLABS_API_KEYS", "ELEVENLABS_API_KEY", extra)

    if len(keys) > 1:
        st.success(f"🔑 {len(keys)} keys loaded — failover on.")
    elif len(keys) == 1:
        st.info("🔑 1 key loaded. Add more for failover.")
    else:
        st.error("No API key found. Add keys to secrets.toml or above.")

    # OpenAI keys power the optional romanization step even when transcribing with
    # ElevenLabs/Groq (their own extra-keys box must not feed the OpenAI list).
    openai_keys = get_keys(
        "OPENAI_API_KEYS", "OPENAI_API_KEY", extra if engine == "OpenAI" else "")

    st.divider()
    st.markdown('<div class="side-head">🌐 Output</div>', unsafe_allow_html=True)
    language_label = st.selectbox("Language", list(LANGUAGES.keys()))
    lang_codes = LANGUAGES[language_label]

    romanize = st.checkbox(
        "Romanize output (Roman Urdu / Pashto)", value=True,
        help="Transliterates Urdu/Pashto into Latin script. Toggle any time to "
             "switch a transcript between Roman and original script.",
    )
    romanize_method = "Offline (free)"
    if romanize:
        romanize_method = st.radio(
            "Romanize method", ["Offline (free)", "OpenAI (higher quality)"],
            help="Offline is instant & free. OpenAI is more natural but needs credits "
                 "(auto-falls back to offline).",
        )
        if romanize_method.startswith("OpenAI") and not openai_keys:
            st.caption("⚠️ No OpenAI key — will use offline transliteration.")

    st.divider()
    st.caption("💡 Keep **Auto-detect** for mixed-language notes — forcing one "
               "language can push everything into the wrong script.")


# --- Everything below is centered in a constrained middle column ---------------
_, body, _ = st.columns([1, 2.4, 1])

with body:
    st.markdown(
        """
        <div class="hero">
          <div class="badge">🔊 Speech → Text</div>
          <div class="emoji">🎙️</div>
          <h1>Voice Note Transcriber</h1>
          <p>English · Urdu · Pashto — accurate even when they're mixed together in a single recording.</p>
          <div class="chips">
            <span>⚡ 3 engines</span><span>🔁 Auto key-failover</span>
            <span>🔤 Romanize</span><span>🎤 Record or upload</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown('<div class="step-label"><span class="num">1</span> Add your audio</div>',
                    unsafe_allow_html=True)

        # Record and Upload live in separate tabs so only one is visible at a
        # time. Both widgets still render (tabs don't unmount), so a recording
        # AND uploads can both be active — handled explicitly further down.
        tab_rec, tab_up = st.tabs(["🎙️ Record", "📁 Upload file"])
        with tab_rec:
            recorded = st.audio_input("Record from microphone", label_visibility="collapsed")
            st.markdown('<div class="mic-hero-cap">🎙️ Tap the mic to start recording</div>',
                        unsafe_allow_html=True)
        with tab_up:
            uploaded = st.file_uploader(
                "Upload voice notes", type=UPLOAD_TYPES, accept_multiple_files=True,
                help="WhatsApp .opus / .m4a supported. .opus and .amr are auto-converted to mp3.",
                label_visibility="collapsed",
            )

        if engine in ("OpenAI", "Groq Whisper"):
            with st.expander("✏️ Context prompt (optional — guides spelling, names, mixed language)"):
                prompt = st.text_area(
                    "Context prompt", value=DEFAULT_PROMPT, height=90,
                    label_visibility="collapsed",
                    help="Steers spelling, names and mixed-language handling. Ignored by ElevenLabs.",
                )
        else:
            prompt = None

    # Combine uploaded files and the mic recording into one list of (name, bytes).
    # Both inputs can be active at once (the mic clip persists across reruns), so
    # we process them together rather than silently picking one — but tell the user.
    n_uploaded = len(uploaded or [])
    sources = [(uf.name, uf.getvalue()) for uf in (uploaded or [])]
    if recorded is not None:
        sources.append(("mic-recording.wav", recorded.getvalue()))

    if not keys:
        st.warning("🔑 No API key found — add keys in the sidebar or `.streamlit/secrets.toml`.")
    elif not sources:
        st.info("⬆️ Record a clip or upload a file above, then press **Transcribe**.")
    elif n_uploaded and recorded is not None:
        files_word = "file" if n_uploaded == 1 else "files"
        st.info(
            f"🎤 Both an upload ({n_uploaded} {files_word}) **and** a mic recording are "
            f"ready — all **{len(sources)}** will be transcribed together. Remove the mic "
            "clip (✕ on the recorder) to transcribe only the upload.")

    btn_label = (f"🎧 Transcribe {len(sources)} clip{'s' if len(sources) != 1 else ''}"
                 if sources else "🎧 Transcribe")
    transcribe_clicked = st.button(
        btn_label, type="primary", use_container_width=True,
        disabled=not (sources and keys),
    )

    # --- Run transcription, store the NATIVE transcript in session_state --------
    # Romanization is applied at DISPLAY time (below) so the toggle can switch
    # between romanized and native instantly, without re-hitting the API.
    if transcribe_clicked and sources and keys:
        # Clear stale per-result widget + romanize-cache state from a previous run.
        for k in [k for k in st.session_state
                  if k.startswith("txt_") or k.startswith("roman_openai_")]:
            del st.session_state[k]

        dead = set()  # keys that failed (retryably) — skipped for the rest of the batch
        results = []
        with st.status("Transcribing your audio…", expanded=True) as status:
            for idx, (name, raw) in enumerate(sources):
                st.write(f"⬆️ **{name}** — uploading & transcribing "
                         f"({idx + 1}/{len(sources)})")
                ext = get_ext(name)
                send_bytes, send_ext, note = maybe_convert_to_mp3(raw, ext)
                send_name = Path(name).with_suffix("." + send_ext).name

                entry = {"name": name, "audio": raw, "mime": MIME_BY_EXT.get(ext),
                         "note": note, "text": "", "used": None, "error": None, "model": model}
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
                results.append(entry)

            ok = sum(1 for it in results if not it.get("error"))
            st.write("✨ Formatting transcripts…")
            status.update(label=f"✅ Transcribed {ok}/{len(results)} file(s)",
                          state="complete", expanded=False)

        st.session_state["results"] = results
        st.session_state["engine_used"] = engine
        # New run id -> fresh widget keys below, so the text boxes always re-seed
        # with the new transcript instead of showing the previous run's text.
        st.session_state["run_id"] = st.session_state.get("run_id", 0) + 1
        st.toast("Transcription ready ✨")

    # --- Render results (romanized vs native chosen live from the toggle) -------
    results = st.session_state.get("results", [])
    if results:
        engine_used = st.session_state.get("engine_used", "")
        run_id = st.session_state.get("run_id", 0)
        # A tag for the widget key so toggling romanize re-seeds the text box.
        mode_tag = ("off" if not romanize
                    else "oa" if romanize_method.startswith("OpenAI") else "roman")
        ok_count = sum(1 for it in results if not it.get("error"))
        st.markdown(
            f'<div class="step-label"><span class="num">2</span> Your transcripts'
            f'<span class="count">{ok_count}/{len(results)} done</span></div>',
            unsafe_allow_html=True)
        zip_items = []
        used_names = set()  # keeps output .txt names unique across all results
        for i, item in enumerate(results):
            with st.container(border=True):
                st.markdown(
                    f'<div class="file-title"><span class="ic">📄</span>{item["name"]}</div>',
                    unsafe_allow_html=True)
                if item.get("audio") is not None:
                    st.audio(item["audio"], format=item.get("mime") or "audio/mpeg")
                if item.get("note"):
                    st.caption(item["note"])
                if item.get("error"):
                    st.error(item["error"])
                    continue

                display, roman_note = resolve_display_text(
                    f"{run_id}_{i}", item["text"], romanize, romanize_method, openai_keys)

                # Info pills: engine/model/key used and the script mode.
                pills = []
                if item.get("used"):
                    model_str = f" · {item['model']}" if item.get("model") else ""
                    pills.append(f'<span class="pill ok">✅ {engine_used}{model_str} · {item["used"]}</span>')
                pills.append(
                    f'<span class="pill info">🔤 Romanized · {roman_note}</span>' if roman_note
                    else '<span class="pill info">🔡 Native script</span>')
                st.markdown(f'<div class="meta-row">{"".join(pills)}</div>', unsafe_allow_html=True)

                base = unique_name(Path(item["name"]).stem + ".txt", used_names)

                # Header row: TRANSCRIPTION label (left) + Copy / Download (right).
                h_label, h_copy, h_dl = st.columns([1.5, 1, 1])
                with h_label:
                    st.markdown('<div class="result-head">📝 Transcription</div>',
                                unsafe_allow_html=True)
                with h_copy:
                    copy_button(display, key=f"{run_id}_{i}")
                # run_id + mode_tag in the key so a new transcription (or a romanize
                # toggle) always re-seeds the box with the right text.
                edited = st.text_area(
                    "Transcript", value=display, height=170,
                    key=f"txt_{run_id}_{i}_{mode_tag}", label_visibility="collapsed")
                with h_dl:
                    st.download_button(
                        "⬇️ .txt", data=edited.encode("utf-8"),
                        file_name=base, mime="text/plain", key=f"dl_{run_id}_{i}",
                        use_container_width=True)

                st.markdown(
                    '<div class="result-meta"><span class="done">✓ Done</span>'
                    f'<span class="dot">·</span>{len(display.split())} words'
                    f'<span class="dot">·</span>{len(display)} chars</div>',
                    unsafe_allow_html=True)
                zip_items.append((base, edited))

        if len(zip_items) > 1:
            st.download_button(
                "⬇️ Download all transcripts (.zip)", data=build_zip(zip_items),
                file_name="transcripts.zip", mime="application/zip",
                key="dl_zip", type="primary", use_container_width=True)

    st.markdown(
        '<div class="vt-footer">🎙️ <b>Voice Note Transcriber</b> · '
        'English · Urdu · Pashto · code-switched speech<br>'
        'Transcripts stay in your session only — nothing is stored.</div>',
        unsafe_allow_html=True)
