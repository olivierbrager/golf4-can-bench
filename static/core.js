export class Core {
  constructor({ wsEl, dotEl, srcEl }){
    this.wsEl = wsEl;
    this.dotEl = dotEl;
    this.srcEl = srcEl;
    this.handlers = [];
    this.ws = null;
  }

  onPayload(fn){ this.handlers.push(fn); }

  _setConn(state){
    if(state === "ok"){
      this.wsEl.textContent = "connected";
      this.dotEl.classList.add("ok"); this.dotEl.classList.remove("bad");
    }else if(state === "bad"){
      this.wsEl.textContent = "disconnected";
      this.dotEl.classList.add("bad"); this.dotEl.classList.remove("ok");
    }else{
      this.wsEl.textContent = "connecting";
      this.dotEl.classList.remove("ok"); this.dotEl.classList.remove("bad");
    }
  }

  _wsUrl(){
    const proto = (location.protocol === "https:") ? "wss" : "ws";
    return `${proto}://${location.host}/ws`;
  }

  connect(){
    this._setConn("connecting");
    const ws = new WebSocket(this._wsUrl());
    this.ws = ws;

    ws.onopen = () => this._setConn("ok");
    ws.onclose = () => { this._setConn("bad"); setTimeout(() => this.connect(), 800); };
    ws.onerror = () => { try{ ws.close(); }catch(_e){} };
    ws.onmessage = (ev) => {
      let payload;
      try{ payload = JSON.parse(ev.data); }catch(_e){ return; }
      const m = payload?.meta || {};
      this.srcEl.textContent = `CAN:${m.src||"—"} | DBC:${m.dbc||"—"} | rx:${m.rx_total||0}/${m.rx_decoded||0} | stale:${m.stale ? "yes":"no"}`;
      for(const fn of this.handlers) fn(payload);
    };
  }
}
