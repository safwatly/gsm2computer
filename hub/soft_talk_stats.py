#!/usr/bin/env python3
import asyncio, json
from talk_chromium import get_talk_ui, PAGE_STATE_JS

GET_STATS = r"""
async () => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected") || pcs[pcs.length-1];
  if (!pc) return {err: "no-pc"};
  const stats = await pc.getStats();
  const rows = [];
  stats.forEach(r => {
    if (["inbound-rtp","outbound-rtp","remote-inbound-rtp","candidate-pair","track","media-source","codec"].includes(r.type)) {
      const o = {type: r.type};
      for (const k of Object.keys(r)) {
        if (k === "type" || k === "id" || k === "timestamp") continue;
        const v = r[k];
        if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") o[k] = v;
      }
      rows.push(o);
    }
  });
  return {
    connection: pc.connectionState,
    ice: pc.iceConnectionState,
    rows,
  };
}
"""


async def main():
    ui = get_talk_ui()
    await ui.start_talk()
    session = await ui._connect_page()
    for _ in range(20):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText") == "Listening...":
            break
        await asyncio.sleep(0.3)
    # wait for greeting window
    await asyncio.sleep(5)
    stats = await session.evaluate(GET_STATS)
    print("STATS", json.dumps(stats, indent=2)[:8000])
    # second sample for delta
    await asyncio.sleep(3)
    stats2 = await session.evaluate(GET_STATS)
    print("STATS2_inbound")
    for r in (stats2.get("rows") or []):
        if r.get("type") == "inbound-rtp" and r.get("kind") == "audio":
            print(json.dumps(r))
        if r.get("type") == "outbound-rtp" and r.get("kind") == "audio":
            print("OUT", json.dumps(r))
        if r.get("type") == "candidate-pair" and r.get("state") == "succeeded":
            print("PAIR", {k: r.get(k) for k in ("state","nominated","bytesReceived","bytesSent","currentRoundTripTime","localCandidateId","remoteCandidateId")})
    await session.close()
    await ui.stop_talk()

asyncio.run(main())
