#!/usr/bin/env python3
import asyncio, json
from talk_chromium import get_talk_ui, PAGE_STATE_JS, CdpSession

async def main():
    ui = get_talk_ui()
    # connect and enable console before talk
    session = await ui._connect_page()
    await session.call("Console.enable")
    await session.call("Runtime.enable")
    logs = []

    # monkeypatch recv to also collect console - simpler: poll after
    await session.close()

    await ui.start_talk()
    session = await ui._connect_page()
    await session.call("Console.enable")
    # Read recent exceptions via Runtime
    # Collect performance logs from page
    page_logs = await session.evaluate(r"""
(() => {
  const out = [];
  // hook if not already
  if (!window.__gsm2Logs) {
    window.__gsm2Logs = [];
    const origErr = console.error.bind(console);
    const origWarn = console.warn.bind(console);
    const origLog = console.log.bind(console);
    console.error = (...a) => { window.__gsm2Logs.push({l:'error', m:a.map(String).join(' ')}); origErr(...a); };
    console.warn = (...a) => { window.__gsm2Logs.push({l:'warn', m:a.map(String).join(' ')}); origWarn(...a); };
    console.log = (...a) => {
      const s = a.map(String).join(' ');
      if (/talk|voice|realtime|webrtc|openai|error|fail|sdp|ice/i.test(s))
        window.__gsm2Logs.push({l:'log', m:s.slice(0,500)});
      origLog(...a);
    };
  }
  return window.__gsm2Logs.slice(-50);
})()
""")
    print("PRE_HOOK_LOGS", page_logs)
    # wait and also inspect status / datachannel messages if exposed
    for i in range(15):
        st = await session.evaluate(PAGE_STATE_JS)
        logs = await session.evaluate("window.__gsm2Logs ? window.__gsm2Logs.slice(-80) : []")
        print(f"i={i} status={st.get('statusText')!r} nlogs={len(logs) if isinstance(logs,list) else logs}")
        await asyncio.sleep(0.5)
    print("LOGS", json.dumps(logs, indent=2)[:6000])
    # Try to find talk module state on window
    exposed = await session.evaluate(r"""
(() => {
  const keys = Object.keys(window).filter(k => /talk|voice|realtime|openclaw|gsm2/i.test(k));
  return {keys, hasPcs: !!(window.__gsm2TalkPcs&&window.__gsm2TalkPcs.length)};
})()
""")
    print("EXPOSED", exposed)
    await session.close()
    await ui.stop_talk()

asyncio.run(main())
