const $ = (s) => document.querySelector(s);
const SHADOW_WINDOW_MS = 1000;
const rpmHistory = [];
const speedHistory = [];
const MAP_DYNAMIC_ZOOM = 17.2;
const MAP_FAST_ZOOM = 15.8;
const FILE_GPS_URL = "/static/gps_position.json";
const FILE_GPS_POLL_MS = 2000;
const GPS_STALE_MS = 8000;
const GEO_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse";
const GEO_REVERSE_MIN_MS = 15000;
const GEO_REVERSE_MOVE_DEG = 0.0002;
const MAP_HEADING_MIN_MOVE_M = 2.5;
const SPEED_LIMIT_OVERPASS_URL = "https://overpass-api.de/api/interpreter";
const SPEED_LIMIT_MIN_MS = 12000;
const SPEED_LIMIT_MOVE_DEG = 0.00028;

const streetMapState = {
  node: null,
  map: null,
  marker: null,
  leafletInitTried: false,
  watchStarted: false,
  filePollStarted: false,
  fileGps: null,
  filePollTsMs: 0,
  filePollBusy: false,
  geoBusy: false,
  geoTsMs: 0,
  geoLat: null,
  geoLon: null,
  geoLabel: "",
  speedBusy: false,
  speedTsMs: 0,
  speedLat: null,
  speedLon: null,
  speedLabel: "--",
  routeIdx: 0,
  routeLastStepMs: 0,
  routeSig: "",
  source: "init",
  lat: null,
  lon: null,
  accM: null,
  tsMs: 0,
  status: "GPS en attente",
  lastCenterKey: "",
  headingDeg: 0,
  headingReady: false,
  prevLat: null,
  prevLon: null,
  fastViewOn: false,
  expanded: false,
};

function getSig(payload, name){
  const s = payload?.signals?.[name];
  return s ? s : null;
}

function getAny(payload, name){
  return payload?.signals?.[name] || payload?.raw?.[name] || null;
}

function getFirst(payload, names){
  for(const name of names){
    const sig = getAny(payload, name);
    if(sig) return sig;
  }
  return null;
}

function sigNum(payload, names, fallback=0){
  const sig = getFirst(payload, names);
  const n = Number(sig?.v);
  return Number.isFinite(n) ? n : fallback;
}

