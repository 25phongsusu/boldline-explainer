import argparse
import asyncio
import hashlib
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
from PIL import Image
from playwright.async_api import async_playwright

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def estimate_word_timings(text: str, audio_dur: float) -> list[dict]:
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
    text_sig = hashlib.sha256(f"{voice}|{lang}|{sc['text']}".encode("utf-8")).hexdigest()[:10]
    mp3_path = work_dir / f"scene_{sc['id']}_{text_sig}.mp3"
    wav_path = work_dir / f"scene_{sc['id']}_{text_sig}.wav"
    words_path = work_dir / f"scene_{sc['id']}_{text_sig}_words.json"

    if wav_path.exists() and wav_path.stat().st_size > 1000 and words_path.exists():
        with wave.open(str(wav_path), "rb") as w:
            return wav_path, w.getnframes() / float(w.getframerate()), json.loads(words_path.read_text(encoding="utf-8"))

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
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )

    with wave.open(str(wav_path), "rb") as w:
        dur = w.getnframes() / float(w.getframerate())

    if not word_timings:
        word_timings = estimate_word_timings(sc["text"], dur)
    words_path.write_text(json.dumps(word_timings, ensure_ascii=False), encoding="utf-8")
    return wav_path, dur, word_timings


def resolve_trigger_delay(item: dict, word_timings: list[dict], voice_offset: float = 0.15) -> float:
    tw = item.get("trigger_word", "").strip().lower()
    if tw and word_timings:
        for wt in word_timings:
            clean = re.sub(r"[^\w\s]", "", wt["word"].lower())
            if tw in clean or (clean in tw and len(clean) >= 2):
                return round(voice_offset + wt["start"], 3)
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


HTML_SHELL = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    width: __W__px; height: __H__px; overflow: hidden;
    font-family: 'Segoe UI', Arial, sans-serif;
    background: #18181b;
  }
  svg#stage { width: __W__px; height: __H__px; display: block; }
  .title-text {
    font-size: 50px; font-weight: 900; fill: #ef4444;
    stroke: #ffffff; stroke-width: 9px; paint-order: stroke fill;
    text-anchor: middle; letter-spacing: 0.5px;
  }
  .top-text {
    font-size: 30px; font-weight: 800; fill: #fef08a;
    stroke: #000000; stroke-width: 6px; paint-order: stroke fill;
    text-anchor: middle;
  }
  .sub-line {
    font-size: 29px; font-weight: 800;
    stroke: #000000; stroke-width: 7px; paint-order: stroke fill;
    text-anchor: middle;
  }
</style>
</head>
<body>
<svg id="stage" viewBox="0 0 __W__ __H__" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="softShadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="6" stdDeviation="5" flood-color="#000000" flood-opacity="0.22"/>
    </filter>
    <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#7dd3fc"/><stop offset="100%" stop-color="#e0f2fe"/>
    </linearGradient>
    <linearGradient id="wallCream" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#f3ead2"/><stop offset="100%" stop-color="#e5d6b0"/>
    </linearGradient>
    <linearGradient id="spotCone" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#fffbeb" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="#fef3c7" stop-opacity="0.05"/>
    </linearGradient>
    <radialGradient id="darkSpot" cx="50%" cy="45%" r="55%">
      <stop offset="0%" stop-color="#2a2e39"/><stop offset="100%" stop-color="#14161b"/>
    </radialGradient>
  </defs>
  <g id="cam"></g>
  <g id="ui"></g>
