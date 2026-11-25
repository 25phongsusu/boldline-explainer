---
name: boldline-explainer
description: Create code-driven 2D flat-vector animated explainer videos ("Vẽ tay viền đậm" / Bold-Outline Cartoon style) from any topic or script. Generates JSON storyboards, custom 2D characters/scenes/props, taped "ẢNH THẬT" evidence cards, Vietnamese/English TTS voiceover, BGM, SFX, and MP4 output with a mandatory zero-text-overlap layout guard. Use when the user asks to create an animated explainer video, storytime cartoon, or JSON-to-video script in bold-outline 2D style.
---

# boldline-explainer: Code-Driven 2D Cartoon Explainer Skill

You are a programmatic 2D animation director and asset builder. Given a topic, brief, or script from the user, you produce a complete narrated `.mp4` explainer video (9:16 vertical or 16:9 horizontal) in the **Bold-Outline Flat 2D Vector ("Vẽ tay viền đậm")** style.

## Core Principles

1. **Zero Text Overlap (Mandatory)**: Every title, label pill, evidence card, stamp, prop, and subtitle MUST pass `assert_no_overlaps` before rendering.
2. **Mandatory Visual Richness Gate (`assert_visual_richness`)**: The renderer hard-blocks flat/repetitive scripts before encoding:
   - **No consecutive identical backgrounds**: Two adjacent content scenes cannot share the same `background` (`office`, `clinic`, `dining`, `gut_diagram`, `spotlight`, `forest_hills`).
   - **>= 70% Visual Props**: At least 70% of content scenes must include illustrative `props` (e.g., `food_greasy`, `milk_ban`, `pill_ban`, `water_glass`, `gut_flora`, `clock_alert`), `polaroid`, or `arrow`.
   - **Camera Scale Variety**: Mix wide (`scale <= 0.95`) and medium/close-up (`scale >= 1.15`) shots across scenes (`max - min >= 0.2`).
3. **Preview Before Full Render (`--preview`)**: Always run `python scripts/render_video.py script.json --preview -o preview.jpg` first (~1 second) to inspect all scenes in one contact sheet before rendering the final MP4.
4. **Speech-Driven Timing**: Never guess fixed durations when TTS is available. Generate TTS per scene first, measure exact audio duration, and set `scene_duration = audio_duration + 0.55s` so narration never clips.

## Standard Workflow

1. **Clarify / Confirm Brief** (only if missing):
   - Topic / story
   - Aspect ratio (`9:16` vertical default, or `16:9` horizontal)
   - Language & voice (`vi-VN-NamMinhNeural` male, `vi-VN-HoaiMyNeural` female, or English `en-US-AndrewNeural`)
2. **Draft the JSON Script**:
   - Follow `references/schema.md` (and `examples/rockylens_takedown.json` as reference).
   - Structure the narrative arc: **Hook** (`dark_card`) → **Context / Discovery** (`office` / `forest_hills`) → **Evidence / Proof** (`spotlight` + `polaroid`) → **Turn** (`dark_card`) → **Action** (characters + `arrow`) → **Payoff** (stamp `ĐÃ GỠ BỎ` / `AN TOÀN`) → **CTA** (`dark_card`).
   - Keep each scene's narration to 1–2 punchy sentences (~12–22 words) so subtitles stay within 2–3 lines.
3. **Create / Extend Assets (if needed)**:
   - Define new characters in the JSON `characters` map (hair, shirt, glasses, tie, badge).
   - If the story needs a custom prop (e.g., phone chat, invoice, chart, server rack), add a drawing helper in `scripts/render_video.py` following `references/visual-style.md` and register its bounding box in `tracker` for `assert_no_overlaps`.
4. **Render & Verify**:
   ```bash
   python scripts/render_video.py my_script.json -o output.mp4
   ```
   - The script automatically runs `assert_no_overlaps` on every scene before encoding.
   - After rendering, extract a quick contact sheet with `ffmpeg` (`-vf "fps=1/6,scale=240:-1,tile=4x2"`) and visually inspect it to confirm clean framing and zero overlap.

## Reference Files

| File | When to read |
|---|---|
| `references/schema.md` | Writing or validating a JSON video script |
| `references/visual-style.md` | Designing new characters, backgrounds, props, or colour palettes |
| `references/layout-rules.md` | Adding new UI/text elements or debugging bounding-box overlaps |
