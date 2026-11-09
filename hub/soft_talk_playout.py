#!/usr/bin/env python3
import asyncio, json, subprocess, os, tempfile, wave, struct
from talk_chromium import get_talk_ui, PAGE_STATE_JS

HOOK = r"""
(() => {
  if (window.__gsm2DcHook3) return "already";
  window.__gsm2AllMsgs = [];
  const Orig = RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel = function(...args) {
    const dc = Orig.apply(this, args);
    window.__gsm2ActiveDc = dc;
    dc.addEventListener("message", (ev) => {
      if (typeof ev.data === "string") window.__gsm2AllMsgs.push(ev.data);
    });
    return dc;
  };
  window.__gsm2DcHook3 = true;
  return "hooked";
})()
"""

FORCE_AND_PLAY = r"""
(() => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected");
  if (!pc) return {ok:false, err:"no-pc"};
  const recv = pc.getReceivers().find(r => r.track && r.track.kind === "audio");
  if (!recv) return {ok:false, err:"no-recv"};
  // Force WebAudio playout to system sink (PULSE_SINK)
  if (!window.__gsm2ForcePlay) {
    const ctx = new AudioContext();
    const stream = new MediaStream([recv.track]);
    const src = ctx.createMediaStreamSource(stream);
    const gain = ctx.createGain();
    gain.gain.value = 1.0;
    src.connect(gain);
    gain.connect(ctx.destination);
    const an = ctx.createAnalyser(); an.fftSize=2048; gain.connect(an);
    window.__gsm2ForcePlay = {ctx, an, track: recv.track};
  }
  if (window.__gsm2ForcePlay.ctx.state === "suspended") window.__gsm2ForcePlay.ctx.resume();
  // Also ensure audio element playing
  for (const el of document.querySelectorAll("audio")) {
    el.muted = false; el.volume = 1;
    try { el.play(); } catch(e) {}
  }
  const dc = window.__gsm2ActiveDc;
  if (!dc || dc.readyState !== "open") return {ok:false, err:"no-dc", state: dc && dc.readyState};
  dc.send(JSON.stringify({
    type: "response.create",
    response: {
      output_modalities: ["audio"],
      instructions: "Count out loud from one to five slowly."
    }
  }));
  return {ok:true, ctx: window.__gsm2ForcePlay.ctx.state};
})()
"""

READ = r"""
(() => {
  const a = window.__gsm2ForcePlay;
  let peak = 0;
  if (a) {
    const buf = new Uint8Array(a.an.fftSize);
    a.an.getByteTimeDomainData(buf);
    for (const v of buf) peak = Math.max(peak, Math.abs(v-128));
  }
  const types = (window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}});
  let session = null;
  for (const m of window.__gsm2AllMsgs||[]) {
    try { const j=JSON.parse(m); if (j.type==="session.created") { session=j.session; break; } } catch(e) {}
  }
  const audios=[...document.querySelectorAll("audio")].map(el=>({muted:el.muted,volume:el.volume,paused:el.paused,rs:el.readyState}));
  return {peak, typesTail: types.slice(-8), nTypes: types.length, audios,
    voice: session && (session.voice || (session.audio&&session.audio.output&&session.audio.output.voice)),
    turn_detection: session && session.turn_detection,
    output_modalities: session && session.output_modalities,
    model: session && session.model,
    instructions_head: session && String(session.instructions||"").slice(0,180),
  };
})()
"""


def peak_wav(path):
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        return -1
    with wave.open(path, "r") as w:
        raw = w.readframes(w.getnframes())
    if w.getsampwidth() != 2:
        return -2
    n = len(raw)//2
    samples = struct.unpack("<"+"h"*n, raw)
    return max(abs(s) for s in samples)/32768.0


async def main():
    ui = get_talk_ui()
    s = await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session = await ui._connect_page()
    for _ in range(20):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...":
            break
        await asyncio.sleep(0.25)

    tmp = tempfile.mkdtemp(prefix="playout-")
    bus = os.path.join(tmp, "bus.wav")
    gsm = os.path.join(tmp, "gsm.wav")
    rec1 = subprocess.Popen(["pw-record","--target","openclaw_bus.monitor","--rate","48000","--channels","2", bus],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rec2 = subprocess.Popen(["pw-record","--target","gsm_bus.monitor","--rate","48000","--channels","2", gsm],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("FORCE", await session.evaluate(FORCE_AND_PLAY))
    for i in range(30):
        r = await session.evaluate(READ)
        # summarize sinks live
        sinks = subprocess.check_output(["pactl","list","sink-inputs"], text=True, errors="replace")
        si = []
        for b in sinks.split("Sink Input #")[1:]:
            if "Chromium" in b:
                sink = next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Sink:")), "?")
                cork = next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Corked:")), "?")
                si.append(f"sink={sink} cork={cork}")
        print(f"i={i} peak={r.get('peak')} types={r.get('typesTail')} si={si} voice={r.get('voice')} turn={r.get('turn_detection')}")
        if i == 0:
            print("SESSION_META", {k:r.get(k) for k in ("model","voice","turn_detection","output_modalities","instructions_head")})
        if (r.get("peak") or 0) > 5 and i > 5:
            await asyncio.sleep(2)
            break
        await asyncio.sleep(0.4)

    for rec in (rec1, rec2):
        rec.terminate()
        try: rec.wait(timeout=2)
        except Exception: rec.kill()
    print("BUS_PEAK", peak_wav(bus), "GSM_PEAK", peak_wav(gsm))
    for name, path in [("bus", bus), ("gsm", gsm)]:
        if os.path.exists(path) and os.path.getsize(path)>1000:
            vd = subprocess.run(["ffmpeg","-i",path,"-af","volumedetect","-f","null","-"], capture_output=True, text=True)
            for ln in vd.stderr.splitlines():
                if "mean_volume" in ln or "max_volume" in ln:
                    print(name, ln.strip())

    await session.close()
    await ui.stop_talk()

asyncio.run(main())