</svg>
<script>
function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function bgSVG(kind, W, H, lt) {
  const fy = Math.round(H * 0.735);
  if (kind === 'dark_card') {
    const sw = 18 * Math.sin(lt * 1.4);
    return `
      <rect width="${W}" height="${H}" fill="url(#darkSpot)"/>
      <polygon points="${120+sw},0 ${360+sw},0 540,${H} 210,${H}" fill="#ffffff" opacity="0.035"/>
      <ellipse cx="${W/2}" cy="${H*0.69}" rx="210" ry="38" fill="#000000" opacity="0.35"/>
    `;
  }
  if (kind === 'forest_hills') {
    return `
      <rect width="${W}" height="${H}" fill="url(#skyGrad)"/>
      <g opacity="0.9">
        <ellipse cx="140" cy="180" rx="68" ry="28" fill="#ffffff"/>
        <ellipse cx="175" cy="172" rx="45" ry="26" fill="#ffffff"/>
        <ellipse cx="560" cy="230" rx="75" ry="30" fill="#ffffff"/>
      </g>
      <path d="M-40,${H*0.72} Q${W*0.3},${H*0.52} ${W*0.78},${H*0.72} L${W*0.78},${H} L-40,${H} Z" fill="#4ade80" stroke="#166534" stroke-width="5"/>
      <path d="M${W*0.22},${H*0.74} Q${W*0.72},${H*0.50} ${W+40},${H*0.74} L${W+40},${H} L${W*0.22},${H} Z" fill="#22c55e" stroke="#15803d" stroke-width="5"/>
      ${[105, 615].map(px => `
        <g filter="url(#softShadow)">
          <rect x="${px-10}" y="${H*0.62}" width="20" height="70" fill="#78350f" stroke="#111827" stroke-width="4"/>
          <polygon points="${px},${H*0.39} ${px-58},${H*0.63} ${px+58},${H*0.63}" fill="#15803d" stroke="#111827" stroke-width="4" stroke-linejoin="round"/>
          <polygon points="${px},${H*0.34} ${px-44},${H*0.52} ${px+44},${H*0.52}" fill="#16a34a" stroke="#111827" stroke-width="4" stroke-linejoin="round"/>
        </g>
      `).join('')}
      <circle cx="190" cy="${H*0.80}" r="6" fill="#fde047"/><circle cx="520" cy="${H*0.78}" r="6" fill="#fb7185"/>
    `;
  }
  let wall = '';
  if (kind === 'kids_room') {
    wall = `
      <rect width="${W}" height="${fy}" fill="#fef9c3"/>
      <!-- Hanging lamp -->
      <line x1="${W/2}" y1="0" x2="${W/2}" y2="75" stroke="#111827" stroke-width="4"/>
      <path d="M${W/2-45},115 Q${W/2},65 ${W/2+45},115 Z" fill="#fb923c" stroke="#111827" stroke-width="4"/>
      <!-- Chalkboard -->
      <g filter="url(#softShadow)">
        <rect x="115" y="220" width="490" height="215" rx="14" fill="#14532d" stroke="#78350f" stroke-width="10"/>
        <text x="360" y="310" text-anchor="middle" font-size="36" font-weight="900" fill="#fde047">A  B  C  •  TOPO</text>
        <text x="360" y="365" text-anchor="middle" font-size="22" font-weight="700" fill="#bbf7d0">Learn &amp; Play Together!</text>
        <rect x="290" y="422" width="60" height="14" rx="3" fill="#f97316" stroke="#111827" stroke-width="2"/>
      </g>
      <!-- Toy shelf on left -->
      <rect x="35" y="540" width="120" height="14" fill="#a16207" stroke="#111827" stroke-width="3"/>
      <rect x="48" y="498" width="22" height="42" fill="#ef4444" stroke="#111827" stroke-width="3"/>
      <rect x="74" y="505" width="22" height="35" fill="#3b82f6" stroke="#111827" stroke-width="3"/>
      <circle cx="122" cy="522" r="18" fill="#facc15" stroke="#111827" stroke-width="3"/>
    `;
  } else if (kind === 'spotlight') {
    wall = `
      <rect width="${W}" height="${fy}" fill="#292524"/>
      <polygon points="220,0 500,0 670,${fy} 50,${fy}" fill="url(#spotCone)"/>
    `;
  } else {
    // living_office with arched window, skyline, bookshelf, desk, rug
    wall = `
      <rect width="${W}" height="${fy}" fill="url(#wallCream)"/>
      <!-- Arched window -->
      <g>
        <rect x="155" y="210" width="410" height="320" rx="16" fill="#93c5fd" stroke="#5c4033" stroke-width="10"/>
        <rect x="180" y="355" width="70" height="165" fill="#fca5a5" stroke="#374151" stroke-width="3"/>
        <rect x="260" y="310" width="85" height="210" fill="#fde68a" stroke="#374151" stroke-width="3"/>
        <rect x="358" y="370" width="80" height="150" fill="#cbd5e1" stroke="#374151" stroke-width="3"/>
        <rect x="450" y="330" width="85" height="190" fill="#fdba74" stroke="#374151" stroke-width="3"/>
        <line x1="360" y1="210" x2="360" y2="530" stroke="#5c4033" stroke-width="7"/>
        <line x1="155" y1="370" x2="565" y2="370" stroke="#5c4033" stroke-width="7"/>
        <rect x="142" y="202" width="42" height="338" rx="6" fill="#166534" stroke="#111827" stroke-width="4"/>
        <rect x="536" y="202" width="42" height="338" rx="6" fill="#166534" stroke="#111827" stroke-width="4"/>
      </g>
      <!-- Desk -->
      <g filter="url(#softShadow)">
        <rect x="50" y="${fy-98}" width="620" height="106" rx="8" fill="#9a6b3f" stroke="#111827" stroke-width="5"/>
        <rect x="85" y="${fy-76}" width="210" height="62" rx="4" fill="#855a33" stroke="#111827" stroke-width="3"/>
        <circle cx="190" cy="${fy-45}" r="6" fill="#facc15" stroke="#111827" stroke-width="2"/>
        <rect x="425" y="${fy-76}" width="210" height="62" rx="4" fill="#855a33" stroke="#111827" stroke-width="3"/>
        <circle cx="530" cy="${fy-45}" r="6" fill="#facc15" stroke="#111827" stroke-width="2"/>
      </g>
    `;
  }
  let planks = '';
  for (let y = fy + 10; y < H; y += 44) {
    planks += `<line x1="0" y1="${y}" x2="${W}" y2="${y}" stroke="#7c5530" stroke-width="3"/>`;
  }
  const rug = (kind !== 'spotlight') ? `<ellipse cx="${W/2}" cy="${fy+115}" rx="250" ry="62" fill="#a32a29" stroke="#eab308" stroke-width="5" opacity="0.92"/>` : '';
  return `
    ${wall}
    <rect x="0" y="${fy-14}" width="${W}" height="24" fill="#8c6d46" stroke="#111827" stroke-width="4"/>
    <rect x="0" y="${fy+10}" width="${W}" height="${H-fy}" fill="#b58453"/>
    ${planks}
    ${rug}
  `;
}

