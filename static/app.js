import { Core } from "./core.js";
import { renderDebug } from "./views/debug.js";
import { renderDev } from "./views/dev.js";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

function showView(name){
  $$("#tabs .tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  $$(".view").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
}

$("#tabs").addEventListener("click", (e) => {
  const t = e.target.closest(".tab");
  if(!t) return;
  showView(t.dataset.view);
});

const core = new Core({
  wsEl: $("#ws"),
  dotEl: $("#dot"),
  srcEl: $("#src"),
});

core.onPayload((p) => {
  renderDebug(p);
  renderDev(p);
});

core.connect();
