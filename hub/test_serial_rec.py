import asyncio, subprocess, os
from pipewire_target import resolve_pipewire_record_target, resolve_pipewire_playback_target

async def main():
    rec = await resolve_pipewire_record_target("openclaw_bus.monitor")
    play = await resolve_pipewire_playback_target("openclaw_bus")
    print("record_target", rec, "play_target", play)
    wav = "/tmp/ser_rec.wav"
    tone = "/tmp/tone.wav"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", "-ar", "48000", "-ac", "2", tone],
        capture_output=True,
    )
    recp = subprocess.Popen(
        ["pw-record", "--target", rec, "--rate", "48000", "--channels", "2", wav],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    await asyncio.sleep(0.4)
    subprocess.run(
        ["pw-cat", "-p", "--target", play, "--rate", "48000", "--channels", "2", tone],
        capture_output=True,
    )
    await asyncio.sleep(0.3)
    recp.terminate()
    recp.wait(timeout=3)
    print("size", os.path.getsize(wav) if os.path.exists(wav) else None)
    vd = subprocess.run(
        ["ffmpeg", "-i", wav, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    for ln in vd.stderr.splitlines():
        if "volume" in ln or "Duration" in ln:
            print(ln.strip())

    rec2 = await resolve_pipewire_record_target("phone_uplink.monitor")
    play2 = await resolve_pipewire_playback_target("phone_uplink")
    print("phone record", rec2, "play", play2)
    wav2 = "/tmp/ser_phone.wav"
    recp = subprocess.Popen(
        ["pw-record", "--target", rec2, "--rate", "48000", "--channels", "2", wav2],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    await asyncio.sleep(0.4)
    subprocess.run(
        ["pw-cat", "-p", "--target", play2, "--rate", "48000", "--channels", "2", tone],
        capture_output=True,
    )
    await asyncio.sleep(0.3)
    recp.terminate()
    recp.wait(timeout=3)
    vd = subprocess.run(
        ["ffmpeg", "-i", wav2, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    for ln in vd.stderr.splitlines():
        if "volume" in ln or "Duration" in ln:
            print("phone", ln.strip())

if __name__ == "__main__":
    asyncio.run(main())