function characterSVG(cfg, actor, lt, mouthAmp, W, H) {
  const cx = actor.x * W;
  const s = actor.scale || 1.0;
  const speaking = actor.speaking && mouthAmp > 0.12;
  const bob = speaking ? 3.5 * mouthAmp * Math.sin(lt * 9) : 1.5 * Math.sin(lt * 2);
  const cy = actor.y * H + bob;

  // Resolve emotion_timeline if present
  let mood = actor.mood || 'normal';
  let armPose = actor.arm_pose || 'down';
  if (actor.emotion_timeline) {
    for (const ev of actor.emotion_timeline) {
      if (lt >= ev.at) {
        if (ev.mood) mood = ev.mood;
        if (ev.arm_pose) armPose = ev.arm_pose;
      }
    }
  }

  const shirt = cfg.shirt_color || '#0d9488';
  const hair = cfg.hair_color || '#1f2937';
  const wave = speaking ? 11 * mouthAmp * Math.sin(lt * 8) : 3 * Math.sin(lt * 2.5);
  const blink = (Math.floor(lt * 10) % 34) === 0;

  let arms = '';
  if (armPose === 'point_right') {
    arms = `
      <path d="M-40,22 Q-65,50 -62,74" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="-62" cy="76" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
      <path d="M40,22 Q78,12 ${108},${8+wave}" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="108" cy="${8+wave}" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
    `;
  } else if (armPose === 'raised') {
    arms = `
      <path d="M-40,22 Q-72,-4 -84,${-26+wave}" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="-84" cy="${-26+wave}" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
      <path d="M40,22 Q72,-4 84,${-26-wave}" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="84" cy="${-26-wave}" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
    `;
  } else {
    arms = `
      <path d="M-40,22 Q-64,52 -62,${78+wave*0.4}" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="-62" cy="${78+wave*0.4}" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
      <path d="M40,22 Q64,52 62,${78-wave*0.4}" fill="none" stroke="#111827" stroke-width="7" stroke-linecap="round"/>
      <circle cx="62" cy="${78-wave*0.4}" r="10" fill="#fde6d8" stroke="#111827" stroke-width="3.5"/>
    `;
  }

  let eyes = '';
  if (blink) {
    eyes = `
      <path d="M-34,-58 Q-24,-52 -14,-58" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round"/>
      <path d="M14,-58 Q24,-52 34,-58" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round"/>
    `;
  } else if (mood === 'excited' || mood === 'shocked') {
    eyes = `
      <circle cx="-24" cy="-60" r="12" fill="#ffffff" stroke="#111827" stroke-width="3.5"/>
      <circle cx="-24" cy="-60" r="5" fill="#111827"/><circle cx="-26" cy="-63" r="2" fill="#ffffff"/>
      <circle cx="24" cy="-60" r="12" fill="#ffffff" stroke="#111827" stroke-width="3.5"/>
      <circle cx="24" cy="-60" r="5" fill="#111827"/><circle cx="22" cy="-63" r="2" fill="#ffffff"/>
    `;
  } else {
    eyes = `
      <circle cx="-24" cy="-58" r="7.5" fill="#111827"/><circle cx="-26" cy="-61" r="2.5" fill="#ffffff"/>
      <circle cx="24" cy="-58" r="7.5" fill="#111827"/><circle cx="22" cy="-61" r="2.5" fill="#ffffff"/>
    `;
  }

  const glasses = cfg.glasses ? `
    <circle cx="-24" cy="-58" r="18" fill="none" stroke="#374151" stroke-width="4"/>
    <circle cx="24" cy="-58" r="18" fill="none" stroke="#374151" stroke-width="4"/>
    <line x1="-6" y1="-58" x2="6" y2="-58" stroke="#374151" stroke-width="4"/>
  ` : '';

  let mouth = '';
  if (speaking) {
    const mh = Math.max(4, Math.round(12 * Math.min(1, mouthAmp * 1.45)));
    mouth = `<ellipse cx="0" cy="-26" rx="12" ry="${mh}" fill="#991b1b" stroke="#111827" stroke-width="3.5"/>`;
  } else if (mood === 'happy' || mood === 'excited') {
    mouth = `<path d="M-18,-30 Q0,-12 18,-30" fill="none" stroke="#111827" stroke-width="4.5" stroke-linecap="round"/>`;
  } else {
    mouth = `<line x1="-14" y1="-26" x2="14" y2="-26" stroke="#111827" stroke-width="4" stroke-linecap="round"/>`;
  }

  return `
    <g transform="translate(${cx},${cy}) scale(${s})" filter="url(#softShadow)">
      <!-- Floor shadow -->
      <ellipse cx="0" cy="154" rx="46" ry="11" fill="#000000" opacity="0.24"/>
      <!-- Legs & shoes -->
      <line x1="-18" y1="95" x2="-18" y2="145" stroke="#111827" stroke-width="8" stroke-linecap="round"/>
      <line x1="18" y1="95" x2="18" y2="145" stroke="#111827" stroke-width="8" stroke-linecap="round"/>
      <ellipse cx="-22" cy="147" rx="16" ry="9" fill="#111827"/>
      <ellipse cx="22" cy="147" rx="16" ry="9" fill="#111827"/>
      <!-- Torso -->
      <path d="M-42,12 Q0,4 42,12 L54,98 Q0,104 -54,98 Z" fill="${shirt}" stroke="#111827" stroke-width="5" stroke-linejoin="round"/>
      <polygon points="-18,10 0,28 18,10" fill="#ffffff" stroke="#111827" stroke-width="3.5"/>
      ${arms}
      <!-- Head -->
      <circle cx="0" cy="-55" r="66" fill="#fde6d8" stroke="#111827" stroke-width="5.5"/>
      <!-- Hair with cute bangs -->
      <path d="M-68,-58 C-70,-142 70,-142 68,-58 C45,-82 22,-78 0,-84 C-22,-78 -45,-82 -68,-58 Z" fill="${hair}" stroke="#111827" stroke-width="5" stroke-linejoin="round"/>
      <!-- Rosy cheeks -->
      <ellipse cx="-40" cy="-38" rx="11" ry="6.5" fill="#fca5a5" opacity="0.85"/>
      <ellipse cx="40" cy="-38" rx="11" ry="6.5" fill="#fca5a5" opacity="0.85"/>
      ${eyes}
      ${glasses}
      ${mouth}
    </g>
  `;
}

