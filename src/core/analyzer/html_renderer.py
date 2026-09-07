"""HTML report rendering — extracted from TelemetryAnalyzer._generate_html and _build_html_template."""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.analyzer._util import _optional_float
from src.utils.structured_logger import Component, log_debug


_VENDOR_DIR = Path(__file__).with_name("vendor")


def _load_vendor_script(filename: str) -> str:
    """Load a pinned chart dependency bundled with source and frozen builds."""
    return (_VENDOR_DIR / filename).read_text(encoding="utf-8")


def _escape_json_for_script(data_json: str) -> str:
    """Keep serialized JSON inside a JavaScript script data context."""
    return (
        data_json.replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("&", r"\u0026")
    )


async def render_html(
    data: Dict[str, Any],
    output_dir: str,
    output_prefix: Optional[str] = None,
) -> str:
    """Generate HTML report with full telemetry visualization."""
    prefix = output_prefix or datetime.now().strftime("%m-%d-%H-%M-%S")
    html_path = os.path.join(output_dir, f"telemetry_{prefix}.html")

    os.makedirs(output_dir, exist_ok=True)

    laps_json: List[Dict] = []
    for lap in data["laps"]:
        render_track = lap.get("canonical_track") or lap["track"]
        track_slim = [
            {
                "frame": pt["frame"],
                "x": round(_optional_float(pt.get("x")) or 0.0, 2),
                "z": round(_optional_float(pt.get("z")) or 0.0, 2),
                "speed": round(_optional_float(pt.get("speed")) or 0.0, 1),
                "brake": round(_optional_float(pt.get("brake")) or 0.0, 3),
                "gas": round(_optional_float(pt.get("gas")) or 0.0, 3),
                "gear": pt["gear"],
                "steer": round(_optional_float(pt.get("steer")) or 0.0, 6),
                "yaw_rate": round(_optional_float(pt.get("yaw_rate")) or 0.0, 6),
                "acc_g_x": round(_optional_float(pt.get("acc_g_x")) or 0.0, 6),
                "acc_g_z": round(_optional_float(pt.get("acc_g_z")) or 0.0, 6),
                "brake_temp_fl": round(_optional_float(pt.get("brake_temp_fl")) or 0.0, 2),
                "brake_temp_fr": round(_optional_float(pt.get("brake_temp_fr")) or 0.0, 2),
                "brake_temp_rl": round(_optional_float(pt.get("brake_temp_rl")) or 0.0, 2),
                "brake_temp_rr": round(_optional_float(pt.get("brake_temp_rr")) or 0.0, 2),
            }
            for pt in render_track
        ]
        corners_json = [
            {
                "id": c["id"],
                "name": c.get("name"),
                "start_frame": c["start_frame"],
                "end_frame": c["end_frame"],
                "apex_frame": c["apex_frame"],
                "apex_speed": round(c["apex_speed"], 1),
                "entry_speed": round(c["entry_speed"], 1),
                "exit_speed": round(c["exit_speed"], 1),
                "apex_x": round(c["apex_x"], 1),
                "apex_z": round(c["apex_z"], 1),
                "lap_pos": round(c["lap_pos"], 4),
            }
            for c in lap["corners"]
        ]
        laps_json.append({
            "lap_num": lap["lap_num"],
            "start_frame": lap["start_frame"],
            "end_frame": lap["end_frame"],
            "lap_time_s": round(lap["lap_time_s"], 3),
            "lap_time_str": lap["lap_time_str"],
            "max_speed": round(lap["max_speed"], 1),
            "avg_speed": round(lap["avg_speed"], 1),
            "fuel_used": (
                round(lap["fuel_used"], 3)
                if lap.get("fuel_used") is not None
                else None
            ),
            "is_valid": lap.get("is_valid", True),
            "confidence_label": lap.get("confidence_label"),
            "track": track_slim,
            "corners": corners_json,
        })

    ref_corners_json = [
        {
            "id": c["id"],
            "name": c.get("name"),
            "lap_pos": round(c["lap_pos"], 4),
        }
        for c in data["ref_corners"]
    ]

    corner_data_json = {}
    for cid, speeds in data["corner_data"].items():
        corner_data_json[str(cid)] = {str(k): v for k, v in speeds.items()}

    corner_speeds_json = {}
    for cid, speeds in data["corner_speeds"].items():
        corner_speeds_json[str(cid)] = {str(k): v for k, v in speeds.items()}

    data_json = json.dumps({
        "meta": data["meta"],
        "hz": data["hz"],
        "track_key": data["track_key"],
        "track_name": data["track_name"],
        "config_key": data["config_key"],
        "config_name": data["config_name"],
        "track_label": data["track_label"],
        "laps": laps_json,
        "best_lap_num": data["best_lap_num"],
        "reference_lap_num": data.get("reference_lap_num"),
        "comparison_lap_num": data.get("comparison_lap_num"),
        "comparison_available": data.get("comparison_available", False),
        "valid_lap_nums": data.get("valid_lap_nums", []),
        "analysis_mode": data.get("analysis_mode"),
        "analysis_confidence": data.get("analysis_confidence"),
        "analysis_notes": data.get("analysis_notes", []),
        "ref_corners": ref_corners_json,
        "corner_data": corner_data_json,
        "corner_speeds": corner_speeds_json,
    }, ensure_ascii=True)

    html_content = build_html_template(
        data_json,
        chart_js=_load_vendor_script("chart.umd.min.js"),
        annotation_js=_load_vendor_script("chartjs-plugin-annotation.min.js"),
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    log_debug(Component.ANALYZER, "Generated HTML report", path=html_path)
    return html_path


def build_html_template(
    data_json: str,
    *,
    chart_js: Optional[str] = None,
    annotation_js: Optional[str] = None,
) -> str:
    """Build the full HTML report template with all chart sections."""
    chart_js = chart_js or _load_vendor_script("chart.umd.min.js")
    annotation_js = annotation_js or _load_vendor_script(
        "chartjs-plugin-annotation.min.js"
    )
    data_json = _escape_json_for_script(data_json)
    template = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC Evo Lap Analysis</title>
<script>__CHART_JS__</script>
<script>__ANNOTATION_JS__</script>
<style>
  :root { --bg: #0d0d0f; --panel: #16181d; --border: #2a2d36; --text: #e0e2ea; --muted: #6b7280; --accent: #3b82f6; --green: #22c55e; --red: #ef4444; --orange: #f97316; --yellow: #eab308; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: var(--panel); border-bottom: 1px solid var(--border); padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 18px; font-weight: 600; letter-spacing: 0.02em; }
  header .sub { color: var(--muted); font-size: 12px; }
  .container { max-width: 1400px; margin: 0 auto; padding: 20px 24px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-bottom: 16px; }
  @media (max-width: 900px) { .grid-2, .grid-3 { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card h2 { font-size: 13px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
  .stat-row { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
  .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 130px; }
  .stat .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .stat .value { font-size: 22px; font-weight: 700; margin-top: 2px; }
  .notice { display: none; border: 1px solid rgba(249,115,22,0.55); background: rgba(249,115,22,0.10); border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; color: #fed7aa; line-height: 1.45; }
  .notice strong { color: #ffedd5; }
  .notice ul { margin: 8px 0 0 18px; }
  .lap-filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
  .lap-btn { background: var(--border); border: 1px solid transparent; border-radius: 6px; padding: 5px 13px; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--muted); transition: all 0.15s; }
  .lap-btn.active { border-color: currentColor; color: var(--text); }
  .lap-btn:hover { background: #2a2d3a; }
  canvas { max-width: 100%; }
  .track-wrap { position: relative; }
  #track-canvas { border-radius: 6px; cursor: crosshair; }
  #map-tooltip { position: absolute; background: rgba(13,15,20,0.95); border: 1px solid #3a3d4a; color: #e0e2ea; padding: 4px 10px; border-radius: 5px; font-size: 12px; font-weight: 600; pointer-events: none; display: none; white-space: nowrap; z-index: 10; }
  .corner-table { width: 100%; border-collapse: collapse; font-size: 13px; }
  .corner-table th { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
  .corner-table td { padding: 6px 10px; border-bottom: 1px solid #1e2028; }
  .corner-table tr:hover td { background: #1e2028; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; }
  .pill { display: inline-block; padding: 2px 7px; border-radius: 99px; font-size: 11px; background: var(--border); }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 10px; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .section-title { font-size: 15px; font-weight: 700; margin: 20px 0 10px; padding-left: 2px; }
  select { background: var(--border); border: 1px solid #3a3d4a; border-radius: 6px; padding: 5px 10px; color: var(--text); font-size: 12px; cursor: pointer; }
</style>
</head>
<body>
<header>
  <div>
    <h1>AC Evo &mdash; Lap Telemetry</h1>
    <div class="sub" id="session-info">Loading&hellip;</div>
  </div>
</header>
<div class="container">
  <!-- Summary stats -->
  <div id="render-error" style="display:none;background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.5);border-radius:8px;padding:12px 14px;margin-bottom:16px;color:#fca5a5;line-height:1.45;"></div>
  <div class="stat-row" id="stats-row"></div>
  <div class="notice" id="analysis-notice"></div>

  <!-- Track map + speed chart -->
  <div class="grid-2">
    <div class="card">
      <h2>Track Map</h2>
      <div style="margin-bottom:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
        <span style="font-size:12px; color:var(--muted)">Color by:</span>
        <select id="map-color-mode" onchange="drawTrackMap()">
          <option value="speed">Speed</option>
          <option value="brake">Brake</option>
          <option value="gas">Throttle</option>
        </select>
        <select id="map-lap-select" onchange="drawTrackMap()"></select>
      </div>
      <div class="track-wrap">
        <canvas id="track-canvas"></canvas>
        <div id="map-tooltip"></div>
      </div>
      <div style="margin-top:8px; display:flex; gap:6px; align-items:center; font-size:11px; color:var(--muted)">
        <span>Low</span>
        <canvas id="colorbar" width="200" height="14" style="border-radius:3px"></canvas>
        <span>High</span>
        <span id="colorbar-label" style="margin-left:8px"></span>
      </div>
    </div>
    <div class="card">
      <h2>Speed Trace</h2>
      <div class="lap-filters" id="speed-lap-filters"></div>
      <canvas id="speed-chart" height="260"></canvas>
    </div>
  </div>

  <!-- Corner speed -->
  <div class="section-title">Corner Speed Comparison</div>
  <div class="grid-2">
    <div class="card">
      <h2>Apex Speed by Corner</h2>
      <canvas id="corner-chart" height="280"></canvas>
    </div>
    <div class="card" style="overflow-x:auto">
      <h2>Corner Speed Table (km/h)</h2>
      <table class="corner-table" id="corner-table"></table>
    </div>
  </div>

  <!-- Input channels -->
  <div class="section-title">Input Channels</div>
  <div class="card" style="margin-bottom:16px">
    <h2>Brake &bull; Throttle &bull; Gear</h2>
    <div class="lap-filters" id="inputs-lap-filters"></div>
    <canvas id="inputs-chart" height="200"></canvas>
  </div>

  <!-- Dynamics -->
  <div class="section-title">Dynamics</div>
  <div class="card" style="margin-bottom:16px">
    <h2>Steering &bull; G &bull; Yaw &bull; Brake Temp</h2>
    <div style="margin-bottom:10px; display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
      <span style="font-size:12px; color:var(--muted)">Metric:</span>
      <select id="dynamics-mode" onchange="buildDynamicsChart()">
        <option value="steer">Steer (deg)</option>
        <option value="yaw_rate">Yaw rate</option>
        <option value="lat_g">Lateral G</option>
        <option value="long_g">Longitudinal G</option>
        <option value="brake_temp_front">Brake temp front (avg)</option>
        <option value="brake_temp_rear">Brake temp rear (avg)</option>
      </select>
    </div>
    <div class="lap-filters" id="dynamics-lap-filters"></div>
    <canvas id="dynamics-chart" height="220"></canvas>
  </div>
</div>

<script>
const DATA = __DATA__;
const LAP_COLORS = ['#3b82f6','#22c55e','#f97316','#a855f7','#eab308','#ec4899','#06b6d4'];
function lapColor(n) { return LAP_COLORS[(n - 1) % LAP_COLORS.length]; }
function speedColor(frac) {
  const stops = [[0,[30,120,255]],[0.25,[0,210,220]],[0.5,[0,200,80]],[0.75,[240,200,0]],[1,[255,40,40]]];
  frac = Math.max(0, Math.min(1, frac));
  for (let i = 1; i < stops.length; i++) {
    if (frac <= stops[i][0]) {
      const t = (frac - stops[i-1][0]) / (stops[i][0] - stops[i-1][0]);
      const c0 = stops[i-1][1], c1 = stops[i][1];
      return `rgb(${Math.round(c0[0]+t*(c1[0]-c0[0]))},${Math.round(c0[1]+t*(c1[1]-c0[1]))},${Math.round(c0[2]+t*(c1[2]-c0[2]))})`;
    }
  }
  return 'rgb(255,40,40)';
}
function brakeColor(v) { const r = Math.round(60 + v * 195); return `rgb(${r},${Math.round(30*(1-v))},${Math.round(30*(1-v))})`; }
function gasColor(v) { const g = Math.round(60 + v * 175); return `rgb(${Math.round(30*(1-v))},${g},${Math.round(30*(1-v))})`; }

const activeLaps = new Set(DATA.laps.map(l => l.lap_num));

function syncFilterButtons() {
  document.querySelectorAll('.lap-btn').forEach(btn => {
    const n = parseInt(btn.dataset.lap);
    btn.classList.toggle('active', activeLaps.has(n));
  });
}

function rebuildAll() { syncFilterButtons(); buildSpeedChart(); buildInputsChart(); buildDynamicsChart(); }

function makeLapFilters(containerId, onChange) {
  const el = document.getElementById(containerId);
  el.replaceChildren();
  const heading = document.createElement('span');
  heading.style.cssText = 'font-size:12px;color:var(--muted);margin-right:4px';
  heading.textContent = 'Laps:';
  el.appendChild(heading);
  DATA.laps.forEach(lap => {
    const btn = document.createElement('button');
    btn.className = 'lap-btn active';
    btn.style.color = lapColor(lap.lap_num);
    const validity = lap.is_valid ? '' : ' [INVALID]';
    btn.textContent = `L${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} - ${lap.lap_time_str}${validity}`;
    btn.dataset.lap = lap.lap_num;
    btn.addEventListener('click', () => {
      if (activeLaps.has(lap.lap_num)) activeLaps.delete(lap.lap_num);
      else activeLaps.add(lap.lap_num);
      onChange();
    });
    el.appendChild(btn);
  });
}

function renderStats() {
  const row = document.getElementById('stats-row');
  const validLaps = DATA.laps.filter(l => l.is_valid);
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num && l.is_valid) || null;
  const maxSpd = validLaps.length ? Math.max(...validLaps.map(l => l.max_speed)) : null;
  const stats = [
    { label: 'Laps', value: DATA.laps.length },
    { label: 'Best Lap', value: bestLap ? bestLap.lap_time_str : 'N/A' },
    { label: 'Valid-Lap Top Speed', value: maxSpd !== null ? maxSpd.toFixed(0) + ' km/h' : 'N/A' },
    { label: 'Corners / Lap', value: DATA.ref_corners.length },
  ];
  row.replaceChildren();
  stats.forEach(s => {
    const stat = document.createElement('div');
    stat.className = 'stat';
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = s.label;
    const value = document.createElement('div');
    value.className = 'value';
    value.textContent = String(s.value);
    stat.append(label, value);
    row.appendChild(stat);
  });
  const notice = document.getElementById('analysis-notice');
  const notes = DATA.analysis_notes || [];
  if (notice && (DATA.analysis_mode !== 'full' || notes.length)) {
    notice.replaceChildren();
    const title = document.createElement('strong');
    title.textContent = DATA.analysis_mode !== 'full'
      ? 'Diagnostic mode:'
      : 'Analysis notes:';
    notice.appendChild(title);
    if (DATA.analysis_mode !== 'full') {
      notice.appendChild(document.createTextNode(
        ' Detailed coaching is suppressed because this capture is not fully trustworthy.'
      ));
    }
    if (notes.length) {
      const list = document.createElement('ul');
      notes.forEach(note => {
        const item = document.createElement('li');
        item.textContent = String(note);
        list.appendChild(item);
      });
      notice.appendChild(list);
    }
    notice.style.display = 'block';
  } else if (notice) {
    notice.style.display = 'none';
  }
  const prefix = DATA.track_label || DATA.track_name || '';
  const bestText = bestLap ? bestLap.lap_time_str : 'N/A (no valid lap)';
  document.getElementById('session-info').textContent = `${prefix ? prefix + '  |  ' : ''}${DATA.laps.length} laps detected  -  best ${bestText}`;
}

/* ── Track map ─────────────────────────────────────────────── */
function drawTrackMap() {
  const canvas = document.getElementById('track-canvas');
  const mode = document.getElementById('map-color-mode').value;
  const sel = document.getElementById('map-lap-select');
  const lapNum = parseInt(sel.value);
  const lap = DATA.laps.find(l => l.lap_num === lapNum);
  if (!lap) return;
  const pts = lap.track;
  const xs = pts.map(p => p.x), zs = pts.map(p => p.z);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minZ = Math.min(...zs), maxZ = Math.max(...zs);
  const wrap = canvas.parentElement;
  const size = Math.min(wrap.clientWidth, 420);
  canvas.width = size; canvas.height = size;
  const pad = 24, sc = Math.min((size - 2*pad) / (maxX - minX || 1), (size - 2*pad) / (maxZ - minZ || 1));
  const offX = pad + ((size - 2*pad) - (maxX-minX)*sc) / 2, offZ = pad + ((size - 2*pad) - (maxZ-minZ)*sc) / 2;
  const cx = x => offX + (x - minX) * sc, cz = z => offZ + (z - minZ) * sc;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, size, size); ctx.fillStyle = '#0d0f14'; ctx.fillRect(0, 0, size, size);
  let vals = mode === 'speed' ? pts.map(p => p.speed) : mode === 'brake' ? pts.map(p => p.brake) : pts.map(p => p.gas);
  const minV = Math.min(...vals), maxV = Math.max(...vals);
  ctx.beginPath(); ctx.strokeStyle = '#1e2028'; ctx.lineWidth = 12; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  pts.forEach((p, i) => { i === 0 ? ctx.moveTo(cx(p.x), cz(p.z)) : ctx.lineTo(cx(p.x), cz(p.z)); });
  ctx.stroke();
  for (let i = 1; i < pts.length; i++) {
    const p0 = pts[i - 1], p1 = pts[i];
    const frac = (vals[i] - minV) / (maxV - minV || 1);
    ctx.beginPath(); ctx.lineWidth = 6;
    ctx.strokeStyle = mode === 'speed' ? speedColor(frac) : mode === 'brake' ? brakeColor(vals[i]) : gasColor(vals[i]);
    ctx.moveTo(cx(p0.x), cz(p0.z)); ctx.lineTo(cx(p1.x), cz(p1.z)); ctx.stroke();
  }
  window._cornerHits = [];
  lap.corners.forEach((c, idx) => {
    const p = pts.find(pt => pt.frame === c.apex_frame) || pts[0];
    const px = cx(p.x), pz = cz(p.z);
    ctx.beginPath(); ctx.arc(px, pz, 6, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill();
    ctx.fillStyle = '#000'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText(idx + 1, px, pz);
    window._cornerHits.push({ px, pz, num: idx + 1, name: c.name || null });
  });
  const start = pts[0];
  ctx.beginPath(); ctx.arc(cx(start.x), cz(start.z), 8, 0, Math.PI * 2); ctx.fillStyle = '#fff'; ctx.fill();
  ctx.fillStyle = '#111'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('S', cx(start.x), cz(start.z));
  drawColorbar(mode, minV, maxV);
}

function drawColorbar(mode, minV, maxV) {
  const cb = document.getElementById('colorbar');
  const ctx = cb.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 200, 0);
  if (mode === 'speed') { for (let i = 0; i <= 10; i++) { grad.addColorStop(i/10, speedColor(i/10)); } }
  else if (mode === 'brake') { grad.addColorStop(0, brakeColor(0)); grad.addColorStop(1, brakeColor(1)); }
  else { grad.addColorStop(0, gasColor(0)); grad.addColorStop(1, gasColor(1)); }
  ctx.fillStyle = grad; ctx.fillRect(0, 0, 200, 14);
  document.getElementById('colorbar-label').textContent = mode === 'speed' ? `${minV.toFixed(0)}\u2013${maxV.toFixed(0)} km/h` : '0\u2013100%';
}

/* ── Speed chart with corner shading ──────────────────────── */
let speedChart = null;
function buildSpeedChart() {
  const ctx = document.getElementById('speed-chart').getContext('2d');
  if (speedChart) speedChart.destroy();
  const datasets = DATA.laps.filter(l => activeLaps.has(l.lap_num)).map(lap => ({
    label: `Lap ${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} (${lap.lap_time_str})`,
    data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.speed })),
    borderColor: lapColor(lap.lap_num), backgroundColor: 'transparent', borderWidth: 1.8, pointRadius: 0, tension: 0.3,
  }));
  const annotations = {};
  const bestLap = DATA.laps.find(l => l.lap_num === DATA.best_lap_num && l.is_valid) || null;
  if (bestLap) {
    bestLap.corners.forEach(c => {
      const s = (c.start_frame - bestLap.start_frame) / Math.max(bestLap.track.length - 1, 1) * 100;
      const e = (c.end_frame - bestLap.start_frame) / Math.max(bestLap.track.length - 1, 1) * 100;
      annotations['corner' + c.id] = {
        type: 'box', xMin: s, xMax: e,
        backgroundColor: 'rgba(255,255,255,0.04)', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        label: { content: c.name || ('C' + c.id), display: true, color: '#9ca3af', font: { size: 9 } },
      };
    });
  }
  speedChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index', intersect: false },
    scales: {
      x: { type: 'linear', min: 0, max: 100, title: { display: true, text: 'Lap progress (%)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { title: { display: true, text: 'Speed (km/h)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } }, annotation: { annotations } },
  }});
}

/* ── Corner charts ────────────────────────────────────────── */
let cornerChart = null;
function buildCornerChart() {
  const ctx = document.getElementById('corner-chart').getContext('2d');
  if (cornerChart) cornerChart.destroy();
  const labels = DATA.ref_corners.map(c => c.name || ('C' + c.id));
  const datasets = DATA.laps.map(lap => ({
    label: `Lap ${lap.lap_num}`,
    data: DATA.ref_corners.map(c => { const s = DATA.corner_speeds[c.id]; return s ? (s[lap.lap_num] || null) : null; }),
    backgroundColor: lapColor(lap.lap_num) + 'cc', borderColor: lapColor(lap.lap_num), borderWidth: 1, borderRadius: 4,
  }));
  cornerChart = new Chart(ctx, { type: 'bar', data: { labels, datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index' },
    scales: {
      x: { grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
      y: { title: { display: true, text: 'Apex Speed (km/h)', color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } },
  }});
}

function buildCornerTable() {
  const table = document.getElementById('corner-table');
  const lapNums = DATA.laps.map(l => l.lap_num);
  table.replaceChildren();
  const thead = document.createElement('thead');
  const headerRow = document.createElement('tr');
  const cornerHeader = document.createElement('th');
  cornerHeader.textContent = 'Corner';
  headerRow.appendChild(cornerHeader);
  lapNums.forEach(n => {
    const header = document.createElement('th');
    header.textContent = `Lap ${n}`;
    headerRow.appendChild(header);
  });
  const deltaHeader = document.createElement('th');
  deltaHeader.textContent = '\u0394 Best\u2013Worst';
  headerRow.appendChild(deltaHeader);
  thead.appendChild(headerRow);
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  DATA.ref_corners.forEach(c => {
    const speeds = DATA.corner_data?.[c.id] || {};
    const vals = lapNums.map(n => speeds[n]?.apex).filter(v => v !== undefined);
    const best = vals.length ? Math.max(...vals) : null;
    const worst = vals.length ? Math.min(...vals) : null;
    const delta = best !== null ? (best - worst).toFixed(1) : '\u2014';
    const row = document.createElement('tr');
    const cornerCell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.style.background = 'var(--border)';
    badge.textContent = c.name || ('C' + c.id);
    cornerCell.appendChild(badge);
    row.appendChild(cornerCell);
    lapNums.forEach(n => {
      const v = speeds[n]?.apex;
      const cell = document.createElement('td');
      if (v === undefined) {
        cell.style.color = 'var(--muted)';
        cell.textContent = '-';
        row.appendChild(cell);
        return;
      }
      const isB = v === best, isW = v === worst;
      const color = isB ? 'var(--green)' : isW ? 'var(--red)' : 'var(--text)';
      cell.style.color = color;
      cell.style.fontWeight = isB || isW ? '700' : '400';
      cell.textContent = v.toFixed(1);
      row.appendChild(cell);
    });
    const deltaCell = document.createElement('td');
    deltaCell.style.color = 'var(--orange)';
    deltaCell.textContent = delta;
    row.appendChild(deltaCell);
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
}

/* ── Inputs chart (Brake / Throttle / Gear) ───────────────── */
let inputsChart = null;
function buildInputsChart() {
  const ctx = document.getElementById('inputs-chart').getContext('2d');
  if (inputsChart) inputsChart.destroy();
  const active = DATA.laps.filter(l => activeLaps.has(l.lap_num));
  if (!active.length) return;
  const datasets = [];
  active.forEach(lap => {
    const color = lapColor(lap.lap_num);
    datasets.push({
      label: `L${lap.lap_num} Brake`,
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.brake * 100 })),
      borderColor: color, backgroundColor: 'transparent', borderWidth: 2, borderDash: [], pointRadius: 0, tension: 0.2,
    });
    datasets.push({
      label: `L${lap.lap_num} Throttle`,
      data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: pt.gas * 100 })),
      borderColor: color, backgroundColor: 'transparent', borderWidth: 2, borderDash: [4, 3], pointRadius: 0, tension: 0.2,
    });
  });
  datasets.push({
    label: 'Gear \u00d7 10',
    data: active[0].track.map((pt, i) => ({ x: i / Math.max(active[0].track.length - 1, 1) * 100, y: pt.gear * 10 })),
    borderColor: '#eab308', backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0, borderDash: [2, 4],
  });
  inputsChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false,
    scales: {
      x: { type: 'linear', min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12, font: { size: 10 } } } },
  }});
}

