#!/usr/bin/env python3
import asyncio, json, subprocess
from talk_chromium import get_talk_ui, PAGE_STATE_JS
from pipewire_target import resolve_pipewire_record_target

HOOK = r"""
(() => {
  if (window.__gsm2Fin) return "already";
  window.__gsm2AllMsgs=[];
  const Orig=RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel=function(...a){
    const dc=Orig.apply(this,a); window.__gsm2ActiveDc=dc;
    dc.addEventListener("message",ev=>{ if(typeof ev.data==="string") window.__gsm2AllMsgs.push(ev.data); });
    return dc;
  };
  window.__gsm2Fin=true; return "hooked";
})()
"""


async def main():
    ui=get_talk_ui()
    s=await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session=await ui._connect_page()
    for _ in range(30):
        st=await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...": break
        await asyncio.sleep(0.25)

    # show links involving chromium/phone
    links=subprocess.check_output(["pw-link","-l"], text=True)
    print("LINKS")
    for ln in links.splitlines():
        if any(k in ln for k in ("Chromium","phone_uplink","openclaw_mic","openclaw_bus")):
            print(ln)

    bus_ser=await resolve_pipewire_record_target("openclaw_bus.monitor")
    rec=subprocess.Popen(["pw-record","--target",bus_ser,"--rate","48000","--channels","2","/tmp/fin_bus.wav"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # loud speech-like into phone_uplink
    subprocess.run(["ffmpeg","-nostdin","-y","-f","lavfi","-i","sine=f=440:d=0.25","-f","lavfi","-i","sine=f=660:d=0.25",
                    "-f","lavfi","-i","sine=f=550:d=0.3","-filter_complex",
                    "[0][1][2]concat=n=3:v=0:a=1,volume=3.0,apad=whole_dur=2.0","-ar","48000","-ac","2","/tmp/loud.wav"],
                   capture_output=True)
    for n in range(3):
        subprocess.run(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/loud.wav"], capture_output=True)
        await asyncio.sleep(0.8)
        types=await session.evaluate("(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return '?'}})")
        print(f"n={n} ntypes={len(types)} interesting={[t for t in types if t not in ('session.created','session.updated')]}")
        if any(t.startswith("input_audio") or t.startswith("response.") for t in types):
            await asyncio.sleep(6)
            break
        await asyncio.sleep(1.2)

    types=await session.evaluate("(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return '?'}})")
    print("ALL_NONEPH", [t for t in types if t not in ("session.created","session.updated")])
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i","/tmp/fin_bus.wav","-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    bus_db=-91
    for ln in vd.stderr.splitlines():
        if "max_volume" in ln or "mean_volume" in ln:
            print("BUS", ln.strip())
        if "max_volume" in ln:
            try: bus_db=float(ln.split(":")[-1].strip().split()[0])
            except Exception: pass

    st=await session.evaluate(PAGE_STATE_JS)
    webrtc=any((p or {}).get("connection")=="connected" for p in (st.get("pcs") or []))
    go = webrtc and bus_db > -35 and any(t.startswith("response.") or t.startswith("input_audio") for t in types)
    print("GO" if go else "NO-GO", "webrtc", webrtc, "bus_db", bus_db, "hasTalk", st.get("hasTalkButton"), "live", st.get("live"))
    await session.close(); await ui.stop_talk()

asyncio.run(main())
