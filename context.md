# Project Context — Voice Note Transcriber

A Streamlit app that turns voice notes into downloadable text transcripts,
built for **English + Urdu + Pashto**, including **code-switched** (mixed)
speech. This file is the orientation doc for the codebase: what it does, how it
is wired, the decisions behind it, and the sharp edges to watch for.

> ⚠️ **No real API keys anywhere in this file or the repo.** All keys live in
> `.streamlit/secrets.toml` (gitignored) locally, or the Streamlit Cloud Secrets
> dashboard. Examples below use placeholders only.

---

## 1. What it does

- Upload one or many voice notes, **or record from the mic** in the browser.
- Transcribe with a choice of engine, with the transcript shown in an editable
  box you can correct.
- Optionally **romanize** the output to Latin script (Roman Urdu / Roman Pashto,
  English left as-is).
- Download each transcript as `.txt`, or all of them as a single `.zip`.

## 2. Files

```
voice-transcriber/
├── app.py                  # the entire Streamlit app (single file)
├── requirements.txt        # streamlit, requests, pydub, unidecode
├── packages.txt            # ffmpeg  (apt package installed by Streamlit Cloud)
├── .streamlit/
│   └── secrets.toml         # API keys — GITIGNORED, never committed
├── .gitignore              # ignores secrets.toml, venv/, __pycache__, *.pyc
├── README.md               # user-facing run & deploy steps
└── context.md              # this file
```

- **GitHub:** `AayanSethi0503/voice-transcriber` (public), default branch `main`.
- **Local dev:** run inside a `venv/`. App served on **port 8502**
  (8501 was already taken on the dev machine).

## 3. Engines & API details

| Engine (UI label) | Endpoint | Auth header | Models | Notes |
|---|---|---|---|---|
| **OpenAI** | `POST /v1/audio/transcriptions` | `Authorization: Bearer <key>` | gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1 | multipart: file, model, response_format=json, [language], [prompt] |
| **ElevenLabs Scribe** | `POST /v1/speech-to-text` | `xi-api-key: <key>` | scribe_v2 | multipart: file, model_id, tag_audio_events=false, [language_code] |
| **Groq Whisper** | `POST https://api.groq.com/openai/v1/audio/transcriptions` | `Authorization: Bearer <key>` | whisper-large-v3-turbo, whisper-large-v3 | OpenAI-compatible — reuses the OpenAI code path |

All read the transcript from response JSON `["text"]`.

**Language codes** (from the `LANGUAGES` map): the UI offers Auto-detect / English
/ Urdu / Pashto.
- OpenAI **and Groq** use ISO 639-1: `en` / `ur` / `ps` (both share `lang_codes["openai"]`).
- ElevenLabs uses ISO 639-3: `eng` / `urd` / `pus`.
- **Auto-detect (None)** is the default and is best for code-switched audio —
  forcing one language can push the whole transcript into the wrong script.

