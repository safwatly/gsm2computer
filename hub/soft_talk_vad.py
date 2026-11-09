#!/usr/bin/env python3
import asyncio, json, subprocess, os, tempfile
from talk_chromium import get_talk_ui, PAGE_STATE_JS

HOOK = r"""
(() => {
  if (window.__gsm2VadHook) return "already";
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
  window.__gsm2VadHook = true;
  return "hooked";
})()
"""

SNAP = r"""
(() => {
  let session=null;
  for (const m of window.__gsm2AllMsgs||[]) {
    try { const j=JSON.parse(m); if (j.type==="session.created"){session=j.session; break;} } catch(e){}
  }
  const types=(window.__gsm2AllMsgs||[]).map(m=>{try{return JSON.parse(m).type}catch(e){return "?"}});
  return {
    model: session&&session.model,
    voice: session&&session.voice,
    top_turn: session&&session.turn_detection,
    audio: session&&session.audio,
    output_modalities: session&&session.output_modalities,
    instructions_head: session&&String(session.instructions||"").slice(0,200),
    types,
  };
})()
"""

STATS = r"""
(async () => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected");
  if (!pc) return {err:"no-pc"};
  const stats = await pc.getStats();
  let inbound=null, outbound=null;
  stats.forEach(r => {
    if (r.type==="inbound-rtp" && r.kind==="audio") inbound={bytesReceived:r.bytesReceived,packetsReceived:r.packetsReceived,audioLevel:r.audioLevel,totalAudioEnergy:r.totalAudioEnergy};
    if (r.type==="outbound-rtp" && r.kind==="audio") outbound={bytesSent:r.bytesSent,packetsSent:r.packetsSent};
  });
  return {inbound, outbound};
})()
"""


async def main():
    ui = get_talk_ui()
    s = await ui._connect_page(); await s.evaluate(HOOK); await s.close()
    await ui.start_talk()
    session = await ui._connect_page()
    for _ in range(25):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText")=="Listening...": break
        await asyncio.sleep(0.2)
    await asyncio.sleep(1)
    snap = await session.evaluate(SNAP)
    print("SESSION_AUDIO", json.dumps(snap.get("audio"), indent=2)[:3000])
    print("TOP_TURN", snap.get("top_turn"))
    print("META", {k:snap.get(k) for k in ("model","voice","output_modalities","instructions_head")})
    print("TYPES0", snap.get("types"))

    # Inject clear speech via espeak or ffmpeg sine AM
    subprocess.run(["bash","-lc",
      "ffmpeg -nostdin -y -f lavfi -i 'sine=f=500:d=0.15' -f lavfi -i 'sine=f=800:d=0.15' "
      "-filter_complex '[0][1]concat=n=2:v=0:a=1,volume=0.8,apad=whole_dur=2' "
      "-ar 48000 -ac 2 /tmp/speechish.wav"], capture_output=True)
    # play into phone_uplink repeatedly
    for n in range(3):
        subprocess.Popen(["pw-cat","-p","--target","phone_uplink","--rate","48000","--channels","2","/tmp/speechish.wav"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(2.2)
        snap = await session.evaluate(SNAP)
        stats = await session.evaluate(STATS, await_promise=True)
        print(f"after_inject_{n} types={snap.get('types')} stats={stats}")
        if any(t.startswith("response.") or t.startswith("input_audio") or "speech" in t for t in (snap.get("types") or [])):
            print("GOT_SERVER_EVENTS")
            break

    # wait a bit more
    await asyncio.sleep(3)
    snap = await session.evaluate(SNAP)
    stats = await session.evaluate(STATS, await_promise=True)
    print("FINAL_TYPES", snap.get("types"))
    print("FINAL_STATS", stats)

    # If still silent, try session.update to force VAD + greeting
    fix = await session.evaluate(r"""
(() => {
  const dc = window.__gsm2ActiveDc;
  if (!dc || dc.readyState!=="open") return {ok:false};
  dc.send(JSON.stringify({
    type: "session.update",
    session: {
      type: "realtime",
      audio: {
        input: {
          turn_detection: {
            type: "server_vad",
            threshold: 0.3,
            prefix_padding_ms: 300,
            silence_duration_ms: 400,
            create_response: true,
            interrupt_response: true
          }
        }
      },
      instructions: "Immediately greet the user out loud in one short sentence, then listen."
    }
  }));
  dc.send(JSON.stringify({
    type: "response.create",
    response: { output_modalities: ["audio"], instructions: "Say hello briefly now." }
  }));
  return {ok:true};
})()
""")
    print("FIX_SEND", fix)
    tmp=tempfile.mkdtemp(); bus=os.path.join(tmp,"bus.wav")
    # record with serial
    import sys
    sys.path.insert(0,".")
    from pipewire_target import resolve_pipewire_record_target
    serial = await resolve_pipewire_record_target("openclaw_bus.monitor")
    rec=subprocess.Popen(["pw-record","--target",serial,"--rate","48000","--channels","2",bus],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for i in range(20):
        snap=await session.evaluate(SNAP)
        stats=await session.evaluate(STATS, await_promise=True)
        print(f"fix_{i} types_tail={(snap.get('types') or [])[-8:]} inbound={stats.get('inbound')}")
        if stats.get("inbound") and (stats["inbound"].get("totalAudioEnergy") or 0) > 0.0001:
            print("ENERGY_OK")
            await asyncio.sleep(3)
            break
        await asyncio.sleep(0.4)
    rec.terminate(); rec.wait(timeout=3)
    vd=subprocess.run(["ffmpeg","-i",bus,"-af","volumedetect","-f","null","-"],capture_output=True,text=True)
    for ln in vd.stderr.splitlines():
        if "volume" in ln: print(ln.strip())
    await session.close(); await ui.stop_talk()

asyncio.run(main())
