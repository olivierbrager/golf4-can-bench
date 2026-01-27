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
  const metaEl = $("#dbg-meta");
  if(metaEl){
    const id = fr.arb_id ? `0x${Number(fr.arb_id).toString(16)}` : "—";
    metaEl.textContent = `${id} ${fr.name||""} | rx ${meta.rx_total||0}/${meta.rx_decoded||0} | stale ${meta.stale ? "yes":"no"}`;
  }

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

  const raw = payload?.raw || {};
  const rows = Object.entries(raw)
    .filter(([k, s]) => {
      if(!q) return true;
      const hay = `${k} ${fmt(s?.v)} ${s?.unit||""}`.toLowerCase();
      return hay.includes(q);
    })
    .sort(([a],[b]) => a.localeCompare(b))
    .map(([k, s]) => {
      const v = fmt(s?.v);
      const unit = s?.unit || "";
      const age = ms(s?.age);
      return `<tr>
        <td class="kbd">${k}</td>
        <td class="kbd right"><span class="val-badge ${classifyValue(k, s?.v)}">${v}</span></td>
        <td class="kbd right">${unit}</td>
        <td class="kbd right">${age} ms</td>
      </tr>`;
    }).join("");

  const tableEl = $("#dbg-table");
  if(tableEl){
    tableEl.innerHTML = `<table class="debug-table">
      <colgroup>
        <col class="col-signal" />
        <col class="col-value" />
        <col class="col-unit" />
        <col class="col-age" />
      </colgroup>
      <thead><tr>
        <th>Signal</th><th class="right">Value</th><th class="right">Unit</th><th class="right">Age</th>
      </tr></thead>
      <tbody>${rows || `<tr><td colspan="4" class="kbd">—</td></tr>`}</tbody>
    </table>`;
  }
}
