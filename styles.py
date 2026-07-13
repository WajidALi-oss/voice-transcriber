"""Injected CSS for the Voice Note Transcriber UI.

Everything visual lives here so `app.py` stays focused on behaviour. Call
`load_styles()` exactly once, right after `st.set_page_config(...)`.

Design system: dark canvas, a single violet accent (#7C4DFF, the repo brand),
Inter for text and JetBrains Mono for metadata. Pure Streamlit + injected CSS —
no third-party components, so it runs unchanged on Streamlit Community Cloud.
"""

import streamlit as st

# One accent color, used everywhere. Kept as constants so app.py can reuse them
# (e.g. the components.html copy button, which lives in its own iframe).
ACCENT = "#7C4DFF"
ACCENT_2 = "#5B3FD6"
SURFACE = "#1A1A24"
INK = "#EDEDF2"
MUTED = "#9A9AB0"

_CSS = f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500&display=swap');

  :root {{
    --vt-accent:   {ACCENT};
    --vt-accent-2: {ACCENT_2};
    --vt-grad: linear-gradient(135deg, {ACCENT} 0%, {ACCENT_2} 100%);
    --vt-bg:       #0E0E14;
    --vt-surface:  {SURFACE};
    --vt-surface-2:#22222E;
    --vt-ink:      {INK};
    --vt-muted:    {MUTED};
    --vt-line:     rgba(255,255,255,.08);
    --vt-soft:     rgba(124,77,255,.12);
    --vt-success:  #3DD68C;
    --vt-radius:   16px;
  }}

  html, body, [class*="css"], .stMarkdown, .stApp {{
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }}

  /* ---------- Cleaner canvas: hide Streamlit chrome ---------- */
  /* Hide the menu, footer, deploy button and status widget — but NOT the whole
     toolbar: the collapsed-sidebar expand control lives inside it, so nuking the
     toolbar would trap users with no way to reopen the sidebar. */
  #MainMenu, footer {{ visibility: hidden; }}
  [data-testid="stStatusWidget"] {{ display: none; }}
  [data-testid="stAppDeployButton"], .stDeployButton {{ display: none; }}
  header[data-testid="stHeader"] {{ background: transparent; }}
  /* Keep the sidebar collapse/expand controls always reachable. */
  [data-testid="stExpandSidebarButton"],
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="stSidebarCollapseButton"] {{
    display: flex !important; visibility: visible !important; opacity: 1 !important;
  }}
  .block-container {{ padding-top: 2rem; padding-bottom: 3.5rem; }}

  /* ---------- Hero ---------- */
  .hero {{
    position: relative; overflow: hidden;
    background: var(--vt-grad);
    border-radius: 22px; padding: 2.4rem 1.6rem 2.1rem;
    text-align: center; color: #fff;
    box-shadow: 0 20px 50px -14px rgba(124,77,255,.6);
    margin-bottom: 1.5rem;
  }}
  .hero::before {{
    content: ""; position: absolute; inset: 0;
    background:
      radial-gradient(60% 90% at 15% 0%, rgba(255,255,255,.25), transparent 60%),
      radial-gradient(50% 80% at 100% 100%, rgba(0,0,0,.18), transparent 55%);
    pointer-events: none;
  }}
  .hero > * {{ position: relative; z-index: 1; }}
  .hero .badge {{
    display: inline-flex; align-items: center; gap: .4rem;
    background: rgba(255,255,255,.16); border: 1px solid rgba(255,255,255,.32);
    padding: .28rem .8rem; border-radius: 999px;
    font-size: .74rem; font-weight: 700; letter-spacing: .6px; text-transform: uppercase;
    margin-bottom: .85rem;
  }}
  .hero .emoji {{ font-size: 2.9rem; line-height: 1; filter: drop-shadow(0 4px 12px rgba(0,0,0,.3)); }}
  .hero h1 {{ margin: .45rem 0 .3rem; font-size: 2.3rem; font-weight: 800;
             color: #fff; letter-spacing: -.8px; line-height: 1.1; }}
  .hero p {{ margin: 0 auto; max-width: 30rem; font-size: 1.02rem; opacity: .95; color: #fff; line-height: 1.5; }}
  .chips {{ margin-top: 1.25rem; display: flex; flex-wrap: wrap; gap: .5rem; justify-content: center; }}
  .chips span {{
    background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.28);
    padding: .36rem .8rem; border-radius: 999px; font-size: .82rem; font-weight: 600;
  }}

  /* ---------- Section step headers ---------- */
  .step-label {{
    display: flex; align-items: center; gap: .6rem;
    font-weight: 700; font-size: 1.12rem; color: var(--vt-ink); margin: .1rem 0 .9rem;
  }}
  .step-label .num {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 1.7rem; height: 1.7rem; border-radius: 9px;
    background: var(--vt-grad); color: #fff; font-size: .95rem; font-weight: 800;
    box-shadow: 0 5px 14px -3px rgba(124,77,255,.6); flex: none;
  }}
  .step-label .count {{ margin-left: auto; font-size: .8rem; font-weight: 600; color: var(--vt-muted); }}

  /* ---------- Bordered containers (cards) ---------- */
  [data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: var(--vt-radius) !important;
    border: 1px solid var(--vt-line) !important;
    background: var(--vt-surface);
    box-shadow: 0 8px 30px -18px rgba(0,0,0,.8);
  }}

  /* ---------- Buttons: tactile, clear primary/secondary hierarchy ---------- */
  .stButton > button, .stDownloadButton > button {{
    border-radius: 12px; padding: .6rem 1.4rem; font-weight: 500;
    border: 1px solid var(--vt-line); background: var(--vt-surface-2); color: var(--vt-ink);
    transition: all .18s ease;
  }}
  .stButton > button:hover, .stDownloadButton > button:hover {{
    transform: translateY(-2px); box-shadow: 0 6px 20px rgba(124,77,255,.35);
    border-color: var(--vt-accent);
  }}
  .stButton > button[kind="primary"], [data-testid="stBaseButton-primary"] {{
    background: var(--vt-grad) !important; border: none !important; color: #fff !important;
    font-weight: 600 !important; box-shadow: 0 8px 22px -6px rgba(124,77,255,.55) !important;
  }}
  .stButton > button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover {{
    box-shadow: 0 12px 28px -6px rgba(124,77,255,.7) !important;
  }}

  /* ---------- File uploader as a real drop target ---------- */
  [data-testid="stFileUploaderDropzone"] {{
    border: 2px dashed rgba(124,77,255,.4) !important; border-radius: 16px !important;
    background: rgba(124,77,255,.05) !important; transition: all .2s ease;
  }}
  [data-testid="stFileUploaderDropzone"]:hover {{
    border-color: var(--vt-accent) !important; background: rgba(124,77,255,.1) !important;
  }}

  /* ---------- Mic zone (record hero) ---------- */
  .mic-hero-cap {{ text-align: center; color: var(--vt-muted); font-size: .86rem; margin-top: .5rem; }}
  [data-testid="stAudioInput"] {{
    border: 1px solid rgba(124,77,255,.3); border-radius: 16px;
    background: rgba(124,77,255,.06); padding: .35rem .5rem;
    transition: box-shadow .3s ease, border-color .2s ease;
  }}
  [data-testid="stAudioInput"]:hover {{ border-color: var(--vt-accent); }}
  @keyframes pulse {{
    0%   {{ box-shadow: 0 0 0 0 rgba(124,77,255,.5); }}
    70%  {{ box-shadow: 0 0 0 22px rgba(124,77,255,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(124,77,255,0); }}
  }}
  .recording-active {{ animation: pulse 1.6s infinite; border-radius: 50%; }}
  /* Draw the eye to the mic as the centerpiece; respects reduced-motion. */
  @media (prefers-reduced-motion: no-preference) {{
    [data-testid="stAudioInput"] {{ animation: pulse 2.6s infinite; }}
    [data-testid="stAudioInput"]:hover {{ animation: none; }}
  }}

  /* ---------- Tabs (input modes) ---------- */
  [data-baseweb="tab-list"] {{ gap: .4rem; background: transparent; border-bottom: 1px solid var(--vt-line); }}
  [data-baseweb="tab"] {{
    border-radius: 10px 10px 0 0; padding: .3rem .9rem; font-weight: 600; color: var(--vt-muted);
  }}
  [data-baseweb="tab"][aria-selected="true"] {{ color: var(--vt-ink); background: var(--vt-soft); }}
  [data-baseweb="tab-highlight"] {{ background: var(--vt-accent) !important; }}

  /* ---------- Text area (transcript) ---------- */
  .stTextArea textarea {{
    border-radius: 12px !important; font-size: .97rem; line-height: 1.6;
    background: var(--vt-bg) !important; border-color: var(--vt-line) !important; color: var(--vt-ink) !important;
  }}
  .stTextArea textarea:focus {{
    border-color: var(--vt-accent) !important; box-shadow: 0 0 0 3px rgba(124,77,255,.25) !important;
  }}

  /* ---------- Result card header + metadata ---------- */
  .result-head {{ display: flex; align-items: center; gap: .5rem;
    font-size: .74rem; font-weight: 700; letter-spacing: 1.2px; color: var(--vt-muted); text-transform: uppercase; }}
  .file-title {{ display: flex; align-items: center; gap: .5rem; font-weight: 700;
    font-size: 1.02rem; color: var(--vt-ink); margin-bottom: .5rem; word-break: break-all; }}
  .file-title .ic {{
    display: inline-flex; align-items: center; justify-content: center; flex: none;
    width: 1.9rem; height: 1.9rem; border-radius: 8px; background: var(--vt-soft); font-size: 1rem;
  }}
  .result-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: .55rem;
    margin-top: .3rem; font-size: .8rem; color: var(--vt-muted);
    font-family: 'JetBrains Mono', monospace; }}
  .result-meta .done {{ color: var(--vt-success); font-weight: 600; }}
  .result-meta .dot {{ opacity: .4; }}

  /* ---------- Result meta pills ---------- */
  .meta-row {{ display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; margin: .1rem 0 .55rem; }}
  .pill {{
    display: inline-flex; align-items: center; gap: .3rem;
    padding: .22rem .62rem; border-radius: 999px; font-size: .76rem; font-weight: 600;
    border: 1px solid transparent; white-space: nowrap;
  }}
  .pill.ok    {{ background: rgba(61,214,140,.14); color: #6EE7B0; border-color: rgba(61,214,140,.3); }}
  .pill.info  {{ background: var(--vt-soft); color: #BCA9FF; border-color: rgba(124,77,255,.3); }}
  .pill.count {{ margin-left: auto; background: transparent; color: var(--vt-muted);
                 font-family: 'JetBrains Mono', monospace; font-size: .74rem; }}

  /* ---------- Sidebar ---------- */
  section[data-testid="stSidebar"] {{ border-right: 1px solid var(--vt-line); }}
  section[data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
  .side-head {{
    display: flex; align-items: center; gap: .5rem;
    font-size: .78rem; font-weight: 700; letter-spacing: .8px; text-transform: uppercase;
    color: var(--vt-muted); margin: .3rem 0 .55rem;
  }}

  /* ---------- Progress ---------- */
  [data-testid="stProgress"] > div > div > div > div {{ background: var(--vt-grad) !important; }}

  /* ---------- Footer ---------- */
  .vt-footer {{ text-align: center; color: var(--vt-muted); font-size: .82rem;
               margin-top: 2.2rem; padding-top: 1.2rem; border-top: 1px solid var(--vt-line); }}
  .vt-footer b {{ color: #BCA9FF; }}
</style>
"""


def load_styles() -> None:
    """Inject the app's stylesheet. Call once, after set_page_config()."""
    st.markdown(_CSS, unsafe_allow_html=True)


def copy_button(text: str, key: str) -> None:
    """A small clipboard button rendered in a built-in components.html iframe.

    Streamlit has no native copy action, so this uses navigator.clipboard with a
    hidden-textarea + execCommand fallback (the reliable path inside the sandboxed
    component iframe). Copies the transcript text as transcribed; edits made in
    the text box are saved via Download, not this button.
    """
    import json
    import streamlit.components.v1 as components

    safe = json.dumps(text)
    components.html(
        f"""
        <button id="cp_{key}" style="
            width:100%; cursor:pointer; border-radius:12px; padding:.55rem 1rem;
            font-family:'Inter',sans-serif; font-weight:600; font-size:.9rem;
            color:{INK}; background:#22222E; border:1px solid rgba(255,255,255,.12);
            transition:all .18s ease;">📋 Copy</button>
        <script>
          const b = document.getElementById("cp_{key}");
          b.onmouseenter = () => {{ b.style.borderColor = "{ACCENT}"; }};
          b.onmouseleave = () => {{ b.style.borderColor = "rgba(255,255,255,.12)"; }};
          b.onclick = async () => {{
            const t = {safe};
            try {{
              await navigator.clipboard.writeText(t);
            }} catch (e) {{
              const ta = document.createElement("textarea");
              ta.value = t; ta.style.position = "fixed"; ta.style.opacity = "0";
              document.body.appendChild(ta); ta.select();
              try {{ document.execCommand("copy"); }} catch (e2) {{}}
              document.body.removeChild(ta);
            }}
            b.textContent = "✓ Copied"; b.style.color = "{ACCENT}";
            setTimeout(() => {{ b.textContent = "📋 Copy"; b.style.color = "{INK}"; }}, 1500);
          }};
        </script>
        """,
        height=48,
    )
