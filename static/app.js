import { Core } from "./core.js";
import { renderDebug } from "./views/debug.js";
import { renderDev } from "./views/dev.js";
import { renderStreet } from "./views/street.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const qs = new URLSearchParams(window.location.search);
const fullscreenStreet = qs.get("fullscreen") === "street";
const streetMapEnabled = qs.get("map") !== "0";

function showView(name){
  $$("#tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
}

$("#tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if(!t) return;
  showView(t.dataset.view);
});

$("#open-street-fs")?.addEventListener("click", () => {
  const url = new URL(window.location.href);
  url.searchParams.set("fullscreen", "street");
  window.open(url.toString(), "_blank", "noopener");
});

if(fullscreenStreet){
  document.body.classList.add("fullscreen-street");
  showView("street");
}

const core = new Core({
  wsEl: $("#ws"),
  dotEl: $("#dot"),
  srcEl: $("#src"),
});

core.onPayload((p) => {
  renderDebug(p);
  renderDev(p);
  renderStreet(p, { enableMap: streetMapEnabled });
});

// Render a baseline shell immediately (before first WS payload), useful for fullscreen mode.
renderStreet({ signals:{}, raw:{}, meta:{} }, { enableMap: streetMapEnabled });

core.connect();
