#!/usr/bin/env python3
import asyncio, json, subprocess, time
from talk_chromium import get_talk_ui, PAGE_STATE_JS

INSTALL_ANALYSER = r"""
(() => {
  if (window.__gsm2Analyser) return "already";
  const pcs = window.__gsm2TalkPcs || [];
  const pc = [...pcs].reverse().find(p => p.connectionState === "connected") || pcs[pcs.length-1];
  if (!pc) return "no-pc";
  const recv = pc.getReceivers().find(r => r.track && r.track.kind === "audio");
  if (!recv || !recv.track) return "no-track";
  const ctx = new AudioContext();
  const stream = new MediaStream([recv.track]);
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 2048;
  src.connect(analyser);
  // also connect to destination so we force a graph (may duplicate to speakers)
  try { src.connect(ctx.destination); } catch (e) {}
  window.__gsm2Analyser = {ctx, analyser, track: recv.track};
  return {ok: true, ctxState: ctx.state, sampleRate: ctx.sampleRate, trackMuted: recv.track.muted};
})()
"""

READ_ANALYSER = r"""
(() => {
  const a = window.__gsm2Analyser;
  if (!a) return {err: "none"};
  if (a.ctx.state === "suspended") a.ctx.resume();
  const buf = new Uint8Array(a.analyser.fftSize);
  a.analyser.getByteTimeDomainData(buf);
  let peak = 0, sum = 0;
  for (let i = 0; i < buf.length; i++) {
    const v = Math.abs(buf[i] - 128);
    if (v > peak) peak = v;
    sum += v;
  }
  const freq = new Uint8Array(a.analyser.frequencyBinCount);
  a.analyser.getByteFrequencyData(freq);
  let fpeak = 0;
  for (let i = 0; i < freq.length; i++) if (freq[i] > fpeak) fpeak = freq[i];
  return {
    ctxState: a.ctx.state,
    timePeak: peak,
    timeAvg: sum / buf.length,
    freqPeak: fpeak,
    trackMuted: a.track.muted,
    trackState: a.track.readyState,
  };
})()
"""

SINK_DETAIL = r"""
(() => {
  const audios = [...document.querySelectorAll("audio")].map(el => {
    let tracks = [];
    try {
      if (el.srcObject) tracks = el.srcObject.getAudioTracks().map(t => ({
        muted: t.muted, enabled: t.enabled, readyState: t.readyState, id: t.id
      }));
    } catch (e) {}
    return {muted: el.muted, volume: el.volume, paused: el.paused, readyState: el.readyState, tracks};
  });
  return audios;
})()
"""


async def main():
    ui = get_talk_ui()
    await ui.start_talk()
    session = await ui._connect_page()
    for _ in range(15):
        st = await session.evaluate(PAGE_STATE_JS)
        if st.get("statusText") == "Listening..." or any(
            (p or {}).get("connection") == "connected" for p in (st.get("pcs") or [])
        ):
            break
        await asyncio.sleep(0.3)
    inst = await session.evaluate(INSTALL_ANALYSER)
    print("INSTALL", inst)
    # resume audio context via user gesture simulation already done by talk click
    for i in range(30):
        reading = await session.evaluate(READ_ANALYSER)
        aud = await session.evaluate(SINK_DETAIL)
        st = await session.evaluate(PAGE_STATE_JS)
        print(f"i={i} status={st.get('statusText')!r} analyser={reading} audioEls={aud}")
        # inject a bit of mic energy mid-way
        if i == 5:
            subprocess.Popen(
                ["bash", "-lc",
                 "ffmpeg -nostdin -f lavfi -i 'sine=f=1000:d=2' -ar 48000 -ac 2 -f wav - "
                 "| pw-cat -p --target phone_uplink --rate 48000 --channels 2 -"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("INJECTED_SINE")
        if i == 8 and subprocess.run(["which", "espeak-ng"], capture_output=True).returncode == 0:
            subprocess.Popen(
                ["bash", "-lc",
                 "espeak-ng -w /tmp/ochello.wav 'Hello OpenClaw please reply with your voice' && "
                 "ffmpeg -y -i /tmp/ochello.wav -ar 48000 -ac 2 /tmp/ochello48.wav && "
                 "pw-cat -p --target phone_uplink --rate 48000 --channels 2 /tmp/ochello48.wav"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            print("INJECTED_ESPEAK")
        await asyncio.sleep(0.7)

    # pactl sink-input volume during session
    out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, errors="replace")
    print("===SINK_INPUTS===")
    print(out[:4000])
    # pw-dump chromium nodes volume
    dump = subprocess.run(["pw-cli", "ls", "Node"], capture_output=True, text=True)
    print("PW_NODES", "\n".join([ln for ln in dump.stdout.splitlines() if "hromium" in ln.lower() or "playback" in ln.lower()][:40]))

    await session.close()
    await ui.stop_talk()


asyncio.run(main())