/* ── Dynamics chart (Steer / Yaw / G / Brake Temp) ────────── */
let dynamicsChart = null;

function dynValue(pt, mode) {
  if (mode === 'steer') return (pt.steer || 0) * (180 / Math.PI);
  if (mode === 'yaw_rate') return pt.yaw_rate || 0;
  if (mode === 'lat_g') return pt.acc_g_x || 0;
  if (mode === 'long_g') return pt.acc_g_z || 0;
  if (mode === 'brake_temp_front') return ((pt.brake_temp_fl || 0) + (pt.brake_temp_fr || 0)) / 2;
  if (mode === 'brake_temp_rear') return ((pt.brake_temp_rl || 0) + (pt.brake_temp_rr || 0)) / 2;
  return 0;
}
function dynLabel(mode) {
  const m = { steer: 'Steer (deg)', yaw_rate: 'Yaw rate', lat_g: 'Lateral G', long_g: 'Longitudinal G', brake_temp_front: 'Brake temp front (avg)', brake_temp_rear: 'Brake temp rear (avg)' };
  return m[mode] || mode;
}

function buildDynamicsChart() {
  const ctx = document.getElementById('dynamics-chart').getContext('2d');
  if (dynamicsChart) dynamicsChart.destroy();
  const active = DATA.laps.filter(l => activeLaps.has(l.lap_num));
  if (!active.length) return;
  const mode = document.getElementById('dynamics-mode').value;
  const datasets = active.map(lap => ({
    label: `Lap ${lap.lap_num}${lap.lap_num===DATA.best_lap_num?'*':''} (${lap.lap_time_str})`,
    data: lap.track.map((pt, i) => ({ x: i / Math.max(lap.track.length - 1, 1) * 100, y: dynValue(pt, mode) })),
    borderColor: lapColor(lap.lap_num), backgroundColor: 'transparent', borderWidth: 1.8, pointRadius: 0, tension: 0.2,
  }));
  dynamicsChart = new Chart(ctx, { type: 'line', data: { datasets }, options: {
    responsive: true, animation: false, interaction: { mode: 'index', intersect: false },
    scales: {
      x: { type: 'linear', min: 0, max: 100, grid: { color: '#1e2028' }, ticks: { color: '#6b7280', callback: v => v + '%' } },
      y: { title: { display: true, text: dynLabel(mode), color: '#6b7280' }, grid: { color: '#1e2028' }, ticks: { color: '#6b7280' } },
    },
    plugins: { legend: { labels: { color: '#e0e2ea', boxWidth: 12 } } },
  }});
}

