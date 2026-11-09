#!/usr/bin/env python3
import asyncio, json, subprocess, os, tempfile, time
from talk_chromium import get_talk_ui, PAGE_STATE_JS

HOOK = r"""
(() => {
  if (window.__gsm2DcHook2) return "already";
  window.__gsm2Dcs = [];
  window.__gsm2AllMsgs = [];
  const Orig = RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel = function(...args) {
    const dc = Orig.apply(this, args);
    window.__gsm2ActiveDc = dc;
    const rec = {label: dc.label, msgs: [], sends: []};
    window.__gsm2Dcs.push(rec);
    dc.addEventListener("message", (ev) => {
      const data = typeof ev.data === "string" ? ev.data : "[bin]";
      rec.msgs.push(data);
      window.__gsm2AllMsgs.push(data);
      if (rec.msgs.length > 80) rec.msgs.shift();
    });
    const origSend = dc.send.bind(dc);
    dc.send = (data) => {
      const s = typeof data === "string" ? data : "[bin]";
      rec.sends.push(s.slice(0, 500));
      return origSend(data);
    };
    return dc;
  };
  window.__gsm2DcHook2 = true;
  return "hooked";
})()
"""

FORCE = r"""
(() => {
  const dc = window.__gsm2ActiveDc;
  if (!dc || dc.readyState !== "open") return {ok:false, state: dc && dc.readyState};
  // Ask model to speak immediately
  dc.send(JSON.stringify({
    type: "response.create",
    response: {
      output_modalities: ["audio"],
      instructions: "Say hello out loud right now in one short sentence."
    }
  }));
  return {ok:true, state: dc.readyState};
})()
"""

SESSION_SNAP = r"""
(() => {
  const msgs = window.__gsm2AllMsgs || [];
  // return last few parsed types + first session.created full if small
  const types = msgs.map(m => { try { return JSON.parse(m).type; } catch(e){ return "parse_err"; } });
  let created = null;
  for (const m of msgs) {
    try {
      const j = JSON.parse(m);
      if (j.type === "session.created") { created = j.session; break; }
    } catch(e) {}
  }
  return {types, created, n: msgs.length, sends: (window.__gsm2Dcs[0]&&window.__gsm2Dcs[0].sends)||[]};
})()
"""

ANALYSER = r"""
(() => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected");
  if (!pc) return {err:"no-pc"};
  const recv = pc.getReceivers().find(r => r.track && r.track.kind === "audio");
  if (!recv) return {err:"no-recv"};
  if (!window.__gsm2A2) {
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(new MediaStream([recv.track]));
    const an = ctx.createAnalyser(); an.fftSize=2048; src.connect(an);
    window.__gsm2A2 = {ctx, an, track: recv.track};
  }
  const a = window.__gsm2A2;
  if (a.ctx.state==="suspended") a.ctx.resume();
  const buf = new Uint8Array(a.an.fftSize);
  a.an.getByteTimeDomainData(buf);
  let peak=0; for (const v of buf) peak=Math.max(peak, Math.abs(v-128));
  return {peak, muted: a.track.muted, typesTail: (window.__gsm2AllMsgs||[]).slice(-5).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}})};
})()
"""


async def main():
    ui = get_talk_ui()
    session = await ui._connect_page()
    await session.evaluate(HOOK)
    await session.close()

    await ui.start_talk()
    session = await ui._connect_page()
    await session.evaluate(HOOK)  # noop if already

    for i in range(20):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText") == "Listening...":
            break
        await asyncio.sleep(0.25)

    snap = await session.evaluate(SESSION_SNAP)
    # redact long instructions in print
    created = snap.get("created") or {}
    safe = {k: created.get(k) for k in ("id","model","output_modalities","voice","audio","turn_detection","tools","tool_choice","max_output_tokens","speed","tracing") if k in created or True}
    # include nested audio if present
    if "audio" in created: safe["audio"] = created["audio"]
    if "turn_detection" in created: safe["turn_detection"] = created["turn_detection"]
    if "voice" in created: safe["voice"] = created["voice"]
    print("TYPES_BEFORE", snap.get("types"))
    print("SENDS_BEFORE", snap.get("sends"))
    print("SESSION", json.dumps(safe, indent=2)[:4000])

    # start bus recorder
    tmp = tempfile.mkdtemp(prefix="force-")
    bus = os.path.join(tmp, "bus.wav")
    rec = subprocess.Popen(["pw-record","--target","openclaw_bus.monitor","--rate","48000","--channels","2", bus],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    forced = await session.evaluate(FORCE)
    print("FORCE", forced)

    for i in range(25):
        a = await session.evaluate(ANALYSER)
        print(f"i={i} analyser={a}")
        if isinstance(a, dict) and (a.get("peak") or 0) > 5:
            print("GOT_AUDIO_PEAK")
            await asyncio.sleep(4)
            break
        await asyncio.sleep(0.4)

    snap2 = await session.evaluate(SESSION_SNAP)
    print("TYPES_AFTER", snap2.get("types"))
    print("SENDS_AFTER", snap2.get("sends"))

    rec.terminate()
    try: rec.wait(timeout=2)
    except Exception: rec.kill()
    # volumedetect
    if os.path.exists(bus) and os.path.getsize(bus)>1000:
        vd = subprocess.run(["ffmpeg","-i",bus,"-af","volumedetect","-f","null","-"], capture_output=True, text=True)
        for ln in vd.stderr.splitlines():
            if "volume" in ln: print(ln.strip())
        print("BUS_BYTES", os.path.getsize(bus))

    await session.close()
    await ui.stop_talk()

asyncio.run(main())
