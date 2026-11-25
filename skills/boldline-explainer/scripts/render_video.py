import argparse
import asyncio
import json
import math
import os
import subprocess
import wave
from pathlib import Path

import edge_tts
from gtts import gTTS
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT_TITLE = load_font(54, True)
FONT_TOP = load_font(32, True)
FONT_LABEL = load_font(26, True)
FONT_SUB = load_font(30, True)
FONT_SMALL = load_font(21, False)
FONT_STAMP = load_font(38, True)


async def gen_tts_for_scene(sc: dict, voice: str, lang: str, work_dir: Path):
    mp3_path = work_dir / f"scene_{sc['id']}.mp3"
    wav_path = work_dir / f"scene_{sc['id']}.wav"
    used_gtts = False
    try:
        comm = edge_tts.Communicate(sc["text"], voice)
        await comm.save(str(mp3_path))
    except Exception:
        used_gtts = True
        gTTS(sc["text"], lang=lang).save(str(mp3_path))

    af = ["-af", "atempo=1.15"] if used_gtts else []
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(mp3_path), *af, "-ar", "44100", "-ac", "1", str(wav_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    with wave.open(str(wav_path), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())
    return wav_path, dur


def make_sfx(kind: str, sr: int = 44100) -> np.ndarray:
    if kind == "whoosh":
        n = int(0.32 * sr)
        t = np.linspace(0, 1, n)
        noise = np.random.randn(n)
        env = np.sin(np.pi * t) ** 1.5
        return (np.convolve(noise, np.ones(18) / 18, mode="same") * env * 0.25).astype(np.float32)
    if kind == "pop":
        n = int(0.12 * sr)
        t = np.linspace(0, 0.12, n)
        freq = 320 + 480 * np.exp(-t * 35)
        return (np.sin(2 * np.pi * freq * t) * np.exp(-t * 30) * 0.35).astype(np.float32)
    n = int(0.55 * sr)
    t = np.linspace(0, 0.55, n)
    env = np.exp(-t * 6.5)
    return ((np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1318.5 * t)) * env * 0.25).astype(np.float32)


def make_bgm(total_sec: float, sr: int = 44100) -> np.ndarray:
    n = int(total_sec * sr)
    beat_dur = 60.0 / 110.0 / 2.0
    notes = [349.23, 440.00, 523.25, 440.00, 392.00, 493.88, 587.33, 493.88]
    sig = np.zeros(n, dtype=np.float32)
    step = int(beat_dur * sr)
    for i, start in enumerate(range(0, n, step)):
        end = min(start + step, n)
        lt = np.linspace(0, (end - start) / sr, end - start, endpoint=False)
        f = notes[i % len(notes)]
        env = np.exp(-lt * 5.0) * np.minimum(1.0, lt / 0.015)
        sig[start:end] += (np.sin(2 * np.pi * f * lt) + 0.2 * np.sin(2 * np.pi * f * 2 * lt)) * env * 0.075
    return sig


def draw_outlined_text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, outline="#111111", stroke=5, anchor="mm"):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=outline, anchor=anchor)


