#!/usr/bin/env python3
import asyncio, json
from talk_chromium import get_talk_ui, PAGE_STATE_JS

GET_STATS = r"""
(async () => {
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected") || pcs[pcs.length-1];
  if (!pc) return {err: "no-pc", n: pcs.length};
  const stats = await pc.getStats();
  const rows = [];
  stats.forEach(r => {
    if (["inbound-rtp","outbound-rtp","remote-inbound-rtp","candidate-pair","codec","transport"].includes(r.type)) {
      const o = {type: r.type, id: r.id};
      for (const k of Object.keys(r)) {
        if (k === "type" || k === "id" || k === "timestamp") continue;
        const v = r[k];
        if (typeof v === "number" || typeof v === "string" || typeof v === "boolean") o[k] = v;
      }
      rows.push(o);
    }
  });
  return {connection: pc.connectionState, ice: pc.iceConnectionState, rows};
})()
"""


async def main():
    ui = get_talk_ui()
    await ui.start_talk()
    session = await ui._connect_page()
    for _ in range(25):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText") == "Listening...":
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(4)
    stats = await session.evaluate(GET_STATS, await_promise=True, timeout_s=15)
    print(json.dumps(stats, indent=2)[:12000])
    await session.close()
    await ui.stop_talk()

asyncio.run(main())
