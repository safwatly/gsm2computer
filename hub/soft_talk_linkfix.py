#!/usr/bin/env python3
import asyncio, subprocess, json
from talk_chromium import get_talk_ui, PAGE_STATE_JS, ensure_openclaw_mic_remap
from pipewire_target import resolve_pipewire_record_target

HOOK = r"""
(() => {
  if (window.__gsm2Lf) return "already";
  window.__gsm2AllMsgs=[];
  const Orig=RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel=function(...a){
    const dc=Orig.apply(this,a); window.__gsm2ActiveDc=dc;
    dc.addEventListener("message",ev=>{ if(typeof ev.data==="string") window.__gsm2AllMsgs.push(ev.data); });
    return dc;
  };
  window.__gsm2Lf=true; return "hooked";
})()
"""

PEAK = r"""
(async () => {
  const pcs=window.__gsm2TalkPcs||[];
  const pc=[...pcs].reverse().find(p=>p.connectionState==="connected");
  if (!pc) return {err:"no-pc"};
  let peak=0;
  for (const s of pc.getSenders()) {
    if (!(s.track&&s.track.kind==="audio")) continue;
    const ctx=new AudioContext();
    const src=ctx.createMediaStreamSource(new MediaStream([s.track]));
    const an=ctx.createAnalyser(); an.fftSize=2048; src.connect(an);
    await new Promise(r=>setTimeout(r,100));
    const buf=new Uint8Array(an.fftSize); an.getByteTimeDomainData(buf);
    for (const v of buf) peak=Math.max(peak, Math.abs(v-128));
    ctx.close();
  }
  const types=(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}});
  return {peak, typesTail: types.slice(-10)};
})()
"""


def link_uplink_to_chromium():
    outs = subprocess.check_output(["pw-link","-o"], text=True).splitlines()
    inns = subprocess.check_output(["pw-link","-i"], text=True).splitlines()
    up = [l.strip() for l in outs if "phone_uplink" in l and "monitor" in l]
    cr = [l.strip() for l in inns if "hromium" in l.lower() and ("input" in l.lower() or "capture" in l.lower())]
    print("UP", up)
    print("CR", cr)
    for a,b in zip(up[:2], cr[:2]):
        r=subprocess.run(["pw-link", a, b], capture_output=True, text=True)
        print("link", a, "->", b, "rc", r.returncode, (r.stderr or r.stdout)[:80])


async def main():
    await ensure_openclaw_mic_remap()
    ui=get_talk_ui()
    s=await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session=await ui._connect_page()
    for _ in range(25):
        st=await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...": break
        await asyncio.sleep(0.25)

    print("=== before explicit link ===")
    print(subprocess.check_output(["pw-link","-l"], text=True, errors="replace"))
    link_uplink_to_chromium()
    print("=== after explicit link ===")
    print("\n".join([ln for ln in subprocess.check_output(["pw-link","-l"], text=True).splitlines() if "hromium" in ln.lower() or "phone_uplink" in ln or "openclaw" in ln]))

    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i","sine=f=700:d=6","-ar","48000","-ac","2","/tmp/longtone.wav"], capture_output=True)
    play=subprocess.Popen(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/longtone.wav"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bus_ser=await resolve_pipewire_record_target("openclaw_bus.monitor")
    rec=subprocess.Popen(["pw-record","--target",bus_ser,"--rate","48000","--channels","2","/tmp/lf_bus.wav"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(16):
        d=await session.evaluate(PEAK, await_promise=True, timeout_s=10)
        print(f"i={i} {d}")
        if any(str(t).startswith("response.") or str(t).startswith("input_audio") for t in (d.get("typesTail") or [])):
            print("VAD_OR_RESPONSE")
            await asyncio.sleep(5)
            break
        await asyncio.sleep(0.4)
    play.terminate()
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i","/tmp/lf_bus.wav","-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    for ln in vd.stderr.splitlines():
        if "volume" in ln: print(ln.strip())
    await session.close(); await ui.stop_talk()

asyncio.run(main())
