#!/usr/bin/env python3
"""Soft e2e GO check: Talk connect + mic energy triggers OpenAI reply onto openclaw_bus."""
import asyncio, json, subprocess, os, tempfile
from talk_chromium import get_talk_ui, PAGE_STATE_JS
from pipewire_target import resolve_pipewire_record_target

HOOK = r"""
(() => {
  if (window.__gsm2GoHook) return "already";
  window.__gsm2AllMsgs=[];
  const Orig=RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel=function(...a){
    const dc=Orig.apply(this,a); window.__gsm2ActiveDc=dc;
    dc.addEventListener("message",ev=>{ if(typeof ev.data==="string") window.__gsm2AllMsgs.push(ev.data); });
    return dc;
  };
  window.__gsm2GoHook=true; return "hooked";
})()
"""

TYPES = r"""
(() => (window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}}))()
"""


async def main():
    # confirm mic path before talk
    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i","sine=f=600:d=1.5","-ar","48000","-ac","2","/tmp/t.wav"], capture_output=True)
    ser = await resolve_pipewire_record_target("openclaw_mic")
    rec = subprocess.Popen(["pw-record","--target",ser,"--rate","48000","--channels","2","/tmp/pre_mic.wav"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(0.2)
    subprocess.run(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/t.wav"], capture_output=True)
    await asyncio.sleep(0.2)
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i","/tmp/pre_mic.wav","-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    mic_ok=False
    for ln in vd.stderr.splitlines():
        if "max_volume" in ln:
            print("PRE_MIC", ln.strip())
            # e.g. max_volume: -21.1 dB
            try:
                db=float(ln.split(":")[-1].strip().split()[0])
                mic_ok = db > -60
            except Exception:
                pass
    if not mic_ok:
        print("NOGO pre-mic silent")
        return

    ui=get_talk_ui()
    s=await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session=await ui._connect_page()
    for _ in range(25):
        st=await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...":
            print("LISTENING webrtc_connected")
            break
        await asyncio.sleep(0.25)

    bus_ser = await resolve_pipewire_record_target("openclaw_bus.monitor")
    bus="/tmp/go_bus.wav"
    rec=subprocess.Popen(["pw-record","--target",bus_ser,"--rate","48000","--channels","2",bus],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Inject speech-like bursts into phone_uplink (feeds openclaw_mic)
    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i",
                    "sine=f=440:d=0.2,afade=t=in:st=0:d=0.02,afade=t=out:st=0.18:d=0.02",
                    "-f","lavfi","-i","sine=f=660:d=0.2",
                    "-f","lavfi","-i","sine=f=550:d=0.25",
                    "-filter_complex","[0][1][2]concat=n=3:v=0:a=1,volume=1.2,apad=whole_dur=2.5",
                    "-ar","48000","-ac","2","/tmp/speechish2.wav"], capture_output=True)

    got=False
    for n in range(4):
        subprocess.run(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/speechish2.wav"], capture_output=True)
        await asyncio.sleep(1.0)
        types=await session.evaluate(TYPES)
        print(f"inject_{n} types_tail={types[-12:]}")
        interesting=[t for t in types if t.startswith("input_audio") or t.startswith("response.") or "speech" in t]
        if interesting:
            print("SERVER_EVENTS", interesting[:20])
            got=True
            await asyncio.sleep(6)  # let reply play
            break
        await asyncio.sleep(1.5)

    # health-ish page state
    st=await session.evaluate(PAGE_STATE_JS)
    print("PAGE", {k:st.get(k) for k in ("live","talkLabel","statusText","pcs")})

    # chromium links
    links=subprocess.check_output(["pw-link","-l"], text=True, errors="replace")
    chromium_ok = ("Chromium" in links or "chromium" in links) and "openclaw_bus" in links
    mic_link = "openclaw_mic" in links and "phone_uplink" in links
    print("PW_CHROMIUM_TO_BUS", chromium_ok, "MIC_REMAP_LINKED", mic_link)

    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i",bus,"-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    bus_peak_db=-91
    for ln in vd.stderr.splitlines():
        if "max_volume" in ln or "mean_volume" in ln:
            print("BUS", ln.strip())
        if "max_volume" in ln:
            try: bus_peak_db=float(ln.split(":")[-1].strip().split()[0])
            except Exception: pass

    await session.close(); await ui.stop_talk()

    # sink check during was already done; report GO
    go = got and bus_peak_db > -40
    print("RESULT", "GO" if go else "NO-GO", "vad_or_response", got, "bus_max_db", bus_peak_db)

asyncio.run(main())
