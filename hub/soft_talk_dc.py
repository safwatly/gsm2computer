#!/usr/bin/env python3
import asyncio, json
from talk_chromium import get_talk_ui, PAGE_STATE_JS

DIAG = r"""
(() => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected") || pcs[pcs.length-1];
  if (!pc) return {err:"no-pc"};
  const dcs = [];
  try {
    // Chrome: pc.sctp might exist; also scan known channels via getTransceivers won't show DC
  } catch(e) {}
  // Hook DataChannel constructor if not hooked
  return {
    connection: pc.connectionState,
    ice: pc.iceConnectionState,
    signaling: pc.signalingState,
    localDesc: pc.localDescription && {type: pc.localDescription.type, sdpLen: (pc.localDescription.sdp||"").length},
    remoteDesc: pc.remoteDescription && {type: pc.remoteDescription.type, sdpLen: (pc.remoteDescription.sdp||"").length},
    // sdp m=application lines
    localHasApp: !!(pc.localDescription && /m=application/i.test(pc.localDescription.sdp||"")),
    remoteHasApp: !!(pc.remoteDescription && /m=application/i.test(pc.remoteDescription.sdp||"")),
  };
})()
"""

HOOK_DC = r"""
(() => {
  if (window.__gsm2DcHook) return "already";
  window.__gsm2Dcs = [];
  const Orig = RTCPeerConnection.prototype.createDataChannel;
  RTCPeerConnection.prototype.createDataChannel = function(...args) {
    const dc = Orig.apply(this, args);
    const rec = {label: dc.label, id: dc.id, readyState: dc.readyState, msgs: [], events: []};
    window.__gsm2Dcs.push(rec);
    dc.addEventListener("open", () => { rec.readyState = dc.readyState; rec.events.push("open"); });
    dc.addEventListener("close", () => { rec.readyState = dc.readyState; rec.events.push("close"); });
    dc.addEventListener("error", (e) => { rec.events.push("error:"+String(e)); });
    dc.addEventListener("message", (ev) => {
      const data = typeof ev.data === "string" ? ev.data : ("[bin "+(ev.data && ev.data.byteLength)+"]");
      rec.msgs.push(data.slice(0, 400));
      if (rec.msgs.length > 40) rec.msgs.shift();
    });
    const origSend = dc.send.bind(dc);
    dc.send = (data) => {
      const s = typeof data === "string" ? data : ("[bin]");
      rec.events.push("send:"+s.slice(0,200));
      return origSend(data);
    };
    return dc;
  };
  // also capture remotely created channels
  const OrigPC = window.RTCPeerConnection;
  // already wrapped by gsm2 hook - patch on existing pcs
  for (const pc of (window.__gsm2TalkPcs||[])) {
    pc.addEventListener("datachannel", (ev) => {
      const dc = ev.channel;
      const rec = {label: dc.label, id: dc.id, readyState: dc.readyState, msgs: [], events: ["remote"]};
      window.__gsm2Dcs.push(rec);
      dc.addEventListener("open", () => { rec.readyState = dc.readyState; rec.events.push("open"); });
      dc.addEventListener("message", (ev) => {
        const data = typeof ev.data === "string" ? ev.data : ("[bin]");
        rec.msgs.push(data.slice(0,400));
      });
    });
  }
  window.__gsm2DcHook = true;
  return "hooked";
})()
"""

READ_DC = r"""
(() => {
  const dcs = (window.__gsm2Dcs || []).map(d => ({
    label: d.label, id: d.id, readyState: d.readyState,
    events: d.events.slice(-20),
    msgs: d.msgs.slice(-15),
  }));
  return dcs;
})()
"""


async def main():
    ui = get_talk_ui()
    # Install DC hook BEFORE starting talk - need page session first
    session = await ui._connect_page()
    await session.evaluate(HOOK_DC)
    # Also need to re-hook on the Wrapped PC - start_talk will create new PCs
    # Patch via evaluating again inside start by pre-installing on prototype which we did
    await session.close()

    await ui.start_talk()
    session = await ui._connect_page()
    # ensure hook still there (prototype patch persists)
    await session.evaluate(HOOK_DC)
    for i in range(20):
        st = await session.evaluate(PAGE_STATE_JS)
        diag = await session.evaluate(DIAG)
        dcs = await session.evaluate(READ_DC)
        print(f"i={i} status={st.get('statusText')!r} diag={json.dumps(diag)} dcs={json.dumps(dcs)[:1500]}")
        if st.get("statusText") == "Listening..." and i >= 6:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(3)
    dcs = await session.evaluate(READ_DC)
    print("FINAL_DCS", json.dumps(dcs, indent=2)[:8000])
    diag = await session.evaluate(DIAG)
    print("FINAL_DIAG", json.dumps(diag, indent=2))
    await session.close()
    await ui.stop_talk()

asyncio.run(main())