def wrap_text(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_bg(draw: ImageDraw.ImageDraw, kind: str, W: int, H: int, t: float):
    floor_y = int(H * 0.735)
    if kind == "dark_card":
        draw.rectangle([0, 0, W, H], fill="#1a1c20")
        shift = int(20 * math.sin(t * 1.2))
        draw.polygon([(int(W * 0.16) + shift, 0), (int(W * 0.52) + shift, 0), (int(W * 0.75), H), (int(W * 0.30), H)], fill="#23262c")
        draw.ellipse([int(W * 0.2), int(H * 0.64), int(W * 0.8), int(H * 0.75)], fill="#262a31")
        return
    if kind == "forest_hills":
        draw.rectangle([0, 0, W, H], fill="#bae6fd")
        draw.pieslice([-int(W * 0.2), int(H * 0.58), int(W * 0.75), int(H * 1.05)], 180, 360, fill="#4ade80", outline="#166534", width=4)
        draw.pieslice([int(W * 0.25), int(H * 0.55), int(W * 1.2), int(H * 1.05)], 180, 360, fill="#22c55e", outline="#166534", width=4)
        for px in [int(W * 0.18), int(W * 0.82)]:
            draw.polygon([(px, int(H * 0.38)), (px - 55, int(H * 0.64)), (px + 55, int(H * 0.64))], fill="#15803d", outline="#111827", width=3)
        return
    if kind == "spotlight":
        draw.rectangle([0, 0, W, floor_y], fill="#e6e1c5")
        draw.polygon([(int(W * 0.33), 0), (int(W * 0.67), 0), (int(W * 0.90), floor_y), (int(W * 0.10), floor_y)], fill="#f4f0dc")
    else:  # office
        draw.rectangle([0, 0, W, floor_y], fill="#ece0be")
        wx0, wy0, wx1, wy1 = int(W * 0.21), int(H * 0.16), int(W * 0.79), int(H * 0.46)
        draw.rectangle([wx0, wy0, wx1, wy1], fill="#93c5fd", outline="#5c4033", width=10)
        draw.line([((wx0 + wx1) // 2, wy0), ((wx0 + wx1) // 2, wy1)], fill="#5c4033", width=7)
        draw.line([(wx0, (wy0 + wy1) // 2), (wx1, (wy0 + wy1) // 2)], fill="#5c4033", width=7)
        draw.rectangle([wx0 - 14, wy0 - 8, wx0 + 30, wy1 + 8], fill="#166534", outline="#1e293b", width=4)
        draw.rectangle([wx1 - 30, wy0 - 8, wx1 + 14, wy1 + 8], fill="#166534", outline="#1e293b", width=4)
        draw.rectangle([int(W * 0.05), floor_y - 100, int(W * 0.95), floor_y + 10], fill="#9a6b3f", outline="#1f2937", width=5)

    draw.rectangle([0, floor_y - 15, W, floor_y + 10], fill="#8c6d46", outline="#222222", width=4)
    draw.rectangle([0, floor_y + 10, W, H], fill="#b58453")
    for y in range(floor_y + 10, H, 45):
        draw.line([(0, y), (W, y)], fill="#7c5530", width=3)


def draw_character(draw: ImageDraw.ImageDraw, cx: int, cy: int, t: float, speaking: bool, cfg: dict, actor: dict):
    s = float(actor.get("scale", 1.0))
    mood = actor.get("mood", "normal")
    arm_pose = actor.get("arm_pose", "down")
    bob = int(3 * math.sin(t * 5.0)) if speaking else int(1.5 * math.sin(t * 2.0))
    cy += bob

    draw.line([(cx - int(18 * s), cy + int(95 * s)), (cx - int(18 * s), cy + int(145 * s))], fill="#111827", width=int(8 * s))
    draw.line([(cx + int(18 * s), cy + int(95 * s)), (cx + int(18 * s), cy + int(145 * s))], fill="#111827", width=int(8 * s))
    draw.ellipse([cx - int(34 * s), cy + int(138 * s), cx - int(4 * s), cy + int(156 * s)], fill="#111827")
    draw.ellipse([cx + int(4 * s), cy + int(138 * s), cx + int(34 * s), cy + int(156 * s)], fill="#111827")

    shirt = cfg.get("shirt_color", "#1e3a5f")
    draw.polygon(
        [(cx - int(45 * s), cy + int(10 * s)), (cx + int(45 * s), cy + int(10 * s)), (cx + int(56 * s), cy + int(100 * s)), (cx - int(56 * s), cy + int(100 * s))],
        fill=shirt, outline="#111827", width=int(5 * s),
    )
    draw.polygon([(cx - int(18 * s), cy + int(10 * s)), (cx, cy + int(28 * s)), (cx + int(18 * s), cy + int(10 * s))], fill="#ffffff", outline="#111827", width=int(3 * s))
    if cfg.get("badge"):
        draw.rectangle([cx + int(14 * s), cy + int(38 * s), cx + int(36 * s), cy + int(58 * s)], fill="#38bdf8", outline="#111827", width=int(3 * s))
    if cfg.get("tie"):
        draw.polygon([(cx - int(6 * s), cy + int(24 * s)), (cx + int(6 * s), cy + int(24 * s)), (cx + int(8 * s), cy + int(75 * s)), (cx, cy + int(85 * s)), (cx - int(8 * s), cy + int(75 * s))], fill="#dc2626", outline="#111827", width=int(3 * s))

    wave = int(8 * math.sin(t * 7.0)) if speaking else 0
    if arm_pose == "point_right":
        draw.line([(cx - int(45 * s), cy + int(25 * s)), (cx - int(72 * s), cy + int(65 * s))], fill="#111827", width=int(7 * s))
        draw.ellipse([cx - int(82 * s), cy + int(56 * s), cx - int(62 * s), cy + int(76 * s)], fill="#fde6d8", outline="#111827", width=int(3 * s))
        draw.line([(cx + int(45 * s), cy + int(25 * s)), (cx + int(105 * s), cy + int(10 * s) + wave)], fill="#111827", width=int(7 * s))
        draw.ellipse([cx + int(95 * s), cy + wave, cx + int(115 * s), cy + int(20 * s) + wave], fill="#fde6d8", outline="#111827", width=int(3 * s))
    elif arm_pose == "raised":
        draw.line([(cx - int(45 * s), cy + int(25 * s)), (cx - int(85 * s), cy - int(25 * s) + wave)], fill="#111827", width=int(7 * s))
        draw.ellipse([cx - int(95 * s), cy - int(35 * s) + wave, cx - int(75 * s), cy - int(15 * s) + wave], fill="#fde6d8", outline="#111827", width=int(3 * s))
        draw.line([(cx + int(45 * s), cy + int(25 * s)), (cx + int(85 * s), cy - int(25 * s) - wave)], fill="#111827", width=int(7 * s))
        draw.ellipse([cx + int(75 * s), cy - int(35 * s) - wave, cx + int(95 * s), cy - int(15 * s) - wave], fill="#fde6d8", outline="#111827", width=int(3 * s))
    else:
        draw.line([(cx - int(45 * s), cy + int(25 * s)), (cx - int(65 * s), cy + int(78 * s) + wave // 2)], fill="#111827", width=int(7 * s))
        draw.ellipse([cx - int(75 * s), cy + int(68 * s) + wave // 2, cx - int(55 * s), cy + int(88 * s) + wave // 2], fill="#fde6d8", outline="#111827", width=int(3 * s))
        draw.line([(cx + int(45 * s), cy + int(25 * s)), (cx + int(65 * s), cy + int(78 * s) - wave // 2)], fill="#111827", width=int(7 * s))
        draw.ellipse([cx + int(55 * s), cy + int(68 * s) - wave // 2, cx + int(75 * s), cy + int(88 * s) - wave // 2], fill="#fde6d8", outline="#111827", width=int(3 * s))

    hr = int(68 * s)
    hy = cy - int(55 * s)
    draw.ellipse([cx - hr, hy - hr, cx + hr, hy + hr], fill="#fde6d8", outline="#111827", width=int(6 * s))
    draw.pieslice([cx - hr - int(4 * s), hy - hr - int(8 * s), cx + hr + int(4 * s), hy + int(8 * s)], 185, 355, fill=cfg.get("hair_color", "#1f2937"), outline="#111827", width=int(5 * s))
    draw.ellipse([cx - int(48 * s), hy + int(12 * s), cx - int(28 * s), hy + int(26 * s)], fill="#fca5a5")
    draw.ellipse([cx + int(28 * s), hy + int(12 * s), cx + int(48 * s), hy + int(26 * s)], fill="#fca5a5")

    blink = (int(t * 10) % 32) == 0
    if blink:
        draw.line([(cx - int(32 * s), hy - int(4 * s)), (cx - int(14 * s), hy - int(4 * s))], fill="#111827", width=int(4 * s))
        draw.line([(cx + int(14 * s), hy - int(4 * s)), (cx + int(32 * s), hy - int(4 * s))], fill="#111827", width=int(4 * s))
    elif mood == "shocked":
        for ex in (cx - int(24 * s), cx + int(24 * s)):
            draw.ellipse([ex - int(12 * s), hy - int(16 * s), ex + int(12 * s), hy + int(8 * s)], fill="#ffffff", outline="#111827", width=int(3 * s))
            draw.ellipse([ex - int(4 * s), hy - int(8 * s), ex + int(4 * s), hy], fill="#111827")
    else:
        for ex in (cx - int(24 * s), cx + int(24 * s)):
            draw.ellipse([ex - int(7 * s), hy - int(11 * s), ex + int(7 * s), hy + int(3 * s)], fill="#111827")
            draw.ellipse([ex - int(3 * s), hy - int(9 * s), ex + int(1 * s), hy - int(5 * s)], fill="#ffffff")

    if cfg.get("glasses"):
        draw.ellipse([cx - int(42 * s), hy - int(22 * s), cx - int(6 * s), hy + int(12 * s)], outline="#374151", width=int(4 * s))
        draw.ellipse([cx + int(6 * s), hy - int(22 * s), cx + int(42 * s), hy + int(12 * s)], outline="#374151", width=int(4 * s))
        draw.line([(cx - int(6 * s), hy - int(5 * s)), (cx + int(6 * s), hy - int(5 * s))], fill="#374151", width=int(4 * s))

    mouth_open = speaking and (int(t * 12) % 2 == 0)
    if mood == "shocked":
        draw.ellipse([cx - int(12 * s), hy + int(22 * s), cx + int(12 * s), hy + int(42 * s)], fill="#7f1d1d", outline="#111827", width=int(3 * s))
    elif mood == "happy":
        draw.arc([cx - int(18 * s), hy + int(16 * s), cx + int(18 * s), hy + int(38 * s)], 10, 170, fill="#111827", width=int(4 * s))
    elif mouth_open:
        draw.ellipse([cx - int(10 * s), hy + int(22 * s), cx + int(10 * s), hy + int(36 * s)], fill="#991b1b", outline="#111827", width=int(3 * s))
    else:
        draw.line([(cx - int(14 * s), hy + int(28 * s)), (cx + int(14 * s), hy + int(28 * s))], fill="#111827", width=int(4 * s))


def draw_polaroid(img: Image.Image, draw: ImageDraw.ImageDraw, pol: dict, W: int, H: int, lt: float, tracker: list | None):
    x, y = int(pol["x"] * W), int(pol["y"] * H)
    w, h = 330, 320
    draw.rectangle([x, y, x + w, y + h], fill="#f8fafc", outline="#1f2937", width=5)
    draw.rectangle([x + 115, y - 16, x + 215, y + 18], fill="#fde047", outline="#a16207", width=2)
    draw.rectangle([x + 16, y + 28, x + w - 16, y + 66], fill="#e2e8f0", outline="#64748b", width=2)
    draw_outlined_text(draw, (x + w // 2, y + 47), pol.get("url", "example.com"), FONT_SMALL, "#dc2626", outline="#ffffff", stroke=1)

    stamp = pol.get("stamp") if lt >= pol.get("stamp_delay", 0.0) else None
    stamp_color = pol.get("stamp_color", "#dc2626")

    if pol.get("image") and os.path.exists(pol["image"]):
        photo = Image.open(pol["image"]).convert("RGB").resize((w - 32, 174))
        img.paste(photo, (x + 16, y + 74))
        draw_outlined_text(draw, (x + 56, y + 92), "ẢNH THẬT", FONT_SMALL, "#dc2626", outline="#ffffff", stroke=3)
    else:
        draw.rectangle([x + 16, y + 74, x + w - 16, y + 248], fill="#fef2f2", outline="#fca5a5", width=2)
        if stamp:
            draw_outlined_text(draw, (x + w // 2, y + 98), pol.get("headline", ""), FONT_SMALL, "#b91c1c", outline="#ffffff", stroke=2)
            sx0, sy0, sx1, sy1 = x + 38, y + 124, x + w - 38, y + 184
            draw.rectangle([sx0, sy0, sx1, sy1], fill="#ffffff", outline=stamp_color, width=6)
            draw_outlined_text(draw, (x + w // 2, (sy0 + sy1) // 2), stamp, FONT_STAMP, stamp_color, outline="#ffffff", stroke=2)
            draw.rectangle([x + 65, y + 198, x + w - 65, y + 236], fill="#dc2626", outline="#7f1d1d", width=3)
            draw_outlined_text(draw, (x + w // 2, y + 217), pol.get("cta", ""), FONT_SMALL, "#ffffff", outline="#7f1d1d", stroke=2)
        else:
            draw_outlined_text(draw, (x + w // 2, y + 125), pol.get("headline", ""), FONT_LABEL, "#b91c1c", outline="#ffffff", stroke=2)
            draw.rectangle([x + 65, y + 168, x + w - 65, y + 212], fill="#dc2626", outline="#7f1d1d", width=3)
            draw_outlined_text(draw, (x + w // 2, y + 190), pol.get("cta", ""), FONT_SMALL, "#ffffff", outline="#7f1d1d", stroke=2)

    draw_outlined_text(draw, (x + w // 2, y + 284), pol.get("caption", "Ảnh chụp bằng chứng"), FONT_SMALL, "#334155", outline="#ffffff", stroke=1)
    if tracker is not None:
        tracker.append(("polaroid", (x, y - 16, x + w, y + h)))


def draw_label_pill(draw: ImageDraw.ImageDraw, lbl: dict, W: int, H: int, lt: float, tracker: list | None):
    if lt < lbl.get("delay", 0.0):
        return
    cx, cy = int(lbl["x"] * W), int(lbl["y"] * H)
    text = lbl["text"]
    bbox = draw.textbbox((0, 0), text, font=FONT_LABEL)
    half_w = max(90, (bbox[2] - bbox[0]) // 2 + 28)
    box = (cx - half_w, cy - 28, cx + half_w, cy + 28)
    draw.rounded_rectangle(list(box), radius=14, fill="#ffffff", outline="#111827", width=4)
    draw_outlined_text(draw, (cx, cy), text, FONT_LABEL, "#111827", outline="#ffffff", stroke=0)
    if tracker is not None:
        tracker.append((f"pill:{text}", box))


def assert_no_overlaps(scene_id: int, boxes: list[tuple[str, tuple[int, int, int, int]]]):
    for i in range(len(boxes)):
        na, (ax0, ay0, ax1, ay1) = boxes[i]
        for j in range(i + 1, len(boxes)):
            nb, (bx0, by0, bx1, by1) = boxes[j]
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                raise ValueError(f"Scene {scene_id} overlap: {na} {boxes[i][1]} vs {nb} {boxes[j][1]}")


def render_scene_frame(spec: dict, sc: dict, lt: float, dur: float, W: int, H: int, check_layout: bool = False) -> Image.Image:
    bg_kind = sc.get("background", "office")
    img = Image.new("RGB", (W, H), "#18181b")
    draw = ImageDraw.Draw(img)
    tracker = [] if check_layout else None

    draw_bg(draw, bg_kind, W, H, lt)

    if bg_kind == "dark_card":
        ty = int(H * 0.37)
        if sc.get("top_text"):
            draw_outlined_text(draw, (W // 2, ty), sc["top_text"], FONT_TOP, "#fef08a", outline="#000000", stroke=4)
        draw_outlined_text(draw, (W // 2, ty + 85), sc["title"], FONT_TITLE, "#ef4444", outline="#ffffff", stroke=6)
        draw.line([(int(W * 0.16), ty + 125), (int(W * 0.84), ty + 125)], fill="#facc15", width=7)
        if sc.get("badge_text"):
            draw.rounded_rectangle([W // 2 - 140, ty + 175, W // 2 + 140, ty + 240], radius=16, fill="#0ea5e9", outline="#ffffff", width=4)
            draw_outlined_text(draw, (W // 2, ty + 207), sc["badge_text"], FONT_LABEL, "#ffffff", outline="#0369a1", stroke=2)
    else:
        ty = int(H * 0.115)
        draw_outlined_text(draw, (W // 2, ty), sc["title"], FONT_TITLE, "#ef4444", outline="#ffffff", stroke=6)
        draw.line([(int(W * 0.16), ty + 42), (int(W * 0.84), ty + 42)], fill="#facc15", width=7)
        if tracker is not None:
            tracker.append(("title", (int(W * 0.16), ty - 35, int(W * 0.84), ty + 46)))

    for actor in sc.get("actors", []):
        cfg = spec.get("characters", {}).get(actor["id"], {})
        speaking = actor.get("speaking", False) and (lt < dur - 0.35)
        draw_character(draw, int(actor["x"] * W), int(actor["y"] * H), lt, speaking, cfg, actor)

    if sc.get("arrow"):
        ar = sc["arrow"]
        x0, x1, y = int(ar["x0"] * W), int(ar["x1"] * W), int(ar["y"] * H)
        draw.polygon([(x0, y - 20), (x1 - 45, y - 20), (x1 - 45, y - 42), (x1, y), (x1 - 45, y + 42), (x1 - 45, y + 20), (x0, y + 20)], fill=ar.get("color", "#22c55e"), outline="#111827", width=4)
        if tracker is not None:
            tracker.append(("arrow", (x0, y - 42, x1, y + 42)))

    if sc.get("polaroid"):
        draw_polaroid(img, draw, sc["polaroid"], W, H, lt, tracker)

    if sc.get("label"):
        draw_label_pill(draw, sc["label"], W, H, lt, tracker)

    # Subtitles
    lines = wrap_text(sc["text"], FONT_SUB, W - 90, draw)
    sub_base = int(H * 0.85) - (len(lines) - 1) * 40
    for i, ln in enumerate(lines):
        draw_outlined_text(draw, (W // 2, sub_base + i * 42), ln, FONT_SUB, "#ffffff", outline="#000000", stroke=6)
    if tracker is not None and lines:
        tracker.append(("subtitles", (40, sub_base - 24, W - 40, sub_base + len(lines) * 42)))
        assert_no_overlaps(sc["id"], tracker)

    return img


async def build(script_path: Path, out_mp4: Path):
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    aspect = spec.get("aspect", "9:16")
    W, H = (1280, 720) if aspect == "16:9" else (720, 1280)
    FPS = int(spec.get("fps", 30))
    voice = spec.get("voice", "vi-VN-NamMinhNeural")
    lang = spec.get("lang", "vi")

    work_dir = out_mp4.parent / ".build_cache"
    work_dir.mkdir(parents=True, exist_ok=True)

    sr = 44100
    scene_durs, wav_arrays, sfx_events = [], [], []
    cur_sample = 0
    for sc in spec["scenes"]:
        wav_path, audio_dur = await gen_tts_for_scene(sc, voice, lang, work_dir)
        dur = round(audio_dur + 0.55, 2)
        scene_durs.append(dur)
        with wave.open(str(wav_path), "rb") as w:
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        target_len = int(dur * sr)
        padded = np.zeros(target_len, dtype=np.float32)
        start_idx = int(0.15 * sr)
        copy_len = min(len(raw), target_len - start_idx)
        padded[start_idx : start_idx + copy_len] = raw[:copy_len]
        wav_arrays.append(padded)
        if sc.get("sfx"):
            sfx_events.append((cur_sample + int(0.05 * sr), sc["sfx"]))
        cur_sample += target_len

    voice_full = np.concatenate(wav_arrays)
    mix = voice_full * 0.9
    if spec.get("bgm", True):
        mix = mix + make_bgm(len(voice_full) / sr, sr)
    for pos, kind in sfx_events:
        s = make_sfx(kind, sr)
        end = min(pos + len(s), len(mix))
        mix[pos:end] += s[: end - pos]
    mix = np.clip(mix, -0.95, 0.95)

    mix_wav = work_dir / "full_mix.wav"
    with wave.open(str(mix_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((mix * 32767.0).astype(np.int16).tobytes())

    for idx, sc in enumerate(spec["scenes"]):
        render_scene_frame(spec, sc, max(0.0, scene_durs[idx] - 0.5), scene_durs[idx], W, H, check_layout=True)
    print(f"Layout guard passed for all {len(spec['scenes'])} scenes (0 overlaps).")

    ffmpeg_log = open(work_dir / "ffmpeg.log", "wb")
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(FPS), "-i", "-",
            "-i", str(mix_wav),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_mp4),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_log,
    )
    dissolve_frames = int(0.25 * FPS)
    prev_last = None
    for idx, sc in enumerate(spec["scenes"]):
        dur = scene_durs[idx]
        n_frames = int(round(dur * FPS))
        for f_idx in range(n_frames):
            frame = render_scene_frame(spec, sc, f_idx / float(FPS), dur, W, H)
            if prev_last is not None and f_idx < dissolve_frames:
                frame = Image.blend(prev_last, frame, (f_idx + 1) / float(dissolve_frames + 1))
            proc.stdin.write(frame.tobytes())
            if f_idx == n_frames - 1:
                prev_last = frame
    proc.stdin.close()
    ret = proc.wait()
    ffmpeg_log.close()
    if ret != 0:
        raise RuntimeError((work_dir / "ffmpeg.log").read_text(encoding="utf-8", errors="replace"))
    print("Rendered ->", out_mp4)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Render boldline-explainer JSON script to MP4.")
    p.add_argument("script", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("output.mp4"))
    args = p.parse_args()
    asyncio.run(build(args.script, args.output))
