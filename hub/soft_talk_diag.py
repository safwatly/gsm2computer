#!/usr/bin/env python3
import asyncio, json, subprocess
from talk_chromium import get_talk_ui, PAGE_STATE_JS

AUDIO_DIAG = r"""
(() => {
  const pcs = window.__gsm2TalkPcs || [];
  const receivers = [];
  for (const pc of pcs) {
    try {
      for (const r of pc.getReceivers()) {
        const t = r.track;
        receivers.push({
          kind: t && t.kind,
          id: t && t.id,
          enabled: t && t.enabled,
          muted: t && t.muted,
          readyState: t && t.readyState,
          label: t && t.label,
        });
      }
    } catch (e) { receivers.push({err: String(e)}); }
  }
  const senders = [];
  for (const pc of pcs) {
    try {
      for (const s of pc.getSenders()) {
        const t = s.track;
        senders.push({
          kind: t && t.kind,
          enabled: t && t.enabled,
          muted: t && t.muted,
          readyState: t && t.readyState,
        });
      }
    } catch (e) { senders.push({err: String(e)}); }
  }
  const audios = [...document.querySelectorAll("audio, video")].map(el => ({
    tag: el.tagName,
    muted: el.muted,
    volume: el.volume,
    paused: el.paused,
    srcObject: !!(el.srcObject),
    currentSrc: (el.currentSrc || "").slice(0, 80),
    readyState: el.readyState,
  }));
  return {receivers, senders, audios, pcs: pcs.length};
})()
"""


def summarize_sink_inputs():
    out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, errors="replace")
    blocks = out.split("Sink Input #")
    print("SINK_INPUT_COUNT", max(0, len(blocks) - 1))
    for b in blocks[1:]:
        num = b.split("\n", 1)[0].strip()
        sink = "?"
        mute = "?"
        cork = "?"
        app = "?"
        media = "?"
        target = "?"
        for ln in b.splitlines():
            s = ln.strip()
            if s.startswith("Sink:"):
                sink = s.split(":", 1)[1].strip()
            elif s.startswith("Mute:"):
                mute = s.split(":", 1)[1].strip()
            elif s.startswith("Corked:"):
                cork = s.split(":", 1)[1].strip()
            elif "application.name" in s and "=" in s:
                app = s.split("=", 1)[1].strip().strip('"')
            elif "media.name" in s and "=" in s:
                media = s.split("=", 1)[1].strip().strip('"')
            elif "target.object" in s and "=" in s:
                target = s.split("=", 1)[1].strip().strip('"')
        print(f"SI#{num} Sink={sink} Mute={mute} Corked={cork} app={app} media={media} target={target}")


def summarize_source_outputs():
    out = subprocess.check_output(["pactl", "list", "source-outputs"], text=True, errors="replace")
    for b in out.split("Source Output #")[1:]:
        num = b.split("\n", 1)[0].strip()
        src = "?"
        app = "?"
        media = "?"
        for ln in b.splitlines():
            s = ln.strip()
            if s.startswith("Source:"):
                src = s.split(":", 1)[1].strip()
            elif "application.name" in s and "=" in s:
                app = s.split("=", 1)[1].strip().strip('"')
            elif "media.name" in s and "=" in s:
                media = s.split("=", 1)[1].strip().strip('"')
        blob = (app + " " + media).lower()
        if "chromium" in blob or "webrtc" in blob or "pulseaudio" in blob or "chrome" in blob:
            print(f"SO#{num} Source={src} app={app} media={media}")


async def main():
    ui = get_talk_ui()
    await ui.start_talk()
    print("TALK_STARTED webrtc=", ui.webrtc_connected)
    session = await ui._connect_page()
    try:
        diag = await session.evaluate(AUDIO_DIAG)
        state = await session.evaluate(PAGE_STATE_JS)
        keys = ("live", "talkLabel", "statusText", "pcs", "composerLen", "hasTalkButton")
        print("PAGE_STATE", json.dumps({k: state.get(k) for k in keys}, default=str))
        print("AUDIO_DIAG", json.dumps(diag, default=str)[:3000])
    finally:
        await session.close()
    summarize_sink_inputs()
    summarize_source_outputs()
    # Map sink index -> name
    sinks = subprocess.check_output(["pactl", "list", "short", "sinks"], text=True)
    print("SINKS\n" + sinks)
    await asyncio.sleep(3)
    await ui.stop_talk()
    print("TALK_STOPPED")


if __name__ == "__main__":
    asyncio.run(main())
