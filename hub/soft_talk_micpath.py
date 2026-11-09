#!/usr/bin/env python3
import asyncio, json, subprocess, os, tempfile
from talk_chromium import get_talk_ui, PAGE_STATE_JS
from pipewire_target import resolve_pipewire_record_target

HOOK = r"""
(() => {
  if (window.__gsm2MicHook) return "already";
  window.__gsm2AllMsgs=[];
  const Orig=RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel=function(...a){
    const dc=Orig.apply(this,a); window.__gsm2ActiveDc=dc;
    dc.addEventListener("message",ev=>{ if(typeof ev.data==="string") window.__gsm2AllMsgs.push(ev.data); });
    return dc;
  };
  window.__gsm2MicHook=true; return "hooked";
})()
"""

MIC_DIAG = r"""
(async () => {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const audioInputs = devices.filter(d=>d.kind==="audioinput").map(d=>({label:d.label,id:d.deviceId.slice(0,12),group:d.groupId.slice(0,8)}));
  // probe live tracks from senders
  const pcs=window.__gsm2TalkPcs||[];
  const pc=[...pcs].reverse().find(p=>p.connectionState==="connected");
  let sender=null;
  if (pc) {
    for (const s of pc.getSenders()) {
      if (s.track && s.track.kind==="audio") { sender=s.track; break; }
    }
  }
  let peak=0;
  if (sender) {
    const ctx=new AudioContext();
    const src=ctx.createMediaStreamSource(new MediaStream([sender]));
    const an=ctx.createAnalyser(); an.fftSize=2048; src.connect(an);
    await new Promise(r=>setTimeout(r,200));
    const buf=new Uint8Array(an.fftSize); an.getByteTimeDomainData(buf);
    for (const v of buf) peak=Math.max(peak, Math.abs(v-128));
    ctx.close();
  }
  const types=(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}});
  return {audioInputs, sender: sender&&{label:sender.label,muted:sender.muted,enabled:sender.enabled,readyState:sender.readyState}, peak, types};
})()
"""


async def peak_source(name, seconds=1.5):
    serial = await resolve_pipewire_record_target(name)
    path = f"/tmp/cap_{name.replace('.','_')}.wav"
    p = subprocess.Popen(["pw-record","--target",serial,"--rate","48000","--channels","2",path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(seconds)
    p.terminate(); p.wait(timeout=3)
    vd = subprocess.run(["ffmpeg","-i",path,"-af","volumedetect","-f","null","-"], capture_output=True, text=True)
    vals={}
    for ln in vd.stderr.splitlines():
        if "mean_volume" in ln or "max_volume" in ln:
            print(name, ln.strip())
            vals[ln.strip()]=True
    return vals


async def main():
    ui=get_talk_ui()
    s=await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session=await ui._connect_page()
    for _ in range(20):
        st=await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...": break
        await asyncio.sleep(0.25)

    # list chromium source outputs
    so=subprocess.check_output(["pactl","list","source-outputs"], text=True, errors="replace")
    for b in so.split("Source Output #")[1:]:
        if "Chromium" in b or "chromium" in b:
            src=next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Source:")), "?")
            mute=next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Mute:")), "?")
            cork=next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Corked:")), "?")
            app=next((ln.split("=",1)[1].strip().strip('"') for ln in b.splitlines() if "application.name" in ln), "?")
            print(f"CAPTURE app={app} Source={src} Mute={mute} Corked={cork}")

    # play tone into phone_uplink while measuring
    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i","sine=f=700:d=5","-ar","48000","-ac","2","/tmp/longtone.wav"], capture_output=True)
    play=subprocess.Popen(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/longtone.wav"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.5)
    print("=== during tone into phone_uplink ===")
    await peak_source("phone_uplink.monitor", 1.0)
    await peak_source("openclaw_mic", 1.0)

    diag=await session.evaluate(MIC_DIAG, await_promise=True, timeout_s=15)
    print("MIC_DIAG", json.dumps(diag, indent=2)[:2500])

    await asyncio.sleep(2)
    diag2=await session.evaluate(MIC_DIAG, await_promise=True, timeout_s=15)
    print("TYPES_AFTER", diag2.get("types"), "peak", diag2.get("peak"))

    play.terminate()
    await session.close(); await ui.stop_talk()

asyncio.run(main())