/* ── Init ──────────────────────────────────────────────────── */
function showError(msg) {
  const el = document.getElementById('render-error');
  if (el) { el.style.display = 'block'; el.textContent = '\u26A0 ' + msg; }
  document.getElementById('session-info').textContent = 'Render error \u2014 check browser console';
}

window.addEventListener('DOMContentLoaded', () => {
  try {
    if (typeof Chart === 'undefined') {
      showError('Bundled Chart.js library failed to load.');
      return;
    }
    renderStats();
    const sel = document.getElementById('map-lap-select');
    DATA.laps.forEach(l => {
      const opt = document.createElement('option');
      opt.value = l.lap_num;
      opt.textContent = `Lap ${l.lap_num}${l.lap_num===DATA.best_lap_num?'*':''} (${l.lap_time_str})${l.is_valid?'':' [INVALID]'}`;
      sel.appendChild(opt);
    });
    makeLapFilters('speed-lap-filters', rebuildAll);
    makeLapFilters('inputs-lap-filters', rebuildAll);
    makeLapFilters('dynamics-lap-filters', rebuildAll);
    drawTrackMap(); buildSpeedChart(); buildCornerChart(); buildCornerTable(); buildInputsChart(); buildDynamicsChart();
    const mapCanvas = document.getElementById('track-canvas');
    const mapTip = document.getElementById('map-tooltip');
    mapCanvas.addEventListener('mousemove', e => {
      const rect = mapCanvas.getBoundingClientRect();
      const scaleX = mapCanvas.width / rect.width, scaleY = mapCanvas.height / rect.height;
      const mx = (e.clientX - rect.left) * scaleX, my = (e.clientY - rect.top) * scaleY;
      const hits = window._cornerHits || [];
      let found = null;
      for (const h of hits) { if (Math.hypot(mx - h.px, my - h.pz) < 10) { found = h; break; } }
      if (found) {
        mapTip.textContent = found.name ? `C${found.num}: ${found.name}` : `Corner ${found.num}`;
        const wrapRect = mapCanvas.parentElement.getBoundingClientRect();
        mapTip.style.left = (e.clientX - wrapRect.left + 14) + 'px';
        mapTip.style.top  = (e.clientY - wrapRect.top  - 10) + 'px';
        mapTip.style.display = 'block';
      } else { mapTip.style.display = 'none'; }
    });
    mapCanvas.addEventListener('mouseleave', () => { mapTip.style.display = 'none'; });
  } catch (e) {
    showError('JavaScript error: ' + (e.message || e));
    console.error(e);
  }
});
</script>
</body>
</html>"""
    # Resolve all markers from the original template in one pass. This keeps
    # marker text in either trusted scripts or report data from changing the
    # other substitution.
    replacements = {
        "__CHART_JS__": chart_js,
        "__ANNOTATION_JS__": annotation_js,
        "__DATA__": data_json,
    }
    return re.sub(
        r"__CHART_JS__|__ANNOTATION_JS__|__DATA__",
        lambda match: replacements[match.group(0)],
        template,
    )