function sigOn(payload, names){
  return sigNum(payload, names, 0) > 0.5;
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

function finiteNum(v){
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
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

function isStreetVisible(){
  const view = document.querySelector("#view-street");
  return Boolean(view?.classList.contains("active") || document.body.classList.contains("fullscreen-street"));
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

function speedArcT(v){
  const n = Number(v);
  if(!Number.isFinite(n)) return 0;
  const s = clamp(n, 0, 300);
  // Non-linear speed scale:
  // 0..100 km/h uses first half of the 270deg arc (100 at vertical top),
  // 100..300 km/h uses second half (coarser value spacing).
  if(s <= 100) return (s / 100) * 0.5;
  return 0.5 + ((s - 100) / 200) * 0.5;
}

function rollingMax(history, value, nowMs){
  const n = Number(value);
  if(Number.isFinite(n)) history.push({ t: nowMs, v: n });
  const minTs = nowMs - SHADOW_WINDOW_MS;
  while(history.length && history[0].t < minTs) history.shift();
  if(!history.length) return null;
  let max = -Infinity;
  for(const p of history){
    if(p.v > max) max = p.v;
  }
  return Number.isFinite(max) ? max : null;
}

function readGpsFromPayload(payload){
  const gpsMeta = payload?.meta?.gps || null;
  if(gpsMeta){
    const latMeta = finiteNum(gpsMeta.lat ?? gpsMeta.latitude);
    const lonMeta = finiteNum(gpsMeta.lon ?? gpsMeta.lng ?? gpsMeta.longitude);
    if(latMeta !== null && lonMeta !== null){
      return {
        lat: latMeta,
        lon: lonMeta,
        accM: finiteNum(gpsMeta.accuracy_m ?? gpsMeta.accuracy),
        source: "meta.gps",
      };
    }
  }

  const lat = sigNum(payload, ["GPS_Lat", "GPSLat", "Latitude", "Lat"], Number.NaN);
  const lon = sigNum(payload, ["GPS_Lon", "GPSLon", "Longitude", "Lon"], Number.NaN);
  if(Number.isFinite(lat) && Number.isFinite(lon)){
    return { lat, lon, accM: null, source: "signals" };
  }
  return null;
}

function fmtCoord(v, digits = 5){
  const n = finiteNum(v);
  if(n === null) return "—";
  return n.toFixed(digits);
}

function normalizeFileGps(cfg){
  if(!cfg || cfg.enabled === false) return null;
  const points = Array.isArray(cfg.points) ? cfg.points : null;
  if(points && points.length){
    const parsedPoints = points.map((p) => {
      const lat = finiteNum(p?.lat ?? p?.latitude);
      const lon = finiteNum(p?.lon ?? p?.lng ?? p?.longitude);
      if(lat === null || lon === null) return null;
      return {
        lat,
        lon,
        accM: finiteNum(p?.accuracy_m ?? p?.accuracy),
        label: (typeof p?.label === "string" ? p.label.trim() : ""),
      };
    }).filter(Boolean);
    if(!parsedPoints.length) return null;
    const tickS = finiteNum(cfg.tick_s);
    const tickMs = Math.max(200, Math.round((tickS === null ? 1 : tickS) * 1000));
    const loop = cfg.loop !== false;
    const label = typeof cfg.label === "string" ? cfg.label.trim() : "";
    const first = parsedPoints[0];
    const last = parsedPoints[parsedPoints.length - 1];
    const sig = `route:${parsedPoints.length}:${tickMs}:${loop ? 1 : 0}:${first.lat.toFixed(5)}:${first.lon.toFixed(5)}:${last.lat.toFixed(5)}:${last.lon.toFixed(5)}`;
    return {
      kind: "route",
      points: parsedPoints,
      tickMs,
      loop,
      label,
      sig,
    };
  }

  const lat = finiteNum(cfg.lat ?? cfg.latitude);
  const lon = finiteNum(cfg.lon ?? cfg.lng ?? cfg.longitude);
  if(lat === null || lon === null) return null;
  const label = typeof cfg.label === "string" ? cfg.label.trim() : "";
  return {
    kind: "point",
    lat,
    lon,
    accM: finiteNum(cfg.accuracy_m ?? cfg.accuracy),
    label,
  };
}

function fileGpsPointNow(){
  const fileGps = streetMapState.fileGps;
  if(!fileGps) return null;
  if(fileGps.kind !== "route"){
    return {
      lat: fileGps.lat,
      lon: fileGps.lon,
      accM: fileGps.accM,
      label: fileGps.label,
      source: "file",
    };
  }

  const nowMs = Date.now();
  if(streetMapState.routeSig !== fileGps.sig){
    streetMapState.routeSig = fileGps.sig;
    streetMapState.routeIdx = 0;
    streetMapState.routeLastStepMs = nowMs;
  }

  const count = fileGps.points.length;
  if(!count) return null;

  const elapsed = nowMs - streetMapState.routeLastStepMs;
  if(elapsed >= fileGps.tickMs){
    const steps = Math.floor(elapsed / fileGps.tickMs);
    streetMapState.routeLastStepMs += steps * fileGps.tickMs;
    if(fileGps.loop){
      streetMapState.routeIdx = (streetMapState.routeIdx + steps) % count;
    }else{
      streetMapState.routeIdx = Math.min(streetMapState.routeIdx + steps, count - 1);
    }
  }

  const idx = streetMapState.routeIdx;
  const p = fileGps.points[idx];
  const nextIdx = (idx < count - 1) ? (idx + 1) : (fileGps.loop ? 0 : idx);
  const pNext = fileGps.points[nextIdx];
  const phase = clamp((nowMs - streetMapState.routeLastStepMs) / fileGps.tickMs, 0, 1);
  const lat = p.lat + ((pNext.lat - p.lat) * phase);
  const lon = p.lon + ((pNext.lon - p.lon) * phase);
  const acc0 = finiteNum(p.accM);
  const acc1 = finiteNum(pNext.accM);
  const accM = (acc0 !== null && acc1 !== null) ? (acc0 + ((acc1 - acc0) * phase)) : (acc0 ?? acc1 ?? null);
  return {
    lat,
    lon,
    accM,
    label: p.label || fileGps.label || "",
    source: "file:route",
  };
}

function pickReverseLabel(data){
  const a = data?.address || {};
  const road = a.road || a.pedestrian || a.cycleway || a.footway || a.path || "";
  const city = a.city || a.town || a.village || a.municipality || a.county || a.state || "";
  if(road && city) return `${road}, ${city}`;
  if(road) return road;
  if(city) return city;
  const d = (data?.display_name || "").split(",")[0]?.trim();
  return d || "";
}

function shouldReverseGeocode(lat, lon, nowMs){
  if(streetMapState.geoBusy) return false;
  const lastLat = finiteNum(streetMapState.geoLat);
  const lastLon = finiteNum(streetMapState.geoLon);
  const since = nowMs - (streetMapState.geoTsMs || 0);
  if(lastLat === null || lastLon === null) return true;
  const moved = Math.abs(lat - lastLat) + Math.abs(lon - lastLon);
  if(moved >= GEO_REVERSE_MOVE_DEG) return since >= 3000;
  return since >= GEO_REVERSE_MIN_MS;
}

async function reverseGeocode(lat, lon){
  const q = new URLSearchParams({
    format: "jsonv2",
    lat: String(lat),
    lon: String(lon),
    zoom: "18",
    addressdetails: "1",
  });
  const r = await fetch(`${GEO_REVERSE_URL}?${q.toString()}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if(!r.ok) return "";
  const data = await r.json();
  return pickReverseLabel(data);
}

function refreshReverseGeocode(lat, lon){
  const nowMs = Date.now();
  if(!shouldReverseGeocode(lat, lon, nowMs)) return;
  streetMapState.geoBusy = true;
  streetMapState.geoTsMs = nowMs;
  streetMapState.geoLat = lat;
  streetMapState.geoLon = lon;
  void reverseGeocode(lat, lon)
    .then((label) => {
      if(label) streetMapState.geoLabel = label;
    })
    .catch(() => {})
    .finally(() => {
      streetMapState.geoBusy = false;
    });
}

function distMeters(latA, lonA, latB, lonB){
  const x = (lonB - lonA) * 111320 * Math.cos(((latA + latB) * Math.PI) / 360);
  const y = (latB - latA) * 110540;
  return Math.hypot(x, y);
}

function wrapDeg180(v){
  let x = v;
  while(x > 180) x -= 360;
  while(x < -180) x += 360;
  return x;
}

function bearingDeg(latA, lonA, latB, lonB){
  const y = (lonB - lonA) * Math.cos(((latA + latB) * Math.PI) / 360);
  const x = latB - latA;
  const deg = Math.atan2(y, x) * (180 / Math.PI);
  return (deg + 360) % 360;
}

function updateHeading(lat, lon){
  const prevLat = finiteNum(streetMapState.prevLat);
  const prevLon = finiteNum(streetMapState.prevLon);
  streetMapState.prevLat = lat;
  streetMapState.prevLon = lon;
  if(prevLat === null || prevLon === null) return;
  const moveM = distMeters(prevLat, prevLon, lat, lon);
  if(moveM < MAP_HEADING_MIN_MOVE_M) return;
  const raw = bearingDeg(prevLat, prevLon, lat, lon);
  if(!streetMapState.headingReady){
    streetMapState.headingDeg = raw;
    streetMapState.headingReady = true;
    return;
  }
  const cur = streetMapState.headingDeg;
  const delta = wrapDeg180(raw - cur);
  streetMapState.headingDeg = (cur + (delta * 0.28) + 360) % 360;
}

function parseMaxSpeedKph(raw){
  const txt = String(raw || "").trim().toLowerCase();
  if(!txt) return null;
  const mph = txt.match(/(\d+(?:\.\d+)?)\s*(mph|mi\/h)/);
  if(mph){
    const n = Number(mph[1]);
    if(Number.isFinite(n)) return Math.round(n * 1.60934);
    return null;
  }
  const kmh = txt.match(/(\d+(?:\.\d+)?)/);
  if(kmh){
    const n = Number(kmh[1]);
    if(Number.isFinite(n)) return Math.round(n);
  }
  return null;
}

function pickSpeedLimitKph(elements, lat, lon){
  if(!Array.isArray(elements) || !elements.length) return null;
  let best = null;
  for(const el of elements){
    const raw = el?.tags?.maxspeed;
    const kph = parseMaxSpeedKph(raw);
    if(!Number.isFinite(kph)) continue;
    const geom = Array.isArray(el?.geometry) ? el.geometry : [];
    if(!geom.length) continue;
    let dMin = Infinity;
    for(const p of geom){
      const plat = finiteNum(p?.lat);
      const plon = finiteNum(p?.lon);
      if(plat === null || plon === null) continue;
      const d = distMeters(lat, lon, plat, plon);
      if(d < dMin) dMin = d;
    }
    if(!Number.isFinite(dMin)) continue;
    if(!best || dMin < best.d){
      best = { d: dMin, kph };
    }
  }
  if(!best) return null;
  return best.kph;
}

function shouldSpeedLookup(lat, lon, nowMs){
  if(streetMapState.speedBusy) return false;
  const lastLat = finiteNum(streetMapState.speedLat);
  const lastLon = finiteNum(streetMapState.speedLon);
  const since = nowMs - (streetMapState.speedTsMs || 0);
  if(lastLat === null || lastLon === null) return true;
  const moved = Math.abs(lat - lastLat) + Math.abs(lon - lastLon);
  if(moved >= SPEED_LIMIT_MOVE_DEG) return since >= 2500;
  return since >= SPEED_LIMIT_MIN_MS;
}

async function fetchSpeedLimit(lat, lon){
  const q = `[out:json][timeout:8];way(around:45,${lat},${lon})["highway"]["maxspeed"];out tags geom;`;
  const r = await fetch(SPEED_LIMIT_OVERPASS_URL, {
    method: "POST",
    headers: {
      "Content-Type": "text/plain;charset=UTF-8",
      Accept: "application/json",
    },
    body: q,
    cache: "no-store",
  });
  if(!r.ok) return null;
  const data = await r.json();
  return pickSpeedLimitKph(data?.elements || [], lat, lon);
}

function refreshSpeedLimit(lat, lon){
  const nowMs = Date.now();
  if(!shouldSpeedLookup(lat, lon, nowMs)) return;
  streetMapState.speedBusy = true;
  streetMapState.speedTsMs = nowMs;
  streetMapState.speedLat = lat;
  streetMapState.speedLon = lon;
  void fetchSpeedLimit(lat, lon)
    .then((kph) => {
      if(Number.isFinite(kph)){
        streetMapState.speedLabel = String(kph);
      }
    })
    .catch(() => {
      // Keep last known numeric speed-limit value when lookup fails.
    })
    .finally(() => {
      streetMapState.speedBusy = false;
    });
}

async function pollFileGpsOnce(){
  if(streetMapState.filePollBusy) return;
  streetMapState.filePollBusy = true;
  try{
    const r = await fetch(FILE_GPS_URL, { cache: "no-store" });
    if(!r.ok){
      streetMapState.fileGps = null;
      return;
    }
    const data = await r.json();
    streetMapState.fileGps = normalizeFileGps(data);
  }catch{
    streetMapState.fileGps = null;
  }finally{
    streetMapState.filePollBusy = false;
  }
}

function ensureFileGpsPoll(){
  if(streetMapState.filePollStarted) return;
  streetMapState.filePollStarted = true;
  const tick = () => {
    if(!isStreetVisible()) return;
    const now = Date.now();
    if((now - streetMapState.filePollTsMs) < FILE_GPS_POLL_MS) return;
    streetMapState.filePollTsMs = now;
    void pollFileGpsOnce();
  };
  tick();
  window.setInterval(tick, 1000);
}

function ensureGeoWatch(){
  if(streetMapState.watchStarted) return;
  streetMapState.watchStarted = true;
  if(!navigator.geolocation){
    streetMapState.status = "Geolocation API indisponible";
    return;
  }
  navigator.geolocation.watchPosition(
    (pos) => {
      const nowMs = Date.now();
      if(String(streetMapState.source || "").startsWith("file")){
        return;
      }
      if(streetMapState.source === "meta.gps" && (nowMs - streetMapState.tsMs) < 4000){
        return;
      }
      streetMapState.lat = pos.coords.latitude;
      streetMapState.lon = pos.coords.longitude;
      streetMapState.accM = finiteNum(pos.coords.accuracy);
      streetMapState.tsMs = nowMs;
      streetMapState.source = "browser";
      streetMapState.status = "Position GPS reçue";
    },
    (err) => {
      if(String(streetMapState.source || "").startsWith("file")){
        return;
      }
      const msg = err?.code === 1 ? "Permission GPS refusée" : "Signal GPS indisponible";
      streetMapState.status = msg;
    },
    { enableHighAccuracy: true, timeout: 7000, maximumAge: 3000 }
  );
}

function ensureMapNode(){
  if(streetMapState.node) return streetMapState.node;
  const root = document.createElement("div");
  root.className = "street-map street-map-floating";
  root.hidden = true;
  root.innerHTML = `
    <div class="street-map-frame street-map-canvas"></div>
    <img class="street-map-fallback" src="/static/wind-rose-compass.png" alt="Compass fallback">
    <div class="street-speed-limit" id="street-speed-limit" aria-label="Speed limit">
      <span class="street-speed-limit-value">--</span>
    </div>
    <div class="street-map-status" id="street-map-status">GPS en attente</div>
  `;
  root.addEventListener("click", () => {
    streetMapState.expanded = !streetMapState.expanded;
    root.classList.toggle("expanded", streetMapState.expanded);
    streetMapState.lastCenterKey = "";
    const host = $("#street-dashboard");
    if(host){
      positionMapNode(host);
      if(streetMapState.map){
        streetMapState.map.invalidateSize(false);
      }
    }
  });
  document.body.appendChild(root);
  streetMapState.node = root;
  return root;
}

function ensureLeafletMap(){
  if(streetMapState.map) return streetMapState.map;
  if(streetMapState.leafletInitTried) return null;
  streetMapState.leafletInitTried = true;

  const node = ensureMapNode();
  const canvas = node.querySelector(".street-map-canvas");
  if(!canvas || !window.L) return null;

  const map = window.L.map(canvas, {
    zoomControl: false,
    attributionControl: false,
    dragging: false,
    doubleClickZoom: false,
    scrollWheelZoom: false,
    boxZoom: false,
    keyboard: false,
    touchZoom: false,
    updateWhenIdle: false,
  });
  window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    keepBuffer: 32,
  }).addTo(map);
  const vehicleIcon = window.L.divIcon({
    className: "street-map-vehicle-wrap",
    html: `
      <div class="street-map-vehicle" aria-hidden="true">
        <svg viewBox="0 0 64 64" class="car-top" focusable="false">
          <defs>
            <linearGradient id="carBodyGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#d10f24"/>
              <stop offset="52%" stop-color="#a30012"/>
              <stop offset="100%" stop-color="#72000d"/>
            </linearGradient>
            <linearGradient id="carGlassGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#1f2c3a"/>
              <stop offset="100%" stop-color="#101922"/>
            </linearGradient>
          </defs>
          <rect x="18" y="6" width="28" height="52" rx="11" fill="url(#carBodyGrad)" stroke="#430008" stroke-width="2"/>
          <rect x="22" y="14" width="20" height="14" rx="4" fill="url(#carGlassGrad)" stroke="#6e7f93" stroke-width="1"/>
          <rect x="22" y="33" width="20" height="11" rx="4" fill="url(#carGlassGrad)" stroke="#6e7f93" stroke-width="1"/>
          <rect x="16" y="15" width="4" height="9" rx="1.5" fill="#111922"/>
          <rect x="44" y="15" width="4" height="9" rx="1.5" fill="#111922"/>
          <rect x="16" y="35" width="4" height="9" rx="1.5" fill="#111922"/>
          <rect x="44" y="35" width="4" height="9" rx="1.5" fill="#111922"/>
          <circle cx="24" cy="9" r="1.1" fill="#ff5a64"/>
          <circle cx="40" cy="9" r="1.1" fill="#ff5a64"/>
          <circle cx="24" cy="55" r="1.1" fill="#ffb16a"/>
          <circle cx="40" cy="55" r="1.1" fill="#ffb16a"/>
        </svg>
      </div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
  const marker = window.L.marker([0, 0], { icon: vehicleIcon, interactive: false }).addTo(map);
  map.setView([0, 0], MAP_DYNAMIC_ZOOM, { animate: false });

  streetMapState.map = map;
  streetMapState.marker = marker;
  return map;
}

function positionMapNode(host){
  const node = ensureMapNode();
  const panel = host.querySelector(".center-panel");
  if(!panel){
    node.hidden = true;
    return;
  }
  const hostRect = host.getBoundingClientRect();
  const rect = panel.getBoundingClientRect();
  if(!streetMapState.expanded){
    node.classList.remove("expanded");
    node.style.removeProperty("--speed-pin-top");
    node.style.removeProperty("--speed-pin-right");
    node.style.left = `${Math.round(rect.left)}px`;
    node.style.top = `${Math.round(rect.top)}px`;
    node.style.width = `${Math.round(rect.width)}px`;
    node.style.height = `${Math.round(rect.height)}px`;
    return;
  }

  node.classList.add("expanded");
  const pad = 8;
  const expandPad = 1;
  const left = hostRect.left + expandPad;
  const top = hostRect.top + expandPad;
  const width = Math.max(120, hostRect.width - (2 * expandPad));
  const height = Math.max(80, hostRect.height - (2 * expandPad));
  const expandedRight = left + width;
  const pinnedTop = Math.max(8, Math.round((rect.top - top) + 10));
  const pinnedRight = Math.max(8, Math.round((expandedRight - rect.right) + 10));
  node.style.setProperty("--speed-pin-top", `${pinnedTop}px`);
  node.style.setProperty("--speed-pin-right", `${pinnedRight}px`);
  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(top)}px`;
  node.style.width = `${Math.round(width)}px`;
  node.style.height = `${Math.round(height)}px`;
}

function refreshMapNode(payload){
  let gotFreshPoint = false;
  const filePoint = fileGpsPointNow();
  if(filePoint){
    streetMapState.lat = filePoint.lat;
    streetMapState.lon = filePoint.lon;
    streetMapState.accM = filePoint.accM;
    streetMapState.tsMs = Date.now();
    streetMapState.source = filePoint.label ? `file:${filePoint.label}` : filePoint.source;
    streetMapState.status = "Position GPS fichier";
    gotFreshPoint = true;
  }else{
    const gps = readGpsFromPayload(payload);
    if(gps){
      streetMapState.lat = gps.lat;
      streetMapState.lon = gps.lon;
      streetMapState.accM = gps.accM;
      streetMapState.tsMs = Date.now();
      streetMapState.source = gps.source;
      streetMapState.status = "Position GPS bus reçue";
      gotFreshPoint = true;
    }
  }

  const node = ensureMapNode();
  const canvasEl = node.querySelector(".street-map-canvas");
  const fallbackEl = node.querySelector(".street-map-fallback");
  const statusEl = node.querySelector("#street-map-status");
  const speedLimitEl = node.querySelector("#street-speed-limit");
  const speedLimitValueEl = node.querySelector(".street-speed-limit-value");
  if(!statusEl || !canvasEl || !fallbackEl || !speedLimitEl || !speedLimitValueEl) return;

  const lat = finiteNum(streetMapState.lat);
  const lon = finiteNum(streetMapState.lon);
  const ageMs = Date.now() - (streetMapState.tsMs || 0);
  const gpsAlive = (lat !== null && lon !== null && ageMs <= GPS_STALE_MS);
  if(!gpsAlive){
    if(!gotFreshPoint){
      streetMapState.status = "GPS indisponible";
    }
    canvasEl.classList.add("is-hidden");
    fallbackEl.classList.remove("is-hidden");
    statusEl.textContent = streetMapState.status;
    speedLimitValueEl.textContent = streetMapState.speedLabel || "--";
    speedLimitEl.classList.add("is-muted");
    speedLimitEl.classList.remove("is-3d", "is-4d");
    canvasEl.style.transform = "";
    streetMapState.geoLabel = "";
    return;
  }

  canvasEl.classList.remove("is-hidden");
  fallbackEl.classList.add("is-hidden");
  statusEl.textContent = streetMapState.status;
  refreshReverseGeocode(lat, lon);
  refreshSpeedLimit(lat, lon);
  if(streetMapState.geoLabel){
    statusEl.textContent = streetMapState.geoLabel;
  }
  const speedLabel = streetMapState.speedLabel || "--";
  speedLimitValueEl.textContent = speedLabel;
  const speedNum = Number(speedLabel);
  const digits = Number.isFinite(speedNum) ? String(Math.round(Math.abs(speedNum))).length : 0;
  const hasNumericSpeedLimit = Number.isFinite(speedNum);
  if(hasNumericSpeedLimit){
    streetMapState.fastViewOn = speedNum >= 90;
  }
  const isFastRoad = streetMapState.fastViewOn === true;
  speedLimitEl.classList.toggle("is-muted", !Number.isFinite(speedNum));
  speedLimitEl.classList.toggle("is-3d", digits === 3);
  speedLimitEl.classList.toggle("is-4d", digits >= 4);
  canvasEl.classList.toggle("is-fast-3d", isFastRoad);
  updateHeading(lat, lon);
  const rotateDeg = -streetMapState.headingDeg;
  if(isFastRoad){
    canvasEl.style.transform = `rotateX(46deg) rotate(${rotateDeg.toFixed(2)}deg) scale(1.46) translateY(32px)`;
  }else{
    canvasEl.style.transform = `rotateX(18deg) rotate(${rotateDeg.toFixed(2)}deg) scale(1.24) translateY(10px)`;
  }
  const mapZoom = isFastRoad ? MAP_FAST_ZOOM : MAP_DYNAMIC_ZOOM;

  const key = `${lat.toFixed(5)}:${lon.toFixed(5)}:${Math.round(finiteNum(streetMapState.accM) || 0)}:${mapZoom.toFixed(1)}`;
  if(key === streetMapState.lastCenterKey) return;
  streetMapState.lastCenterKey = key;

  const map = ensureLeafletMap();
  if(!map){
    statusEl.textContent = "Carte indisponible (Leaflet non charge)";
    return;
  }
  map.setView([lat, lon], mapZoom, { animate: false });
  if(streetMapState.marker){
    streetMapState.marker.setLatLng([lat, lon]);
    const markerEl = streetMapState.marker.getElement();
    const vehicleEl = markerEl?.querySelector(".street-map-vehicle");
    if(vehicleEl){
      vehicleEl.style.setProperty("--heading-deg", `${streetMapState.headingDeg.toFixed(1)}deg`);
    }
  }
}

export function renderStreet(payload, options = {}){
  const enableMap = options.enableMap !== false;
  const enableBottomMetrics = options.enableBottomMetrics === true;
  const streetVisible = isStreetVisible();
  if(enableMap && streetVisible){
    ensureFileGpsPoll();
    ensureGeoWatch();
  }
  const rpm = getSig(payload,"RPM") || getAny(payload, "RPM");
  const spd = getSig(payload,"Speed") || getAny(payload, "Speed");
  const bst = getSig(payload,"Boost");
  const thr = getSig(payload,"Throttle");
  const load = getSig(payload,"Load");
  const iat = getAny(payload, "IAT_C");
  const oil = getFirst(payload, ["OilTemp", "OilTemp_C", "Oil_Temp", "OilTemperature"]);
  const fuel = getFirst(payload, ["FuelLevel", "Fuel", "FuelPct", "FuelPercent"]);
  const coolant = getFirst(payload, ["CoolantTemp", "ECT", "WaterTemp", "Coolant"]);
  const gear = getAny(payload, "Gear");

  const speed = spd ? spd.v : null;
  const rpmVal = rpm ? rpm.v : null;
  const nowMs = Date.now();
  const rpmMax3s = rollingMax(rpmHistory, rpmVal, nowMs);
  const speedMax3s = rollingMax(speedHistory, speed, nowMs);
  const blinkOn = Math.floor(nowMs / 420) % 2 === 0;

  const rpmPct = pct(rpmVal || 0, 0, 8000);
  const spdPct = speedArcT(speed || 0) * 100;
  const showRpmShadow = Number.isFinite(rpmMax3s) && Number.isFinite(rpmVal) && rpmMax3s > (Number(rpmVal) + 0.5);
  const showSpdShadow = Number.isFinite(speedMax3s) && Number.isFinite(speed) && speedMax3s > (Number(speed) + 0.2);
  const rpmShadowPct = showRpmShadow ? pct(rpmMax3s, 0, 8000) : 0;
  const spdShadowPct = showSpdShadow ? (speedArcT(speedMax3s) * 100) : 0;
  const boostPct = pct(bst?.v || 0, -1.0, 2.0);
  const loadPct = pct(load?.v || 0, 0, 100);
  const thrPct = pct(thr?.v || 0, 0, 100);
  const engagedGear = gearDisplay(gear?.v);
  const dtcCount = sigNum(payload, ["DTC_Count", "DTC_count", "DTCCount"], 0);
  const milOn = sigOn(payload, ["MIL"]);
  const epcOn = sigOn(payload, ["EPC"]);
  const brakeOn = sigOn(payload, ["BrakeSwitch", "Brake"]);
  const parkOn = sigOn(payload, ["ClutchSwitch"]);
  const cruiseOn = sigOn(payload, ["Cruise", "CruiseActive"]);
  const oilText = Number.isFinite(Number(oil?.v)) ? `${int(oil?.v)}°C` : "—";
  const fuelText = Number.isFinite(Number(fuel?.v)) ? `${int(fuel?.v)}%` : "—";
  const boostText = Number.isFinite(Number(bst?.v)) ? `${fmt(bst?.v, 1)} bar` : "—";
  const waterText = Number.isFinite(Number(coolant?.v)) ? `${int(coolant?.v)}°C` : "—";

  const warningTopItems = [
    { kind: "arrow", on: cruiseOn && blinkOn, title: "Left indicator", dir: "left", text: "\u25b6" },
    { kind: "img", on: milOn, title: "Engine", src: "/static/warning_icons/check_engine_128.png" },
    { kind: "img", on: dtcCount > 1 || milOn || epcOn, title: "Master warning", src: "/static/warning_icons/warning_triangle_128.png" },
    { kind: "img", on: parkOn || (brakeOn && (Number(speed) < 2)), title: "Parking brake", src: "/static/warning_icons/parking_brake_128.png" },
    { kind: "arrow", on: cruiseOn && !blinkOn, title: "Right indicator", dir: "right", text: "\u25b6" },
  ];
  const warningTopHtml = warningTopItems.map((item) => {
    if(item.kind === "arrow"){
      return `<span class="warning-led warning-arrow arrow-${item.dir} ${item.on ? "on" : "off"}" title="${item.title}">${item.text}</span>`;
    }
    return `<span class="warning-led ${item.on ? "on" : "off"}" title="${item.title}"><img src="${item.src}" alt="${item.title}"></span>`;
  }).join("");

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
  const speedMajorVals = [0, 20, 40, 60, 80, 100, 140, 180, 220, 260, 300];
  const speedLabels = speedMajorVals.map((v) => {
    const a = dialAngle01(speedArcT(v));
    const rad = (a * Math.PI) / 180.0;
    const x = 50 + (37.3 * Math.cos(rad));
    const y = 50 + (37.3 * Math.sin(rad));
    return `<span class="dial-num" style="left:${x}%;top:${y}%;">${v}</span>`;
  }).join("");

  const speedTicks = [];
  for(let i = 0; i < speedMajorVals.length - 1; i++){
    const t0 = speedArcT(speedMajorVals[i]);
    const t1 = speedArcT(speedMajorVals[i + 1]);
    speedTicks.push({ t: t0, major: true });
    for(let j = 1; j <= 3; j++){
      speedTicks.push({ t: t0 + ((t1 - t0) * (j / 4)), major: false });
    }
  }
  speedTicks.push({ t: speedArcT(speedMajorVals[speedMajorVals.length - 1]), major: true });
  const speedTicksHtml = speedTicks.map(({ t, major }) => {
    const a = dialAngle01(t);
    const rad = (a * Math.PI) / 180.0;
    const radius = major ? 46.8 : 47.6;
    const x = 50 + (radius * Math.cos(rad));
    const y = 50 + (radius * Math.sin(rad));
    const cls = major ? "dial-tick major" : "dial-tick minor";
    return `<span class="${cls}" style="left:${x}%;top:${y}%;transform:translate(-50%,-50%) rotate(${a + 90}deg);"></span>`;
  }).join("");

  host.innerHTML = `
    <div class="audi-classic" style="--rpm:${rpmPct}; --spd:${spdPct}; --boost:${boostPct}; --load:${loadPct}; --thr:${thrPct};">
      <div class="top-bar">
        <div class="top-left"></div>
        <div class="top-right"></div>
      </div>
      <div class="warning-top">
        <svg class="warning-outline" viewBox="0 0 420 86" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="warning-trace-grad" x1="0" y1="34" x2="0" y2="-6" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stop-color="#c6ceda" stop-opacity="0.78"></stop>
              <stop offset="62%" stop-color="#c6ceda" stop-opacity="0.44"></stop>
              <stop offset="100%" stop-color="#c6ceda" stop-opacity="0.00"></stop>
            </linearGradient>
          </defs>
          <path class="fill" d="M-81 -6 L-17.1 29.2 A42 42 0 0 0 0 34 L420 34 A42 42 0 0 0 437.1 29.2 L501 -6 L-81 -6 Z"></path>
          <path class="trace" stroke="url(#warning-trace-grad)" d="M-81 -6 L-17.1 29.2 A42 42 0 0 0 0 34 L420 34 A42 42 0 0 0 437.1 29.2 L501 -6"></path>
        </svg>
        <div class="warning-top-strip" aria-label="Warning indicators">
          ${warningTopHtml}
        </div>
      </div>

      <div class="cluster">
        <div class="dial dial-left" style="--p:${rpmPct};">
          <div class="dial-ring"></div>
          <div class="dial-ring-outer"></div>
          <div class="dial-inner">
            <div class="dial-big dial-gear">${engagedGear}</div>
          </div>
          ${showRpmShadow ? `
          <div class="dial-needle dial-needle-left dial-needle-shadow" style="--p:${rpmShadowPct};">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>` : ""}
          <div class="dial-needle dial-needle-left" style="--p:${rpmPct};">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>
          <div class="dial-ticks">${dialTicks}${dialTicksRedFine}</div>
          <div class="dial-labels left-labels">${dialLabels}</div>
          <div class="dial-redline"></div>
        </div>

        <div class="center-panel">
        </div>

        <div class="dial dial-right">
          <div class="dial-ring"></div>
          <div class="dial-ring-outer"></div>
          <div class="dial-inner">
            <div class="dial-big dial-gear">${int(speed)}</div>
          </div>
          ${showSpdShadow ? `
          <div class="dial-needle dial-needle-right dial-needle-shadow" style="--p:${spdShadowPct}">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>` : ""}
          <div class="dial-needle dial-needle-right" style="--p:${spdPct}">
            <span class="dial-needle-line"></span>
            <span class="dial-needle-hub"></span>
          </div>
          <div class="dial-ticks">${speedTicksHtml}</div>
          <div class="dial-labels right-labels">${speedLabels}</div>
        </div>
      </div>

      <div class="warning-bottom ${enableBottomMetrics ? "with-metrics" : ""}" ${enableBottomMetrics ? "" : 'aria-hidden="true"'}>
        <svg class="warning-outline-bottom" viewBox="0 0 420 86" preserveAspectRatio="none">
          <defs>
            <linearGradient id="warning-trace-grad-bottom" x1="0" y1="34" x2="0" y2="-6" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stop-color="#c6ceda" stop-opacity="0.78"></stop>
              <stop offset="62%" stop-color="#c6ceda" stop-opacity="0.44"></stop>
              <stop offset="100%" stop-color="#c6ceda" stop-opacity="0.00"></stop>
            </linearGradient>
          </defs>
          <path class="fill" d="M-81 -6 L-17.1 29.2 A42 42 0 0 0 0 34 L420 34 A42 42 0 0 0 437.1 29.2 L501 -6 L-81 -6 Z"></path>
          <path class="trace" stroke="url(#warning-trace-grad-bottom)" d="M-81 -6 L-17.1 29.2 A42 42 0 0 0 0 34 L420 34 A42 42 0 0 0 437.1 29.2 L501 -6"></path>
        </svg>
        ${enableBottomMetrics ? `
        <div class="warning-bottom-strip" aria-label="Lower metrics">
          <div class="wb-item"><span class="wb-k">OIL</span><span class="wb-v">${oilText}</span></div>
          <div class="wb-item"><span class="wb-k">FUEL</span><span class="wb-v">${fuelText}</span></div>
          <div class="wb-item"><span class="wb-k">BOOST</span><span class="wb-v">${boostText}</span></div>
          <div class="wb-item"><span class="wb-k">COOLANT</span><span class="wb-v">${waterText}</span></div>
        </div>` : ""}
      </div>

      <div class="bottom-bar">
        <div class="bottom-left">${timeHHMM()}</div>
        <div class="bottom-center"></div>
        <div class="bottom-right">${int(iat?.v)}.0°C</div>
      </div>
    </div>
  `;

  if(enableMap && streetVisible){
    const node = ensureMapNode();
    node.hidden = false;
    positionMapNode(host);
    if(streetMapState.map){
      streetMapState.map.invalidateSize(false);
    }
    refreshMapNode(payload);
  }else if(streetMapState.node){
    streetMapState.node.hidden = true;
  }
}
