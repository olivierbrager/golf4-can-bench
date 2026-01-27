const $ = (s) => document.querySelector(s);

function getSig(payload, name){
  const s = payload?.signals?.[name];
  return s ? s : null;
}
function fmt(v, digits=2){
  if(v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Number(v);
  if(!Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}
function ageMs(sig){
  if(!sig) return "—";
  return Math.round((sig.age || 0)*1000);
}
function kpi(label, val, unit, sub){
  return `<div class="kpi">
    <div class="label">${label}</div>
    <div class="val">${val}<span style="opacity:.55;font-size:14px;margin-left:8px">${unit||""}</span></div>
    <div class="sub">${sub||""}</div>
  </div>`;
}
function flagChip(name, on){
  return `<span class="flag ${on ? "on":""}">${name}</span>`;
}

export function renderDev(payload){
  const rpm = getSig(payload,"RPM");
  const spd = getSig(payload,"Speed");
  const bst = getSig(payload,"Boost");
  const lam = getSig(payload,"Lambda");
  const oil = getSig(payload,"OilTemp");
  const clt = getSig(payload,"CoolantTemp");
  const bat = getSig(payload,"BatteryV");
  const dev = payload?.dev || {};

  const el = $("#dev-kpis");
  if(!el) return;

  el.innerHTML = [
    kpi("RPM", rpm ? Math.round(rpm.v) : "—", "rpm", `age ${ageMs(rpm)} ms`),
    kpi("Speed", spd ? fmt(spd.v,1) : "—", "km/h", `age ${ageMs(spd)} ms`),
    kpi("Boost", bst ? fmt(bst.v,2) : "—", "bar", `max 5s: ${dev.BoostMax5===null?"—":fmt(dev.BoostMax5,2)} | age ${ageMs(bst)} ms`),
    kpi("Lambda", lam ? fmt(lam.v,3) : "—", "", `min/max 5s: ${dev.LambdaMin5===null?"—":fmt(dev.LambdaMin5,3)} / ${dev.LambdaMax5===null?"—":fmt(dev.LambdaMax5,3)} | age ${ageMs(lam)} ms`),
    kpi("Oil", oil ? Math.round(oil.v) : "—", "°C", `age ${ageMs(oil)} ms`),
    kpi("Coolant", clt ? Math.round(clt.v) : "—", "°C", `age ${ageMs(clt)} ms`),
    kpi("Battery", bat ? fmt(bat.v,2) : "—", "V", `age ${ageMs(bat)} ms`),
  ].join("");

  const flags = payload?.flags || {};
  const fel = $("#dev-flags");
  if(fel){
    fel.innerHTML = [
      flagChip("MIL", !!flags.MIL),
      flagChip("EPC", !!flags.EPC),
      flagChip("Fan", !!flags.Fan),
      flagChip("Cruise", !!flags.Cruise),
      flagChip("Brake", !!flags.Brake),
      flagChip("Clutch", !!flags.Clutch),
    ].join("");
  }
}
