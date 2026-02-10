const $ = (s) => document.querySelector(s);

function fmt(v){
  if(v === null || v === undefined) return "—";
  if(typeof v === "number") return Number.isFinite(v) ? v.toFixed(3).replace(/\.000$/,"") : "—";
  return String(v);
}
function ms(age){ return Math.round((age||0)*1000); }
function flagChip(name, on){
  return `<span class="flag ${on ? "on":""}">${name}</span>`;
}


/* ===== Value severity thresholds (Debug) =====
   - green: within warn band
   - orange: outside warn band
   - red: outside bad band
   Adjust as needed for your setup.
*/
const THRESH = {
  CoolantTemp: { warn: [70, 110], bad: [60, 120] },
  OilTemp:     { warn: [60, 120], bad: [50, 130] },
  BatteryV:    { warn: [12.0, 14.8], bad: [11.5, 15.2] },
  Lambda:      { warn: [0.95, 1.05], bad: [0.85, 1.15] },
  MAP_kPa:     { warn: [95, 220], bad: [85, 260] },
  RPM:         { warn: [700, 7000], bad: [400, 8200] },
  Speed:       { warn: [0, 260], bad: [0, 300] },
  Throttle:    { warn: [0, 100], bad: [0, 100] },
  Load:        { warn: [0, 100], bad: [0, 100] },
};

function classifyValue(signalName, v){
  const x = Number(v);
  if(v === null || v === undefined || !Number.isFinite(x)) return "val-na";
  const t = THRESH[signalName];
  if(!t) return "val-na";
  const [wMin, wMax] = t.warn;
  const [bMin, bMax] = t.bad;
  if(x < bMin || x > bMax) return "val-bad";
  if(x < wMin || x > wMax) return "val-warn";
  return "val-ok";
}


export function renderDebug(payload){
  const qEl = $("#dbg-q");
  const q = (qEl?.value || "").trim().toLowerCase();

  const meta = payload?.meta || {};
  const fr = meta.last_frame || {};

  // Header chip (keep existing top-right meta)
  const metaEl = $("#dbg-meta");
  if(metaEl){
    const id = fr.arb_id ? `0x${Number(fr.arb_id).toString(16)}` : "—";
    metaEl.textContent = `${id} ${fr.name||""} | rx ${meta.rx_total||0}/${meta.rx_decoded||0} | stale ${meta.stale ? "yes":"no"}`;
  }

  // Flags chips
  const flags = payload?.flags || {};
  const flagsEl = $("#dbg-flags");
  if(flagsEl){
    flagsEl.innerHTML = [
      flagChip("MIL", !!flags.MIL),
      flagChip("EPC", !!flags.EPC),
      flagChip("Fan", !!flags.Fan),
      flagChip("Cruise", !!flags.Cruise),
      flagChip("Brake", !!flags.Brake),
      flagChip("Clutch", !!flags.Clutch),
    ].join("");
  }

  // Helpers
  const matchQ = (k, v, unit) => {
    if(!q) return true;
    const hay = `${k} ${fmt(v)} ${unit||""}`.toLowerCase();
    return hay.includes(q);
  };

  const tableSignals = (title, obj) => {
    const entries = Object.entries(obj || {})
      .filter(([k, s]) => matchQ(k, s?.v, s?.unit))
      .sort(([a],[b]) => a.localeCompare(b));

    const rows = entries.map(([k, s]) => {
      const v = fmt(s?.v);
      const unit = s?.unit || "";
      const age = ms(s?.age);
      const cls = classifyValue(k, s?.v);
      return `<tr>
        <td class="kbd">${k}</td>
        <td class="kbd"><span class="val-badge ${cls}">${v}</span></td>
        <td class="kbd">${unit}</td>
        <td class="kbd right">${age} ms</td>
      </tr>`;
    }).join("");

    const n = entries.length;
    return `<section class="dbg-section">
      <div class="dbg-section-head kbd">${title} <span class="kbd" style="opacity:.7">(${n})</span></div>
      <div class="dbg-section-body">
        <table class="debug-table">
          <colgroup>
            <col class="col-signal" />
            <col class="col-value" />
            <col class="col-unit" />
            <col class="col-age" />
          </colgroup>
          <thead><tr>
            <th>Signal</th><th>Value</th><th>Unit</th><th class="right">Age</th>
          </tr></thead>
          <tbody>${rows || `<tr><td colspan="4" class="kbd">—</td></tr>`}</tbody>
        </table>
      </div>
    </section>`;
  };

  const tableKV = (title, obj) => {
    const entries = Object.entries(obj || {})
      .filter(([k, v]) => matchQ(k, v, ""))
      .sort(([a],[b]) => a.localeCompare(b));

    const rows = entries.map(([k, v]) => {
      return `<tr>
        <td class="kbd">${k}</td>
        <td class="kbd right" colspan="3">${fmt(v)}</td>
      </tr>`;
    }).join("");

    const n = entries.length;
    return `<section class="dbg-section">
      <div class="dbg-section-head kbd">${title} <span class="kbd" style="opacity:.7">(${n})</span></div>
      <div class="dbg-section-body">
        <table class="debug-table">
          <colgroup>
            <col class="col-signal" />
            <col class="col-value" />
            <col class="col-unit" />
            <col class="col-age" />
          </colgroup>
          <thead><tr>
            <th>Key</th><th class="right" colspan="3">Value</th>
          </tr></thead>
          <tbody>${rows || `<tr><td colspan="4" class="kbd">—</td></tr>`}</tbody>
        </table>
      </div>
    </section>`;
  };

  // Sections
  const sig = payload?.signals || {};
  const raw = payload?.raw || {};
  const dev = payload?.dev || {};
  const compat = payload?.compat?.signals || {};
  const metaCopy = {
    rx_total: meta.rx_total,
    rx_decoded: meta.rx_decoded,
    last_rx_age_s: meta.last_rx_age_s,
    stale: meta.stale,
    last_frame_id: fr.arb_id ? `0x${Number(fr.arb_id).toString(16)}` : null,
    last_frame_name: fr.name || null,
    push_hz: meta.push_hz,
    stale_s: meta.stale_s,
    src: meta.src,
    dbc: meta.dbc,
  };

  const leftCol = [
    tableSignals("Raw (DBC decode)", raw),
  ].join("");

  const rightCol = [
    tableSignals("Signals (canonical)", sig),
    tableSignals("Compat (legacy aliases)", compat),
    tableKV("Dev (derived KPIs)", dev),
    tableKV("Meta (RX/health)", metaCopy),
  ].join("");

  const html = `<div class="dbg-col dbg-col-left">${leftCol}</div>
    <div class="dbg-col dbg-col-right">${rightCol}</div>`;

  const tableEl = $("#dbg-table");
  if(tableEl){
    tableEl.innerHTML = html;
  }
}
