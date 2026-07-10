# 🎙️ Voice Note Transcriber

A small Streamlit app that turns voice notes into downloadable text transcripts.
Built for **English, Urdu, and Pashto** — including **code-switched** recordings
where the languages are mixed inside a single voice note.

- Upload one or many voice notes (including WhatsApp **.opus** / **.m4a**).
- Choose an engine: **OpenAI** or **ElevenLabs Scribe**.
- Get an editable transcript per file, download each as **.txt**, or grab them
  all as a **.zip**.

## Supported formats

`mp3, wav, m4a, ogg, opus, flac, webm, mp4, aac, amr`

`.opus` (WhatsApp) and `.amr` are automatically transcoded to mp3 with
`pydub` + `ffmpeg` before upload. If `ffmpeg` is missing, the app falls back to
sending the original file.

## Language handling

Keep **Auto-detect** (the default) for mixed-language voice notes. When Urdu,
Pashto, and English are code-switched in one recording, forcing a single
language can push the whole transcript into the wrong script. Pick a specific
language only when you know the whole clip is in that one language.

---

## Run locally

Requires **Python 3.9+** and **ffmpeg**.

```bash
# 1. From inside the voice-transcriber folder, create + activate a venv
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install ffmpeg (needed to convert .opus / .amr)
#    macOS:          brew install ffmpeg
#    Debian/Ubuntu:  sudo apt install ffmpeg
#    conda:          conda install -c conda-forge ffmpeg

# 4. Add your API keys — edit .streamlit/secrets.toml and paste your real keys:
#    OPENAI_API_KEY = "sk-proj-..."
#    ELEVENLABS_API_KEY = "..."
#    (You can also paste a key into the sidebar at runtime instead.)

# 5. Run
streamlit run app.py
```

Then open the local URL Streamlit prints (usually http://localhost:8501).

### API keys

The app reads keys in this order:

1. `st.secrets` — from `.streamlit/secrets.toml` (local) or the Cloud dashboard.
2. The **password field in the sidebar** (fallback), if no secret is set.

`.streamlit/secrets.toml` is **gitignored** so your keys are never committed.

---

## Deploy to Streamlit Community Cloud

1. Push this folder to a **GitHub repo** (see below). `secrets.toml` stays out of
   the repo thanks to `.gitignore`.
2. Go to **https://share.streamlit.io** and sign in with GitHub.
3. **Create app → From existing repo**, and select your repo/branch and
   `app.py` as the entry point.
4. Open **Advanced settings → Secrets** and paste:

   ```toml
   OPENAI_API_KEY = "sk-proj-..."
   ELEVENLABS_API_KEY = "..."
   ```

5. `packages.txt` (which contains `ffmpeg`) and `requirements.txt` are picked up
   automatically to build the environment.
6. Click **Deploy**.

---

## Project layout

```
voice-transcriber/
├── app.py                 # the Streamlit app
├── requirements.txt       # streamlit, requests, pydub
├── packages.txt           # ffmpeg (apt package for Streamlit Cloud)
├── .streamlit/
│   └── secrets.toml        # your API keys — gitignored, never committed
├── .gitignore
└── README.md
```