function propSVG(prop, delay, lt, W, H) {
  if (lt < delay) return '';
  const dt = lt - delay;
  const pop = 1.0 + 0.14 * Math.sin(Math.min(dt, 0.28) / 0.28 * Math.PI);
  const cx = prop.x * W, cy = prop.y * H;
  const k = prop.kind;
  let inner = '';
  if (k === 'topo_phone') {
    inner = `
      <g transform="rotate(-6)">
        <rect x="-46" y="-68" width="92" height="136" rx="16" fill="#1e293b" stroke="#111827" stroke-width="5"/>
        <rect x="-38" y="-56" width="76" height="108" rx="10" fill="#38bdf8" stroke="#0284c7" stroke-width="2.5"/>
        <rect x="-30" y="-44" width="60" height="28" rx="6" fill="#ffffff"/>
        <text x="0" y="-24" text-anchor="middle" font-size="18" font-weight="900" fill="#0284c7">TOPO</text>
        <rect x="-28" y="-6" width="24" height="24" rx="4" fill="#facc15" stroke="#111827" stroke-width="2.5"/>
        <text x="-16" y="12" text-anchor="middle" font-size="16" font-weight="900" fill="#111827">A</text>
        <rect x="4" y="-6" width="24" height="24" rx="4" fill="#4ade80" stroke="#111827" stroke-width="2.5"/>
        <text x="16" y="12" text-anchor="middle" font-size="16" font-weight="900" fill="#111827">B</text>
        <polygon points="0,24 5,34 16,35 8,42 10,52 0,47 -10,52 -8,42 -16,35 -5,34" fill="#fde047" stroke="#111827" stroke-width="2"/>
      </g>
    `;
  } else if (k === 'abc_blocks') {
    inner = `
      <g>
        <rect x="-52" y="2" width="48" height="48" rx="8" fill="#f87171" stroke="#111827" stroke-width="4.5"/>
        <text x="-28" y="36" text-anchor="middle" font-size="28" font-weight="900" fill="#ffffff">A</text>
        <rect x="4" y="2" width="48" height="48" rx="8" fill="#4ade80" stroke="#111827" stroke-width="4.5"/>
        <text x="28" y="36" text-anchor="middle" font-size="28" font-weight="900" fill="#ffffff">B</text>
        <rect x="-24" y="-48" width="48" height="48" rx="8" fill="#facc15" stroke="#111827" stroke-width="4.5"/>
        <text x="0" y="-14" text-anchor="middle" font-size="28" font-weight="900" fill="#111827">C</text>
      </g>
    `;
  } else if (k === 'mic_speak') {
    const pulse = 1 + 0.1 * Math.sin(lt * 8);
    inner = `
      <g>
        <circle cx="0" cy="-12" r="${52*pulse}" fill="none" stroke="#38bdf8" stroke-width="4" stroke-dasharray="10 8" opacity="0.7"/>
        <rect x="-20" y="-48" width="40" height="58" rx="20" fill="#60a5fa" stroke="#111827" stroke-width="4.5"/>
        <line x1="-12" y1="-28" x2="12" y2="-28" stroke="#111827" stroke-width="3"/>
        <line x1="-12" y1="-16" x2="12" y2="-16" stroke="#111827" stroke-width="3"/>
        <path d="M-32,-8 A32,32 0 0,0 32,-8" fill="none" stroke="#111827" stroke-width="5" stroke-linecap="round"/>
        <line x1="0" y1="24" x2="0" y2="50" stroke="#111827" stroke-width="5"/>
        <line x1="-22" y1="50" x2="22" y2="50" stroke="#111827" stroke-width="5" stroke-linecap="round"/>
      </g>
    `;
  } else if (k === 'trophy_star') {
    inner = `
      <g>
        <path d="M-36,-44 L36,-44 L28,4 Q0,24 -28,4 Z" fill="#facc15" stroke="#111827" stroke-width="4.5"/>
        <path d="M-36,-34 Q-56,-34 -52,-12 Q-48,4 -30,2" fill="none" stroke="#111827" stroke-width="4.5"/>
        <path d="M36,-34 Q56,-34 52,-12 Q48,4 30,2" fill="none" stroke="#111827" stroke-width="4.5"/>
        <rect x="-10" y="14" width="20" height="24" fill="#eab308" stroke="#111827" stroke-width="4"/>
        <rect x="-28" y="38" width="56" height="14" rx="4" fill="#78350f" stroke="#111827" stroke-width="4"/>
        <polygon points="0,-32 6,-18 20,-17 9,-8 13,6 0,-2 -13,6 -9,-8 -20,-17 -6,-18" fill="#ffffff" stroke="#111827" stroke-width="2"/>
      </g>
    `;
  } else if (k === 'parent_kid_heart') {
    inner = `
      <g>
        <path d="M0,42 C-58,8 -58,-44 -22,-44 C-6,-44 0,-30 0,-22 C0,-30 6,-44 22,-44 C58,-44 58,8 0,42 Z" fill="#f43f5e" stroke="#111827" stroke-width="5"/>
        <circle cx="-18" cy="-12" r="5" fill="#ffffff" opacity="0.65"/>
      </g>
    `;
  }
  return `<g transform="translate(${cx},${cy}) scale(${pop})" filter="url(#softShadow)">${inner}</g>`;
}