**Key naming** (Groq = the letter **q**; not xAI's "Grok"). Groq hosts Whisper on
an OpenAI-compatible endpoint; keys start with `gsk_` (free tier at
console.groq.com). xAI Grok has no Whisper/STT API.

## 4. Multi-key failover

Each engine takes a **list** of keys and rotates through them:
- `get_keys(PLURAL, SINGULAR, sidebar)` gathers keys from
  `*_API_KEYS` (TOML array) → `*_API_KEY` (single) → sidebar box, then strips
  blanks/placeholders and de-dupes.
- `_run_with_failover(make_request, keys, engine, dead, extract=…)` tries keys in
  order. It **rotates to the next key** on statuses in `RETRYABLE_STATUSES`
  (`401, 403, 408, 429, 500, 502, 503, 504`) or network errors, and **fails fast**
  on any other 4xx (e.g. 400 bad audio) so a request problem doesn't burn every
  key. A shared `dead` set skips keys that already failed during the same batch.
- The upload body is rebuilt per attempt (a `requests` file object is single-use).
- `extract` lets the same helper read both the transcription shape
  (`json()["text"]`) and the chat shape (`json()["choices"][0]["message"]["content"]`).
- The UI shows which key produced each transcript, e.g. `key 2/3 (…2222)`.

## 5. Romanization (Latin-script output)

Optional post-step (default ON). Two methods, chosen in the sidebar:
- **Offline (free)** — `romanize_offline()` via `unidecode`. No API, instant.
  Great on **Devanagari**; **weak on Arabic script** (Arabic omits short vowels,
  so it yields consonant-heavy output like "myr nm" for میرا نام).
- **OpenAI (higher quality)** — `romanize_text()` calls OpenAI chat
  (`gpt-4o-mini`, `ROMANIZE_SYSTEM` prompt, temperature 0). More natural spelling,
  but needs OpenAI credits. **Auto-falls-back to offline** if the call fails.

Romanization reuses the OpenAI keys (via `openai_keys`) even when transcribing
with ElevenLabs/Groq. `DANDA_MAP` converts Devanagari/Urdu sentence terminators
(।, ॥, ۔) to periods so they don't become stray " / " marks.

> Interaction to remember: **ElevenLabs → Devanagari → Offline romanize = good.**
> **Whisper (OpenAI/Groq) → Arabic script → Offline romanize = poor** (use the
> OpenAI romanize method, which needs credits, for those).

## 6. Audio input & conversion

- **Upload:** `st.file_uploader` (multiple) accepting
  `mp3, wav, m4a, ogg, opus, flac, webm, mp4, aac, amr`.
- **Mic:** `st.audio_input` (Streamlit ≥1.40) records a WAV clip named
  `mic-recording.wav`. Uploads + the recording are merged into one `sources`
  list of `(name, bytes)` and processed identically.
- **Conversion:** `maybe_convert_to_mp3()` transcodes `opus`/`amr`
  (`CONVERT_EXTS`) to mp3 via pydub+ffmpeg. On failure (e.g. ffmpeg missing) it
  falls back to sending the original bytes. **Gotcha:** ffmpeg has no demuxer
  named `opus` — WhatsApp `.opus` is Ogg-encapsulated, so `DECODE_FORMAT` maps
  `opus → "ogg"`, with an auto-detect fallback.

## 7. Configuration / secrets

`.streamlit/secrets.toml` (gitignored). Arrays enable failover; singular keys
still work for backward-compat:

```toml
OPENAI_API_KEYS     = ["sk-proj-...", "sk-proj-..."]
ELEVENLABS_API_KEYS = ["sk_...", "sk_..."]
GROQ_API_KEYS       = ["gsk_..."]
```

- **Priority:** `st.secrets` → sidebar "Extra key(s)" box (fallback).
- **Streamlit Cloud:** do NOT commit `secrets.toml`. Paste the same TOML into
  **Manage app → Settings → Secrets** in the dashboard.
- Placeholder markers (`REPLACE_WITH`, `PASTE_YOUR`, `YOUR_KEY_HERE`) are treated
  as "unset" so they never shadow a real key.

## 8. Run locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# ffmpeg required for .opus/.amr:
#   Debian/Ubuntu: sudo apt install ffmpeg   |   conda: conda install -c conda-forge ffmpeg
# add keys to .streamlit/secrets.toml (or the sidebar)
streamlit run app.py            # dev machine uses --server.port 8502
```

On the dev machine ffmpeg was installed via **conda-forge** (no sudo available);
it lands on PATH so pydub finds it.

## 9. Deploy to Streamlit Community Cloud

1. Push to GitHub (`secrets.toml` stays out via `.gitignore`).
2. share.streamlit.io → New app → repo `AayanSethi0503/voice-transcriber`,
   branch `main`, main file `app.py`.
3. **Advanced settings → Secrets:** paste the key arrays (see §7).
4. `requirements.txt` + `packages.txt` (ffmpeg) build automatically. Deploy.

## 10. Key decisions & gotchas (history)

- **`.opus` demuxer** → must decode as `ogg`, not `opus` (fixed early).
- **secrets.toml stays gitignored** — repo is public; committing keys would
  expose them (auto-revoked/scraped). Gitignore does NOT block local use;
  Streamlit reads the file from disk regardless. Cloud uses the dashboard, not
  the repo file.
- **Grok vs Groq** — the Whisper host is Groq (`gsk_`); xAI's Grok is unrelated.
- **Offline romanize** is script-dependent (see §5).
- **Failover** distinguishes retryable (key) vs fatal (request) errors so it
  doesn't waste keys on a bad file.

## 11. Known limitations

- Offline romanization is mechanical and weak on Arabic-script text.
- Recognition quality for Pashto is engine-dependent and imperfect.
- Mic capture needs `localhost` or HTTPS (works locally and on Cloud).
- No persistence — transcripts live in `st.session_state` for the session only.

## 12. Possible next steps

- Point the **romanize** step at a Groq-hosted LLM so high-quality Roman output
  works on the free Groq tier (no OpenAI credits needed).
- Prompt-based script steering for Whisper engines.
- Optional translation (vs. transliteration) mode.
