#!/usr/bin/env python3
"""Soft e2e: start Talk, inject tone+speech into phone_uplink, measure openclaw_bus energy."""
import asyncio, json, subprocess, tempfile, os, time, wave, struct, math
from pathlib import Path
from talk_chromium import get_talk_ui, PAGE_STATE_JS

AUDIO_DIAG = r"""
(() => {
  const pcs = window.__gsm2TalkPcs || [];
  const livePcs = pcs.filter(pc => pc.connectionState === "connected" || pc.iceConnectionState === "connected");
  const list = livePcs.length ? livePcs : pcs.slice(-1);
  const receivers = [];
  for (const pc of list) {
    for (const r of pc.getReceivers()) {
      const t = r.track;
      if (!t) continue;
      receivers.push({kind:t.kind, muted:t.muted, enabled:t.enabled, readyState:t.readyState});
    }
  }
  const audios = [...document.querySelectorAll("audio")].map(el => ({
    muted: el.muted, volume: el.volume, paused: el.paused,
    srcObject: !!(el.srcObject), readyState: el.readyState,
  }));
  return {
    receivers,
    audios,
    pcs: pcs.map(pc => ({c: pc.connectionState, i: pc.iceConnectionState})),
  };
})()
"""


def make_speech_wav(path: str, seconds: float = 3.0, rate: int = 48000):
    """Generate a loud 440Hz + 880Hz tone burst (simulates speech energy)."""
    n = int(rate * seconds)
    with wave.open(path, "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            # amplitude-modulated dual tone so STT might pick something; energy is the point
            env = 0.7 if (int(t * 4) % 2 == 0) else 0.15
            sample = env * 0.45 * (math.sin(2 * math.pi * 440 * t) + 0.5 * math.sin(2 * math.pi * 880 * t))
            v = max(-32767, min(32767, int(sample * 32767)))
            frames += struct.pack("<hh", v, v)
        w.writeframes(frames)


def peak_of_wav(path: str) -> float:
    try:
        with wave.open(path, "r") as w:
            nch = w.getnchannels()
            sw = w.getsampwidth()
            n = w.getnframes()
            raw = w.readframes(n)
        if sw != 2 or not raw:
            return 0.0
        fmt = "<" + "h" * (len(raw) // 2)
        samples = struct.unpack(fmt, raw)
        peak = max(abs(s) for s in samples) / 32768.0
        return peak
    except Exception as e:
        print("peak err", e)
        return -1.0


async def main():
    ui = get_talk_ui()
    await ui.start_talk()
    print("TALK_STARTED")
    session = await ui._connect_page()

    # Wait until connected + status settles
    for i in range(20):
        state = await session.evaluate(PAGE_STATE_JS)
        diag = await session.evaluate(AUDIO_DIAG)
        print(f"t={i} status={state.get('statusText')!r} live={state.get('live')} diag={json.dumps(diag)}")
        pcs = state.get("pcs") or []
        if any(isinstance(p, dict) and p.get("connection") == "connected" for p in pcs):
            if i >= 3:
                break
        await asyncio.sleep(0.5)

    # Summarize chromium sink
    out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, errors="replace")
    for b in out.split("Sink Input #")[1:]:
        if "Chromium" in b or "chromium" in b:
            sink = next((ln.split(":",1)[1].strip() for ln in b.splitlines() if ln.strip().startswith("Sink:")), "?")
            print("CHROMIUM_SINK_INDEX", sink)

    tmp = tempfile.mkdtemp(prefix="gsm2soft-")
    speech = os.path.join(tmp, "speech.wav")
    bus_rec = os.path.join(tmp, "bus.wav")
    make_speech_wav(speech, seconds=4.0)

    # Record openclaw_bus.monitor while playing into phone_uplink (feeds openclaw_mic)
    rec = subprocess.Popen(
        ["pw-record", "--target", "openclaw_bus.monitor", "--rate", "48000", "--channels", "2", bus_rec],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    await asyncio.sleep(0.3)
    # Play into phone_uplink sink so openclaw_mic remap picks it up
    play = subprocess.run(
        ["pw-cat", "-p", "--target", "phone_uplink", "--rate", "48000", "--channels", "2", speech],
        capture_output=True, text=True,
    )
    print("PLAY_RC", play.returncode, (play.stderr or "")[:200])
    # Also try espeak if available for real speech
    espeak = subprocess.run(["which", "espeak-ng"], capture_output=True, text=True)
    if espeak.returncode == 0:
        spoken = os.path.join(tmp, "hello.wav")
        subprocess.run(
            ["espeak-ng", "-w", spoken, "-s", "140", "Hello OpenClaw, can you hear me? Please say hello back."],
            check=False,
        )
        # resample/convert to 48k stereo if needed via ffmpeg
        spoken48 = os.path.join(tmp, "hello48.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", spoken, "-ar", "48000", "-ac", "2", spoken48],
            capture_output=True,
        )
        print("Playing espeak hello into phone_uplink")
        subprocess.run(
            ["pw-cat", "-p", "--target", "phone_uplink", "--rate", "48000", "--channels", "2", spoken48],
            capture_output=True,
        )
    # Wait for possible assistant reply
    for i in range(24):
        diag = await session.evaluate(AUDIO_DIAG)
        state = await session.evaluate(PAGE_STATE_JS)
        muted = [r.get("muted") for r in (diag.get("receivers") or []) if r.get("kind") == "audio"]
        print(f"wait={i} status={state.get('statusText')!r} remote_muted={muted} pcs={diag.get('pcs')}")
        if muted and any(m is False for m in muted):
            print("REMOTE_UNMUTED")
            await asyncio.sleep(6)  # let speech play
            break
        await asyncio.sleep(0.5)
    else:
        await asyncio.sleep(2)

    rec.send_signal(subprocess.signal.SIGINT) if False else None
    rec.terminate()
    try:
        rec.wait(timeout=3)
    except Exception:
        rec.kill()
    await asyncio.sleep(0.5)
    peak = peak_of_wav(bus_rec) if os.path.exists(bus_rec) else -1
    print("BUS_PEAK", peak, "bytes", os.path.getsize(bus_rec) if os.path.exists(bus_rec) else 0)
    # ffmpeg volumedetect
    if os.path.exists(bus_rec) and os.path.getsize(bus_rec) > 1000:
        vd = subprocess.run(
            ["ffmpeg", "-i", bus_rec, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        for ln in (vd.stderr or "").splitlines():
            if "mean_volume" in ln or "max_volume" in ln:
                print(ln.strip())

    final = await session.evaluate(AUDIO_DIAG)
    print("FINAL_DIAG", json.dumps(final))
    await session.close()
    await ui.stop_talk()
    print("DONE")


if __name__ == "__main__":
    import signal as _  # noqa
    asyncio.run(main())