window.renderState = function(st) {
  const { W, H, sc, lt, dur, chars, words, mouthAmp, propDelays, labelDelay } = st;
  const bg = sc.background || 'living_office';
  const zoom = (bg === 'dark_card') ? 1.0 : (1.0 + 0.045 * Math.min(1, lt / Math.max(1, dur)));
  const zx = W * (1 - zoom) / 2, zy = H * (1 - zoom) / 2;

  let camHTML = bgSVG(bg, W, H, lt);
  for (const actor of (sc.actors || [])) {
    const cfg = chars[actor.id] || {};
    camHTML += characterSVG(cfg, actor, lt, actor.speaking ? mouthAmp : 0, W, H);
  }
  (sc.props || []).forEach((p, idx) => {
    camHTML += propSVG(p, propDelays[idx] ?? (p.delay || 0), lt, W, H);
  });
  document.getElementById('cam').setAttribute('transform', `translate(${zx},${zy}) scale(${zoom})`);
  document.getElementById('cam').innerHTML = camHTML;

  let uiHTML = '';
  if (bg === 'dark_card') {
    const ty = H * 0.37;
    if (sc.top_text) uiHTML += `<text x="${W/2}" y="${ty}" class="top-text">${esc(sc.top_text)}</text>`;
    uiHTML += `<text x="${W/2}" y="${ty+82}" class="title-text">${esc(sc.title)}</text>`;
    uiHTML += `<line x1="${W*0.16}" y1="${ty+115}" x2="${W*0.84}" y2="${ty+115}" stroke="#facc15" stroke-width="7" stroke-linecap="round"/>`;
    if (sc.badge_text) {
      uiHTML += `<rect x="${W/2-160}" y="${ty+162}" width="320" height="58" rx="16" fill="#0ea5e9" stroke="#ffffff" stroke-width="4" filter="url(#softShadow)"/>`;
      uiHTML += `<text x="${W/2}" y="${ty+200}" text-anchor="middle" font-size="25" font-weight="800" fill="#ffffff">${esc(sc.badge_text)}</text>`;
    }
  } else {
    const ty = H * 0.115;
    uiHTML += `<text x="${W/2}" y="${ty}" class="title-text">${esc(sc.title)}</text>`;
    uiHTML += `<line x1="${W*0.16}" y1="${ty+30}" x2="${W*0.84}" y2="${ty+30}" stroke="#facc15" stroke-width="7" stroke-linecap="round"/>`;
  }

  if (sc.label && lt >= (labelDelay ?? (sc.label.delay || 0))) {
    const lx = sc.label.x * W, ly = sc.label.y * H;
    const pw = Math.max(190, sc.label.text.length * 14 + 48);
    uiHTML += `
      <g filter="url(#softShadow)">
        <rect x="${lx-pw/2}" y="${ly-26}" width="${pw}" height="52" rx="14" fill="#ffffff" stroke="#111827" stroke-width="4"/>
        <text x="${lx}" y="${ly+9}" text-anchor="middle" font-size="24" font-weight="800" fill="#111827">${esc(sc.label.text)}</text>
      </g>
    `;
  }

  // Karaoke subtitles
  const allWords = sc.text.split(/\\s+/);
  const lines = [];
  let cur = [];
  for (let i = 0; i < allWords.length; i++) {
    const test = cur.map(x => x.w).concat([allWords[i]]).join(' ');
    if (test.length * 15.5 > W - 90 && cur.length > 0) {
      lines.push(cur); cur = [{i, w: allWords[i]}];
    } else {
      cur.push({i, w: allWords[i]});
    }
  }
  if (cur.length) lines.push(cur);

  const spT = lt - 0.15;
  let activeIdx = -1;
  for (let i = 0; i < words.length; i++) {
    if (words[i].start <= spT && spT <= words[i].end + 0.08) { activeIdx = i; break; }
  }
  const subBase = H * 0.855 - (lines.length - 1) * 40;
  lines.forEach((row, rIdx) => {
    const y = subBase + rIdx * 42;
    const tspans = row.map(item => `<tspan fill="${item.i === activeIdx ? '#fde047' : '#ffffff'}">${esc(item.w)} </tspan>`).join('');
    uiHTML += `<text x="${W/2}" y="${y}" class="sub-line">${tspans}</text>`;
  });

  document.getElementById('ui').innerHTML = uiHTML;
};
</script>
</body>
</html>"""


async def build(script_path: Path, out_path: Path, preview_only: bool):
    spec = json.loads(script_path.read_text(encoding="utf-8"))
    aspect = spec.get("aspect", "9:16")
    W, H = (1280, 720) if aspect == "16:9" else (720, 1280)
    FPS = int(spec.get("fps", 30))
    voice = spec.get("voice", "vi-VN-HoaiMyNeural")
    lang = spec.get("lang", "vi")

    html_content = HTML_SHELL.replace("__W__", str(W)).replace("__H__", str(H))

    if preview_only:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=EDGE_PATH, headless=True)
            page = await browser.new_page(viewport={"width": W, "height": H})
            await page.set_content(html_content)
            scenes = spec["scenes"]
            cols = min(5, len(scenes))
            rows = math.ceil(len(scenes) / cols)
            tw, th = 240, int(240 * H / W)
            grid = Image.new("RGB", (cols * tw, rows * th), "#111827")
            for idx, sc in enumerate(scenes):
                await page.evaluate("window.renderState", {
                    "W": W, "H": H, "sc": sc, "lt": 2.5, "dur": 5.0,
                    "chars": spec.get("characters", {}), "words": [], "mouthAmp": 0.4,
                    "propDelays": [0.0] * len(sc.get("props", [])), "labelDelay": 0.0,
                })
                png = await page.screenshot(type="png")
                from io import BytesIO
                im = Image.open(BytesIO(png)).convert("RGB").resize((tw, th), Image.Resampling.LANCZOS)
                r, c = divmod(idx, cols)
                grid.paste(im, (c * tw, r * th))
            grid.save(out_path, quality=90)
            await browser.close()
        print(f"SVG preview grid saved -> {out_path}")
        return

    work_dir = out_path.parent / ".build_cache" / out_path.stem
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
        p_delays = []
        for prop in sc.get("props", []):
            d = resolve_trigger_delay(prop, word_timings)
            p_delays.append(d)
            if prop.get("trigger_word"):
                sfx_events.append((cur_sample + int(d * sr), prop.get("sfx", "pop")))
        all_prop_delays.append(p_delays)
        all_label_delays.append(resolve_trigger_delay(sc["label"], word_timings) if sc.get("label") else None)
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
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((mix * 32767.0).astype(np.int16).tobytes())

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

    from io import BytesIO
    dissolve_frames = int(0.25 * FPS)
    prev_last = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=EDGE_PATH, headless=True)
        page = await browser.new_page(viewport={"width": W, "height": H})
        await page.set_content(html_content)
        for idx, sc in enumerate(spec["scenes"]):
            dur = scene_durs[idx]
            n_frames = int(round(dur * FPS))
            env = frame_rms_envs[idx]
            for f_idx in range(n_frames):
                await page.evaluate("window.renderState", {
                    "W": W, "H": H, "sc": sc, "lt": f_idx / float(FPS), "dur": dur,
                    "chars": spec.get("characters", {}), "words": all_word_timings[idx],
                    "mouthAmp": env[f_idx] if f_idx < len(env) else 0.0,
                    "propDelays": all_prop_delays[idx], "labelDelay": all_label_delays[idx],
                })
                png = await page.screenshot(type="png")
                frame = Image.open(BytesIO(png)).convert("RGB")
                if prev_last is not None and f_idx < dissolve_frames:
                    frame = Image.blend(prev_last, frame, (f_idx + 1) / float(dissolve_frames + 1))
                proc.stdin.write(frame.tobytes())
                if f_idx == n_frames - 1:
                    prev_last = frame
        await browser.close()

    proc.stdin.close()
    ret = proc.wait()
    ffmpeg_log.close()
    if ret != 0:
        raise RuntimeError((work_dir / "ffmpeg.log").read_text(encoding="utf-8", errors="replace"))
    print("SVG Rendered ->", out_path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="HTML/SVG renderer for boldline-explainer.")
    ap.add_argument("script", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("output.mp4"))
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    asyncio.run(build(args.script, args.output, args.preview))
