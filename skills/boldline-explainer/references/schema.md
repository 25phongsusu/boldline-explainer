# JSON Script Schema (`boldline-explainer`)

Every video is defined by a single JSON file passed to `scripts/render_video.py`.

## Top-Level Fields

```json
{
  "title": "Rockylens Fake Site Takedown",
  "aspect": "9:16",
  "fps": 30,
  "lang": "vi",
  "voice": "vi-VN-NamMinhNeural",
  "bgm": true,
  "characters": {
    "emp": {
      "shirt_color": "#1e3a5f",
      "hair_color": "#1f2937",
      "glasses": true,
      "badge": true,
      "tie": false
    }
  },
  "scenes": [ ... ]
}
```

- `aspect`: `"9:16"` (`720x1280`) or `"16:9"` (`1280x720`).
- `voice`: Any `edge-tts` voice (e.g. `vi-VN-NamMinhNeural`, `vi-VN-HoaiMyNeural`, `en-US-AndrewNeural`). Falls back to `gTTS` using `lang` (`"vi"`, `"en"`, etc.) if `edge-tts` is unreachable.

## Scene Object

| Field | Type | Description |
|---|---|---|
| `id` | int | 1-based scene index |
| `background` | string | `"dark_card"`, `"office"`, `"spotlight"`, or `"forest_hills"` |
| `title` | string | Main red-and-white outlined title with yellow underline |
| `top_text` | string | Optional smaller yellow text above title (used on `dark_card`) |
| `badge_text` | string | Optional blue pill below title (e.g. `"rockylens.com"` on outro `dark_card`) |
| `text` | string | Spoken narration + bottom subtitle |
| `sfx` | string | `"whoosh"`, `"pop"`, or `"ding"` played at scene start |
| `actors` | list | Array of actor placements: `{"id": "emp", "x": 0.3, "y": 0.58, "mood": "normal|shocked|happy|determined", "arm_pose": "down|point_right|raised", "speaking": true, "scale": 1.0}` (`x`, `y` normalized 0–1) |
| `label` | dict | Optional pill: `{"text": "...", "x": 0.5, "y": 0.38, "delay": 0.6}` |
| `polaroid` | dict | Optional evidence card: `{"x": 0.46, "y": 0.37, "url": "...", "headline": "...", "cta": "...", "caption": "...", "image": null, "stamp": "GIẢ MẠO", "stamp_color": "#dc2626", "stamp_delay": 1.6}` |
| `arrow` | dict | Optional action arrow: `{"x0": 0.38, "x1": 0.65, "y": 0.46, "color": "#22c55e"}` |
| `sticker` | string | Optional `"sweat"`, `"heart"`, or `"exclamation"` |
