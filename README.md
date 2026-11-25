# boldline-explainer

**Code-driven 2D flat-vector explainer video generator & AI agent skill** (*"Vẽ tay viền đậm"* / Bold-Outline Cartoon style).

Turn any JSON script into a narrated **9:16 (TikTok/Reels/Shorts)** or **16:9 (YouTube)** animated explainer video with:
- Bold-outline 2D cartoon characters (customizable colors, hair, glasses, badges, expressions, blinking, lip-sync, arm poses)
- Multiple 2D backgrounds (`dark_card`, `office`, `spotlight`, `forest_hills`)
- Taped polaroid `"ẢNH THẬT"` evidence cards (with real local images or UI mockups + opaque non-overlapping stamps)
- Automatic Vietnamese/English TTS (`edge-tts` with `gTTS` fallback), procedural BGM, and synced SFX (`whoosh`, `pop`, `ding`)
- **Mandatory No-Overlap Layout Guard** (`assert_no_overlaps`) that verifies zero text/pill/card collisions before encoding

## Quick Start

```bash
git clone https://github.com/25phongsusu/boldline-explainer
cd boldline-explainer
pip install -r skills/boldline-explainer/scripts/requirements.txt

# Render the included example
python skills/boldline-explainer/scripts/render_video.py examples/rockylens_takedown.json -o out.mp4
```

Requires **Python 3.10+** and **FFmpeg** on PATH.

## Install as an Agent Skill

Link `skills/boldline-explainer` into your agent's skills directory:

```powershell
# Antigravity (Windows)
New-Item -ItemType Junction -Path "$env:USERPROFILE\.gemini\config\skills\boldline-explainer" -Target "$PWD\skills\boldline-explainer"

# Claude Code (Windows)
New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\boldline-explainer" -Target "$PWD\skills\boldline-explainer"
```

```bash
# macOS / Linux
ln -s "$PWD/skills/boldline-explainer" ~/.gemini/config/skills/boldline-explainer
```
