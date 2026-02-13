# Street Counters Specs

Reference spec for the `Street` view dual-dial cluster.
Last updated: 2026-02-12

## Scope

- Left dial: RPM / gear.
- Right dial: speed (numeric center + non-linear km/h scale).
- Shared ring style and needle language across both dials.

## Data Inputs

- Left dial value: `RPM` (`signals.RPM` fallback `raw.RPM`).
- Right dial value: `Speed` (`signals.Speed` fallback `raw.Speed`).
- Gear text: `Gear` (`D-` when unavailable/non-positive).

## Angular Geometry

- Global dial arc: `270deg`.
- Angle mapping helper: `dialAngle01(t) = 135 + 270*t`.
- `t=0.5` is vertical top.

## Left Dial (RPM)

- Main needle percentage: linear `pct(rpm, 0, 8000)`.
- Labels: `0..8` over full 270deg arc.
- Red numbers from `7`.
- Tick ring:
  - 33 total divisions.
  - Major tick every 4th.

## Right Dial (Speed)

- Center value: numeric only (no `km/h` label).
- Scale domain: `0..300 km/h`.
- Non-linear mapping (required behavior):
  - `0..100` uses first half of arc.
  - `100..300` uses second half of arc.
  - Therefore `100 km/h` is at vertical top.
- Major labels:
  - `0, 20, 40, 60, 80, 100, 140, 180, 220, 260, 300`.
- Minor ticks:
  - 3 minor ticks between each pair of major labels.

## Needles

- Red active needle on both dials:
  - Same geometry (triangular profile).
  - Base moved outward: `translate(112%, -50%)`.
  - Rotation law (both dials): `rotate(135deg + var(--p) * 2.7deg)`.
- Gray shadow needle (both dials):
  - Same design as active needle.
  - Represents rolling maximum over recent window (not an average).
  - Window length: `SHADOW_WINDOW_MS = 1000` (1 second).
  - Hidden when max approximately equals current value:
    - RPM threshold: `max <= current + 0.5`.
    - Speed threshold: `max <= current + 0.2`.
  - Visual style: gray, blurred, semi-transparent (`opacity: .64`, `blur(1.9px)`).

## Ring / Center Styling

- Outer ring: widened, metallic gray border.
- Inner center ring: widened border.
- Dial center: radial dark gradient with soft central bloom (circular, no square patch).
- Center typography (`.dial-gear`):
  - Audi-type family.
  - Light gray fill (`#e8edf5`).
  - Embossed effect via stroke + shadow.

## Blur / Softness

- Slight softening applied to ring/tick layers:
  - `dial-ring`: `blur(.25px)`.
  - `dial-ring-outer`: `blur(.2px)`.
  - `dial-ticks`: `blur(.2px)`.

## Warning Banners (Street)

- Top warning banner:
  - Trapezoid style with 3 straight segments + 2 rounded tangent joins.
  - Static content set for Street: left/right indicator, engine, master warning, parking brake.
  - Icon spacing and size tuned for normal + fullscreen variants.
- Bottom banner:
  - Geometric mirror of top banner (shape only, no icon content).
  - Enlarged vertically (x1.5 on trace) to match retained visual style.
  - Fullscreen positioning uses percentage-based bottom offset (responsive to frame height).

## Warning Icons / Glow

- Icons use local assets from `static/warning_icons/`.
- PNG black backgrounds were converted to transparent alpha.
- Active warnings use subtle contour glow (yellow/red/green by icon type), not a circular disk halo.

## Fullscreen Street Mode

- Dedicated mode available via URL:
  - `/webui/index.html?fullscreen=street`
- In fullscreen:
  - only the Street dashboard is shown.
  - top/tabs/debug shell are hidden.
  - dedicated icon sizing/spacing rules apply to the top warning banner.
  - bottom warning banner uses fullscreen-specific responsive positioning.

## Center Panel GPS Map

- The center rectangle can host a live map (`OpenStreetMap` embed).
- Activation:
  - enabled by default.
  - can be disabled with query flag: `?map=0`.
- Optional file-based temporary GPS source:
  - file path: `static/gps_position.json`.
  - polling period: about `2s`.
  - if `enabled=true` and coordinates are valid, this source has priority over payload/browser GPS.
  - route playback is supported:
    - `points`: array of coordinates (`lat/lon`) used as timeline.
    - `tick_s`: seconds between points (for example `1` => one point per second).
    - `loop`: replay from first point when the end is reached.
  - expected shape:
    - `enabled` (boolean)
    - `label` (string, optional)
    - `lat` / `lon` (number)
    - `accuracy_m` (number, optional)
    - or route mode with `points[]`, `tick_s`, `loop`
- GPS source priority:
  - file source (`static/gps_position.json`) when enabled/valid.
  - `meta.gps` (`lat/lon[/accuracy]`) if present in WS payload.
  - fallback to browser geolocation API (`navigator.geolocation`).
- Map updates are throttled by coordinate key (`~5 decimals`) to avoid excessive reloads.
- On-map overlay shows source + current latitude/longitude + status text.

## Files of Record

- Behavior and layout: `static/views/street.js`.
- Visual styling: `static/styles/street.css`.
