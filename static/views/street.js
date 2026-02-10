const $ = (s) => document.querySelector(s);

function getSig(payload, name){
  const s = payload?.signals?.[name];
  return s ? s : null;
}

function fmt(v, digits=1){
  if(v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Number(v);
  if(!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

function int(v){
  if(v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Number(v);
  if(!Number.isFinite(n)) return "—";
  return Math.round(n);
}

function ageMs(sig){
  if(!sig) return "—";
  return Math.round((sig.age || 0)*1000);
}

function ringPct(speed){
  const s = Number(speed || 0);
  if(!Number.isFinite(s) || s < 0) return 0;
  return Math.max(0, Math.min(100, (s / 260) * 100));
}

export function renderStreet(payload){
  const rpm = getSig(payload,"RPM");
  const spd = getSig(payload,"Speed");
  const bst = getSig(payload,"Boost");
  const lam = getSig(payload,"Lambda");
  const oil = getSig(payload,"OilTemp");
  const clt = getSig(payload,"CoolantTemp");
  const bat = getSig(payload,"BatteryV");

  const speed = spd ? spd.v : null;
  const rpmVal = rpm ? rpm.v : null;
  const pct = ringPct(speed);

  const host = $("#street-dashboard");
  if(!host) return;

  host.innerHTML = `
    <div class="audi-dash">
      <div class="audi-main">
        <div class="speed-ring" style="--p:${pct}">
          <div class="speed-ring-inner">
            <div class="speed-value">${int(speed)}</div>
            <div class="speed-unit">km/h</div>
            <div class="speed-age">age ${ageMs(spd)} ms</div>
          </div>
        </div>

        <div class="center-stack">
          <div class="center-title">DRIVE SELECT · STREET</div>
          <div class="rpm-value">${int(rpmVal)}</div>
          <div class="rpm-unit">rpm</div>
          <div class="rpm-bar"><span style="width:${Math.max(0, Math.min(100, (Number(rpmVal||0)/8000)*100))}%"></span></div>
          <div class="rpm-age">age ${ageMs(rpm)} ms</div>
        </div>
      </div>

      <div class="audi-kpis">
        <div class="audi-kpi"><div>Boost</div><strong>${fmt(bst?.v,2)}</strong><small>bar · ${ageMs(bst)} ms</small></div>
        <div class="audi-kpi"><div>Lambda</div><strong>${fmt(lam?.v,3)}</strong><small>afr ratio · ${ageMs(lam)} ms</small></div>
        <div class="audi-kpi"><div>Oil</div><strong>${int(oil?.v)}</strong><small>°C · ${ageMs(oil)} ms</small></div>
        <div class="audi-kpi"><div>Coolant</div><strong>${int(clt?.v)}</strong><small>°C · ${ageMs(clt)} ms</small></div>
        <div class="audi-kpi"><div>Battery</div><strong>${fmt(bat?.v,2)}</strong><small>V · ${ageMs(bat)} ms</small></div>
      </div>
    </div>
  `;
}
