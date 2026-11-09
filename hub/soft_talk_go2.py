#!/usr/bin/env python3
import asyncio, json, subprocess, os
from talk_chromium import get_talk_ui, PAGE_STATE_JS, ensure_openclaw_mic_remap
from pipewire_target import resolve_pipewire_record_target

HOOK = r"""
(() => {
  if (window.__gsm2Go2) return "already";
  window.__gsm2AllMsgs=[];
  const Orig=RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel=function(...a){
    const dc=Orig.apply(this,a); window.__gsm2ActiveDc=dc;
    dc.addEventListener("message",ev=>{ if(typeof ev.data==="string") window.__gsm2AllMsgs.push(ev.data); });
    return dc;
  };
  window.__gsm2Go2=true; return "hooked";
})()
"""

MICJS = r"""
(async () => {
  const pcs=window.__gsm2TalkPcs||[];
  const pc=[...pcs].reverse().find(p=>p.connectionState==="connected");
  let peak=0, label=null;
  if (pc) {
    for (const s of pc.getSenders()) {
      if (!(s.track&&s.track.kind==="audio")) continue;
      label=s.track.label;
      const ctx=new AudioContext();
      const src=ctx.createMediaStreamSource(new MediaStream([s.track]));
      const an=ctx.createAnalyser(); an.fftSize=2048; src.connect(an);
      await new Promise(r=>setTimeout(r,150));
      const buf=new Uint8Array(an.fftSize); an.getByteTimeDomainData(buf);
      for (const v of buf) peak=Math.max(peak, Math.abs(v-128));
      ctx.close();
    }
  }
  const types=(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}});
  return {label, peak, typesTail: types.slice(-15)};
})()
"""


async def main():
    await ensure_openclaw_mic_remap()
    ui=get_talk_ui()
    s=await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session=await ui._connect_page()
    for _ in range(30):
        st=await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...":
            break
        await asyncio.sleep(0.3)

    # show chromium capture source
    so=subprocess.check_output(["pactl","list","source-outputs"], text=True, errors="replace")
    for b in so.split("Source Output #")[1:]:
        if "Chromium" in b:
            src=next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Source:")), "?")
            cork=next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Corked:")), "?")
            print("CHROMIUM_CAP Source=", src, "Corked=", cork)

    # map source ids
    print(subprocess.check_output(["pactl","list","short","sources"], text=True))

    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i","sine=f=700:d=4","-ar","48000","-ac","2","/tmp/longtone.wav"], capture_output=True)
    play=subprocess.Popen(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/longtone.wav"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.5)

    # peaks on mic and phone
    for name in ("phone_uplink.monitor", "openclaw_mic"):
        ser=await resolve_pipewire_record_target(name)
        path=f"/tmp/{name}.wav"
        r=subprocess.Popen(["pw-record","--target",ser,"--rate","48000","--channels","2",path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(1.0)
        r.terminate(); r.wait(timeout=3)
        vd=subprocess.run(["ffmpeg","-i",path,"-af","volumedetect","-f","null","-"],capture_output=True,text=True)
        for ln in vd.stderr.splitlines():
            if "max_volume" in ln: print(name, ln.strip())

    diag=await session.evaluate(MICJS, await_promise=True, timeout_s=15)
    print("SENDER", diag)

    bus_ser=await resolve_pipewire_record_target("openclaw_bus.monitor")
    bus="/tmp/go2_bus.wav"
    rec=subprocess.Popen(["pw-record","--target",bus_ser,"--rate","48000","--channels","2",bus],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # wait for VAD/response while tone continues
    for i in range(20):
        diag=await session.evaluate(MICJS, await_promise=True, timeout_s=15)
        print(f"i={i} peak={diag.get('peak')} types={diag.get('typesTail')}")
        if any(t.startswith("response.") or t.startswith("input_audio") for t in (diag.get("typesTail") or [])):
            await asyncio.sleep(5)
            break
        await asyncio.sleep(0.5)

    play.terminate()
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i",bus,"-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    for ln in vd.stderr.splitlines():
        if "volume" in ln: print("BUS", ln.strip())

    # if no VAD, force response.create to prove downlink still works
    forced=await session.evaluate(r"""
(() => {
  const dc=window.__gsm2ActiveDc;
  if (!dc||dc.readyState!=="open") return {ok:false, state:dc&&dc.readyState};
  dc.send(JSON.stringify({type:"response.create", response:{output_modalities:["audio"], instructions:"Say hello briefly."}}));
  return {ok:true};
})()
""")
    print("FORCE", forced)
    bus2="/tmp/go2_bus_force.wav"
    rec=subprocess.Popen(["pw-record","--target",bus_ser,"--rate","48000","--channels","2",bus2],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(5)
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i",bus2,"-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    for ln in vd.stderr.splitlines():
        if "volume" in ln: print("BUS_FORCE", ln.strip())

    await session.close(); await ui.stop_talk()

asyncio.run(main())
