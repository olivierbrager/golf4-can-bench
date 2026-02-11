const $ = (s) => document.querySelector(s);

function getSig(payload, name){
  const s = payload?.signals?.[name];
  return s ? s : null;
}

function getAny(payload, name){
  return payload?.signals?.[name] || payload?.raw?.[name] || null;
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

function clamp(n, lo, hi){
  if(!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, n));
}

function pct(val, min, max){
  const n = Number(val);
  if(!Number.isFinite(n)) return 0;
  return clamp(((n - min) / (max - min)) * 100, 0, 100);
}

function timeHHMM(){
  const d = new Date();
  const h = String(d.getHours()).padStart(2, "0");
  const m = String(d.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function gearDisplay(gearValue){
  const gearNum = Number(gearValue);
  const gear = Number.isFinite(gearNum) ? Math.max(0, Math.round(gearNum)) : null;
  if(gear && gear > 0) return `D${gear}`;
  return "D-";
}

function dialAngle01(t){
  // 270-degree arc centered on vertical top: t=0.5 -> 270deg (4000 rpm straight up).
  return 135 + (270 * t);
}

export function renderStreet(payload){
  const rpm = getSig(payload,"RPM") || getAny(payload, "RPM");
  const spd = getSig(payload,"Speed") || getAny(payload, "Speed");
  const bst = getSig(payload,"Boost");
  const thr = getSig(payload,"Throttle");
  const load = getSig(payload,"Load");
  const iat = getAny(payload, "IAT_C");
  const gear = getAny(payload, "Gear");

  const speed = spd ? spd.v : null;
  const rpmVal = rpm ? rpm.v : null;

  const rpmPct = pct(rpmVal || 0, 0, 8000);
  const spdPct = pct(speed || 0, 0, 260);
  const boostPct = pct(bst?.v || 0, -1.0, 2.0);
  const loadPct = pct(load?.v || 0, 0, 100);
  const thrPct = pct(thr?.v || 0, 0, 100);
  const engagedGear = gearDisplay(gear?.v);

  const host = $("#street-dashboard");
  if(!host) return;

  const dialLabels = Array.from({length: 9}, (_, i) => {
    const a = dialAngle01(i / 8);
    const rad = (a * Math.PI) / 180.0;
    const x = 50 + (37.3 * Math.cos(rad));
    const y = 50 + (37.3 * Math.sin(rad));
    const cls = i >= 7 ? "dial-num red" : "dial-num";
    return `<span class="${cls}" style="left:${x}%;top:${y}%;">${i}</span>`;
  }).join("");

  const dialTicks = Array.from({length: 33}, (_, i) => {
    const a = dialAngle01(i / 32);
    const rad = (a * Math.PI) / 180.0;
    const major = (i % 4) === 0;
    const radius = major ? 46.8 : 47.6;
    const x = 50 + (radius * Math.cos(rad));
    const y = 50 + (radius * Math.sin(rad));
    const red = i >= 27 ? " red" : "";
    const cls = (major ? "dial-tick major" : "dial-tick minor") + red;
    return `<span class="${cls}" style="left:${x}%;top:${y}%;transform:translate(-50%,-50%) rotate(${a + 90}deg);"></span>`;
  }).join("");

  const dialTicksRedFine = "";

  host.innerHTML = `
    <div class="audi-classic" style="--rpm:${rpmPct}; --spd:${spdPct}; --boost:${boostPct}; --load:${loadPct}; --thr:${thrPct};">
      <div class="top-bar">
        <div class="top-left"></div>
        <div class="top-right">⋯</div>
      </div>

      <div class="cluster">
        <div class="dial dial-left" style="--p:${rpmPct};">
          <div class="dial-ring"></div>
          <div class="dial-ring-outer"></div>
          <div class="dial-inner">
            <div class="dial-big dial-gear">${engagedGear}</div>
          </div>
          <div class="dial-needle dial-needle-left" style="--p:${rpmPct};">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>
          <div class="dial-ticks">${dialTicks}${dialTicksRedFine}</div>
          <div class="dial-labels left-labels">${dialLabels}</div>
          <div class="dial-redline"></div>
        </div>

        <div class="center-panel">
          <div class="center-top">
            <div class="mode">${engagedGear}</div>
            <div class="car">A3</div>
          </div>
          <div class="center-icons">
            <span>△</span><span>⚙</span><span>☀</span><span>☂</span>
          </div>
        </div>

        <div class="dial dial-right">
          <div class="dial-ring"></div>
          <div class="dial-inner">
            <div class="dial-label">km/h</div>
            <div class="dial-big">${int(speed)}</div>
          </div>
          <div class="dial-needle dial-needle-right" style="--p:${spdPct}">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>
          <div class="dial-scale right-scale">
            <span>0</span><span>60</span><span>120</span><span>180</span><span>240</span><span>300</span>
          </div>
        </div>
      </div>

      <div class="bottom-bar">
        <div class="bottom-left"></div>
        <div class="bottom-center">${timeHHMM()}</div>
        <div class="bottom-right">+${int(iat?.v)}.0°C</div>
      </div>
    </div>
  `;
}
