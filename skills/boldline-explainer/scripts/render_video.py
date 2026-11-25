import argparse
import asyncio
import json
import math
import os
import re
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


def estimate_word_timings(text: str, audio_dur: float) -> list[dict]:
    """Fallback proportional word timings when WordBoundary events are unavailable."""
    words = text.split()
    if not words:
        return []
    weights = [max(1, len(re.sub(r"[^\w]", "", w))) for w in words]
    total_w = sum(weights)
    timings, cur = [], 0.0
    for w, wt in zip(words, weights):
        wdur = audio_dur * (wt / total_w)
        timings.append({"word": w, "start": round(cur, 3), "end": round(cur + wdur, 3)})
        cur += wdur
    return timings


async def gen_tts_for_scene(sc: dict, voice: str, lang: str, work_dir: Path):
    mp3_path = work_dir / f"scene_{sc['id']}.mp3"
    wav_path = work_dir / f"scene_{sc['id']}.wav"
    words_path = work_dir / f"scene_{sc['id']}_words.json"

    if wav_path.exists() and wav_path.stat().st_size > 1000 and words_path.exists():
        with wave.open(str(wav_path), "rb") as w:
            dur = w.getnframes() / float(w.getframerate())
        return wav_path, dur, json.loads(words_path.read_text(encoding="utf-8"))

    used_gtts = False
    word_timings = []
    if not (wav_path.exists() and wav_path.stat().st_size > 1000):
        try:
            comm = edge_tts.Communicate(sc["text"], voice)
            with open(mp3_path, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        st = chunk["offset"] / 10_000_000.0
                        ed = st + chunk["duration"] / 10_000_000.0
                        word_timings.append({"word": chunk["text"], "start": round(st, 3), "end": round(ed, 3)})
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

    if not word_timings:
        word_timings = estimate_word_timings(sc["text"], dur)
    words_path.write_text(json.dumps(word_timings, ensure_ascii=False), encoding="utf-8")
    return wav_path, dur, word_timings


def resolve_trigger_delay(item: dict, word_timings: list[dict], voice_offset: float = 0.15) -> float:
    """Resolve `trigger_word` to exact timestamp in scene seconds, or fall back to `delay`."""
    tw = item.get("trigger_word", "").strip().lower()
    if tw and word_timings:
        for wt in word_timings:
            clean = re.sub(r"[^\w\s]", "", wt["word"].lower())
            if tw in clean or clean in tw and len(clean) >= 2:
                return round(voice_offset + wt["start"], 3)
        # Multi-word phrase check
        joined = " ".join(re.sub(r"[^\w]", "", w["word"].lower()) for w in word_timings)
        idx = joined.find(tw)
        if idx >= 0:
            prefix_words = len(joined[:idx].split())
            if prefix_words < len(word_timings):
                return round(voice_offset + word_timings[prefix_words]["start"], 3)
    return float(item.get("delay", 0.0))


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


def wrap_text_words(text: str, font, max_w: int, draw: ImageDraw.ImageDraw) -> list[list[tuple[int, str]]]:
    """Wrap text into lines of (global_word_idx, word_str) for karaoke word highlighting."""
    words = text.split()
    lines, cur = [], []
    for idx, w in enumerate(words):
        test = " ".join([cw for _, cw in cur] + [w])
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur.append((idx, w))
        else:
            if cur:
                lines.append(cur)
            cur = [(idx, w)]
    if cur:
        lines.append(cur)
    return lines


def draw_karaoke_subtitles(draw: ImageDraw.ImageDraw, text: str, word_timings: list[dict], lt: float, W: int, H: int, tracker: list | None):
    lines = wrap_text_words(text, FONT_SUB, W - 90, draw)
    if not lines:
        return
    # Determine active word index from speech time (voice starts at 0.15s)
    speech_t = lt - 0.15
    active_idx = -1
    for i, wt in enumerate(word_timings):
        if wt["start"] <= speech_t <= wt["end"] + 0.08:
            active_idx = i
            break

    sub_base = int(H * 0.85) - (len(lines) - 1) * 40
    for row_i, row in enumerate(lines):
        y = sub_base + row_i * 42
        full_line = " ".join(w for _, w in row)
        total_w = draw.textbbox((0, 0), full_line, font=FONT_SUB)[2] - draw.textbbox((0, 0), full_line, font=FONT_SUB)[0]
        cur_x = (W - total_w) // 2
        for w_idx, w in row:
            color = "#fde047" if w_idx == active_idx else "#ffffff"
            draw.text((cur_x, y), w, font=FONT_SUB, fill=color, stroke_width=6, stroke_fill="#000000", anchor="lm")
            adv = draw.textbbox((0, 0), w + " ", font=FONT_SUB)[2] - draw.textbbox((0, 0), w + " ", font=FONT_SUB)[0]
            cur_x += adv

    if tracker is not None:
        tracker.append(("subtitles", (40, sub_base - 24, W - 40, sub_base + len(lines) * 42)))


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
    if kind == "gut_diagram":
        draw.rectangle([0, 0, W, floor_y], fill="#fef3c7")
        draw.rounded_rectangle([int(W * 0.10), int(H * 0.20), int(W * 0.90), int(H * 0.65)], radius=40, fill="#fed7aa", outline="#9a3412", width=6)
        draw.rounded_rectangle([int(W * 0.16), int(H * 0.25), int(W * 0.84), int(H * 0.60)], radius=30, fill="#fff7ed", outline="#c2410c", width=4)
    elif kind == "clinic":
        draw.rectangle([0, 0, W, floor_y], fill="#e0f2fe")
        cx, cy = int(W * 0.78), int(H * 0.28)
        draw.rectangle([cx - 55, cy - 65, cx + 55, cy + 65], fill="#ffffff", outline="#111827", width=4)
        draw.rectangle([cx - 12, cy - 38, cx + 12, cy + 38], fill="#ef4444")
        draw.rectangle([cx - 38, cy - 12, cx + 38, cy + 12], fill="#ef4444")
    elif kind == "dining":
        draw.rectangle([0, 0, W, floor_y], fill="#fde68a")
        draw.rectangle([int(W * 0.05), floor_y - 95, int(W * 0.95), floor_y + 10], fill="#dc2626", outline="#111827", width=5)
        draw.rectangle([int(W * 0.05), floor_y - 95, int(W * 0.95), floor_y - 75], fill="#ffffff", outline="#111827", width=3)
    elif kind == "spotlight":
        draw.rectangle([0, 0, W, floor_y], fill="#e6e1c5")
        draw.polygon([(int(W * 0.33), 0), (int(W * 0.67), 0), (int(W * 0.90), floor_y), (int(W * 0.10), floor_y)], fill="#f4f0dc")
    else:
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


def draw_character(draw: ImageDraw.ImageDraw, cx: int, cy: int, t: float, mouth_amp: float, cfg: dict, actor: dict):
    s = float(actor.get("scale", 1.0))
    mood = actor.get("mood", "normal")
    arm_pose = actor.get("arm_pose", "down")
    speaking = mouth_amp > 0.12
    bob = int(3 * mouth_amp * math.sin(t * 9.0)) if speaking else int(1.5 * math.sin(t * 2.0))
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

    wave = int(10 * mouth_amp * math.sin(t * 8.0)) if speaking else 0
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

    # Audio-reactive lip-sync: mouth height scales smoothly with voice RMS
    if speaking:
        mh = max(3, int(11 * min(1.0, mouth_amp * 1.4)))
        draw.ellipse([cx - int(11 * s), hy + int(28 * s) - mh, cx + int(11 * s), hy + int(28 * s) + mh], fill="#991b1b", outline="#111827", width=int(3 * s))
    elif mood == "shocked":
        draw.ellipse([cx - int(12 * s), hy + int(22 * s), cx + int(12 * s), hy + int(42 * s)], fill="#7f1d1d", outline="#111827", width=int(3 * s))
    elif mood == "happy":
        draw.arc([cx - int(18 * s), hy + int(16 * s), cx + int(18 * s), hy + int(38 * s)], 10, 170, fill="#111827", width=int(4 * s))
    else:
        draw.line([(cx - int(14 * s), hy + int(28 * s)), (cx + int(14 * s), hy + int(28 * s))], fill="#111827", width=int(4 * s))


def draw_prop(draw: ImageDraw.ImageDraw, prop: dict, W: int, H: int, lt: float, delay: float, tracker: list | None):
    if lt < delay:
        return
    kind = prop["kind"]
    cx, cy = int(prop["x"] * W), int(prop["y"] * H)
    dt = lt - delay
    s = 1.0 + 0.12 * math.sin(min(dt, 0.25) / 0.25 * math.pi)
    r = int(64 * s)

    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#ffffff", outline="#111827", width=5)

    if kind == "food_greasy":
        draw.ellipse([cx - 38, cy - 10, cx + 38, cy + 32], fill="#fde047", outline="#111827", width=3)
        draw.pieslice([cx - 28, cy - 30, cx + 28, cy + 12], 180, 360, fill="#f97316", outline="#111827", width=3)
        draw.line([(cx - 36, cy - 36), (cx + 36, cy + 36)], fill="#dc2626", width=7)
        draw.line([(cx + 36, cy - 36), (cx - 36, cy + 36)], fill="#dc2626", width=7)
    elif kind == "milk_ban":
        draw.rectangle([cx - 22, cy - 34, cx + 22, cy + 34], fill="#bfdbfe", outline="#111827", width=3)
        draw.polygon([(cx - 22, cy - 34), (cx, cy - 48), (cx + 22, cy - 34)], fill="#60a5fa", outline="#111827", width=3)
        draw.line([(cx - 34, cy - 34), (cx + 34, cy + 34)], fill="#dc2626", width=7)
    elif kind == "pill_ban":
        draw.rounded_rectangle([cx - 24, cy - 16, cx + 24, cy + 34], radius=8, fill="#fca5a5", outline="#111827", width=3)
        draw.rectangle([cx - 18, cy - 30, cx + 18, cy - 14], fill="#94a3b8", outline="#111827", width=3)
        draw.ellipse([cx - 42, cy - 42, cx + 42, cy + 42], outline="#dc2626", width=6)
        draw.line([(cx - 30, cy - 30), (cx + 30, cy + 30)], fill="#dc2626", width=6)
    elif kind == "water_glass":
        draw.polygon([(cx - 24, cy - 34), (cx + 24, cy - 34), (cx + 18, cy + 34), (cx - 18, cy + 34)], fill="#e0f2fe", outline="#111827", width=3)
        draw.polygon([(cx - 21, cy - 8), (cx + 21, cy - 8), (cx + 18, cy + 34), (cx - 18, cy + 34)], fill="#38bdf8", outline="#111827", width=2)
        draw.line([(cx - 14, cy + 12), (cx - 2, cy + 24), (cx + 22, cy - 6)], fill="#16a34a", width=6)
    elif kind == "gut_flora":
        draw.ellipse([cx - 34, cy - 26, cx - 2, cy + 6], fill="#4ade80", outline="#111827", width=3)
        draw.ellipse([cx + 4, cy - 10, cx + 36, cy + 22], fill="#f87171", outline="#111827", width=3)
        draw.ellipse([cx - 16, cy + 8, cx + 14, cy + 38], fill="#60a5fa", outline="#111827", width=3)
    elif kind == "clock_alert":
        draw.ellipse([cx - 34, cy - 34, cx + 34, cy + 34], fill="#fef08a", outline="#111827", width=4)
        draw.line([(cx, cy), (cx, cy - 22)], fill="#111827", width=5)
        draw.line([(cx, cy), (cx + 18, cy + 8)], fill="#dc2626", width=5)

    if tracker is not None:
        tracker.append((f"prop:{kind}", (cx - r, cy - r, cx + r, cy + r)))


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


def draw_label_pill(draw: ImageDraw.ImageDraw, lbl: dict, W: int, H: int, lt: float, delay: float, tracker: list | None):
    if lt < delay:
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


def assert_visual_richness(spec: dict):
    content_scenes = [s for s in spec["scenes"] if s.get("background", "office") != "dark_card"]
    if len(content_scenes) < 2:
        return
    for a, b in zip(content_scenes, content_scenes[1:]):
        if a.get("background", "office") == b.get("background", "office"):
            raise ValueError(f"Richness gate failed: consecutive scenes {a['id']} & {b['id']} share background '{a.get('background')}'")
    with_props = sum(1 for s in content_scenes if s.get("props") or s.get("polaroid") or s.get("arrow"))
    if with_props / len(content_scenes) < 0.7:
        raise ValueError(f"Richness gate failed: only {with_props}/{len(content_scenes)} content scenes have props/polaroid/arrow (need >= 70%).")
    scales = [float(a.get("scale", 1.0)) for s in content_scenes for a in s.get("actors", [])]
    if scales and (max(scales) - min(scales) < 0.2):
        raise ValueError(f"Richness gate failed: actor scale range too flat ({min(scales):.2f}..{max(scales):.2f}).")


def render_scene_frame(
    spec: dict, sc: dict, lt: float, dur: float, W: int, H: int,
    word_timings: list[dict] | None = None,
    mouth_amp: float = 0.0,
    prop_delays: list[float] | None = None,
    label_delay: float | None = None,
    check_layout: bool = False,
) -> Image.Image:
    bg_kind = sc.get("background", "office")
    img = Image.new("RGB", (W, H), "#18181b")
    draw = ImageDraw.Draw(img)
    tracker = [] if check_layout else None
    word_timings = word_timings or []

    draw_bg(draw, bg_kind, W, H, lt)

    if bg_kind == "dark_card":
        ty = int(H * 0.37)
        if sc.get("top_text"):
            draw_outlined_text(draw, (W // 2, ty), sc["top_text"], FONT_TOP, "#fef08a", outline="#000000", stroke=4)
        draw_outlined_text(draw, (W // 2, ty + 85), sc["title"], FONT_TITLE, "#ef4444", outline="#ffffff", stroke=6)
        draw.line([(int(W * 0.16), ty + 125), (int(W * 0.84), ty + 125)], fill="#facc15", width=7)
        if sc.get("badge_text"):
            draw.rounded_rectangle([W // 2 - 165, ty + 175, W // 2 + 165, ty + 240], radius=16, fill="#0ea5e9", outline="#ffffff", width=4)
            draw_outlined_text(draw, (W // 2, ty + 207), sc["badge_text"], FONT_LABEL, "#ffffff", outline="#0369a1", stroke=2)
    else:
        ty = int(H * 0.115)
        draw_outlined_text(draw, (W // 2, ty), sc["title"], FONT_TITLE, "#ef4444", outline="#ffffff", stroke=6)
        draw.line([(int(W * 0.16), ty + 42), (int(W * 0.84), ty + 42)], fill="#facc15", width=7)
        if tracker is not None:
            tracker.append(("title", (int(W * 0.16), ty - 35, int(W * 0.84), ty + 46)))

    for actor in sc.get("actors", []):
        cfg = spec.get("characters", {}).get(actor["id"], {})
        amp = mouth_amp if actor.get("speaking", False) else 0.0
        draw_character(draw, int(actor["x"] * W), int(actor["y"] * H), lt, amp, cfg, actor)

    for p_idx, prop in enumerate(sc.get("props", [])):
        d = prop_delays[p_idx] if prop_delays is not None else float(prop.get("delay", 0.0))
        draw_prop(draw, prop, W, H, lt, d, tracker)

    if sc.get("arrow"):
        ar = sc["arrow"]
        x0, x1, y = int(ar["x0"] * W), int(ar["x1"] * W), int(ar["y"] * H)
        draw.polygon([(x0, y - 20), (x1 - 45, y - 20), (x1 - 45, y - 42), (x1, y), (x1 - 45, y + 42), (x1 - 45, y + 20), (x0, y + 20)], fill=ar.get("color", "#22c55e"), outline="#111827", width=4)
        if tracker is not None:
            tracker.append(("arrow", (x0, y - 42, x1, y + 42)))

    if sc.get("polaroid"):
        draw_polaroid(img, draw, sc["polaroid"], W, H, lt, tracker)

    if sc.get("label"):
        ld = label_delay if label_delay is not None else float(sc["label"].get("delay", 0.0))
        draw_label_pill(draw, sc["label"], W, H, lt, ld, tracker)

    draw_karaoke_subtitles(draw, sc["text"], word_timings, lt, W, H, tracker)
    if tracker is not None:
        assert_no_overlaps(sc["id"], tracker)

    return img


def render_preview_grid(spec: dict, out_jpg: Path, W: int, H: int):
    scenes = spec["scenes"]
    cols = min(5, len(scenes))
    rows = math.ceil(len(scenes) / cols)
    tw, th = 240, int(240 * H / W)
    grid = Image.new("RGB", (cols * tw, rows * th), "#111827")
    for idx, sc in enumerate(scenes):
        frame = render_scene_frame(spec, sc, 3.0, 6.0, W, H, check_layout=True).resize((tw, th), Image.Resampling.LANCZOS)
        r, c = divmod(idx, cols)
        grid.paste(frame, (c * tw, r * th))
    grid.save(out_jpg, quality=90)
    print(f"Preview grid saved -> {out_jpg}")


async def build(script_path: Path, out_path: Path, preview_only: bool, allow_simple: bool):
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    if not allow_simple:
        assert_visual_richness(spec)
    aspect = spec.get("aspect", "9:16")
    W, H = (1280, 720) if aspect == "16:9" else (720, 1280)

    if preview_only:
        render_preview_grid(spec, out_path, W, H)
        return

    FPS = int(spec.get("fps", 30))
    voice = spec.get("voice", "vi-VN-NamMinhNeural")
    lang = spec.get("lang", "vi")
    work_dir = out_path.parent / ".build_cache"
    work_dir.mkdir(parents=True, exist_ok=True)

    sr = 44100
    scene_durs, wav_arrays, sfx_events = [], [], []
    all_word_timings, all_prop_delays, all_label_delays, frame_rms_envs = [], [], [], []
    cur_sample = 0

    for sc in spec["scenes"]:
        wav_path, audio_dur, word_timings = await gen_tts_for_scene(sc, voice, lang, work_dir)
        dur = round(audio_dur + 0.55, 2)
        scene_durs.append(dur)
        all_word_timings.append(word_timings)

        with wave.open(str(wav_path), "rb") as w:
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        target_len = int(dur * sr)
        padded = np.zeros(target_len, dtype=np.float32)
        start_idx = int(0.15 * sr)
        copy_len = min(len(raw), target_len - start_idx)
        padded[start_idx : start_idx + copy_len] = raw[:copy_len]
        wav_arrays.append(padded)

        # Compute per-frame RMS amplitude for real lip-sync
        n_frames = int(round(dur * FPS))
        samples_per_frame = sr // FPS
        env = []
        for f_i in range(n_frames):
            seg = padded[f_i * samples_per_frame : (f_i + 1) * samples_per_frame]
            rms = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
            env.append(min(1.0, rms * 6.5))
        frame_rms_envs.append(env)

        if sc.get("sfx"):
            sfx_events.append((cur_sample + int(0.05 * sr), sc["sfx"]))

        # Resolve word-triggered prop delays & schedule pop SFX on each trigger
        p_delays = []
        for prop in sc.get("props", []):
            d = resolve_trigger_delay(prop, word_timings)
            p_delays.append(d)
            if prop.get("trigger_word"):
                sfx_events.append((cur_sample + int(d * sr), prop.get("sfx", "pop")))
        all_prop_delays.append(p_delays)

        ld = resolve_trigger_delay(sc["label"], word_timings) if sc.get("label") else None
        all_label_delays.append(ld)
        cur_sample += target_len

    voice_full = np.concatenate(wav_arrays)
    mix = voice_full * 0.9
    if spec.get("bgm", True):
        mix = mix + make_bgm(len(voice_full) / sr, sr)
    for pos, kind in sfx_events:
        s = make_sfx(kind, sr)
        end = min(pos + len(s), len(mix))
        if pos < len(mix):
            mix[pos:end] += s[: end - pos]
    mix = np.clip(mix, -0.95, 0.95)

    mix_wav = work_dir / "full_mix.wav"
    with wave.open(str(mix_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((mix * 32767.0).astype(np.int16).tobytes())

    for idx, sc in enumerate(spec["scenes"]):
        render_scene_frame(
            spec, sc, max(0.0, scene_durs[idx] - 0.5), scene_durs[idx], W, H,
            word_timings=all_word_timings[idx], prop_delays=all_prop_delays[idx], label_delay=all_label_delays[idx],
            check_layout=True,
        )
    print(f"Layout, richness & sync checks passed for all {len(spec['scenes'])} scenes.")

    ffmpeg_log = open(work_dir / "ffmpeg.log", "wb")
    proc = subprocess.Popen(
        [
            "ffmpeg", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{W}x{H}", "-pix_fmt", "rgb24", "-r", str(FPS), "-i", "-",
            "-i", str(mix_wav),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_log,
    )
    dissolve_frames = int(0.25 * FPS)
    prev_last = None
    for idx, sc in enumerate(spec["scenes"]):
        dur = scene_durs[idx]
        n_frames = int(round(dur * FPS))
        env = frame_rms_envs[idx]
        for f_idx in range(n_frames):
            amp = env[f_idx] if f_idx < len(env) else 0.0
            frame = render_scene_frame(
                spec, sc, f_idx / float(FPS), dur, W, H,
                word_timings=all_word_timings[idx],
                mouth_amp=amp,
                prop_delays=all_prop_delays[idx],
                label_delay=all_label_delays[idx],
            )
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
    print("Rendered ->", out_path)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Render boldline-explainer JSON script to MP4 or preview grid.")
    p.add_argument("script", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("output.mp4"))
    p.add_argument("--preview", action="store_true", help="Render instant 1-frame-per-scene contact sheet JPG instead of MP4")
    p.add_argument("--allow-simple", action="store_true", help="Bypass visual richness gate")
    args = p.parse_args()
    asyncio.run(build(args.script, args.output, args.preview, args.allow_simple))
