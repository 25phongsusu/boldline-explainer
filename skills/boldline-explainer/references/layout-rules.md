# Layout & Zero-Overlap Rules

All scenes MUST obey these rules and pass `assert_no_overlaps` before rendering:

1. **Vertical Safe Zones (`9:16` / `720×1280`)**:
   - **Title zone**: `y = 100..210` (never place labels or polaroids above `y = 220`).
   - **Stage / Action zone**: `y = 240..940` (characters, polaroids, arrows, pills).
   - **Subtitle zone**: `y = 980..1220` (never place pills or polaroids below `y = 940`).
2. **Horizontal Safe Zones (`16:9` / `1280×720`)**:
   - **Title zone**: `y = 55..135`.
   - **Stage zone**: `y = 150..560`.
   - **Subtitle zone**: `y = 580..690`.
3. **Opaque Stamps Only**:
   - When drawing a stamp (`GIẢ MẠO`, `ĐÃ GỠ BỎ`, etc.) over a polaroid or UI card, **always** draw an opaque `#ffffff` rectangle behind the stamp text and keep any card headline above `sy0` and CTA button below `sy1`.
4. **Dynamic Pill Widths**:
   - Never hardcode pill width; compute `half_w = max(90, text_width // 2 + 28)` from `draw.textbbox()`.
5. **Pre-Flight Collision Check**:
   - Register every text block, pill, card, and arrow in `tracker` and run `assert_no_overlaps(scene_id, tracker)` at each scene's peak timestamp before invoking `ffmpeg`.
