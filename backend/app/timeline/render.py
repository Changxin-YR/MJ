import subprocess
import tempfile
import wave
from pathlib import Path

from app.asset.storage import client as minio_client
from app.models import Asset
from app.providers.base import MediaResult


def download(asset: Asset) -> bytes:
    response = minio_client().get_object(asset.bucket, asset.object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def normalized_audio(data: bytes, directory: Path, index: int, target_seconds: float) -> bytes:
    source = directory / f"voice_{index}.wav"
    source.write_bytes(data)
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source), "-f", "s16le", "-ac", "1", "-ar", "22050", "pipe:1"], capture_output=True, check=True, timeout=30)
    expected = int(target_seconds * 22050) * 2
    return (proc.stdout[:expected] + b"\x00" * max(0, expected - len(proc.stdout)))[:expected]


def render_timeline(clips: list[tuple[Asset, Asset | None, str, float]]) -> MediaResult:
    if not clips:
        raise ValueError("Timeline has no clips")
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        inputs = []
        audio_chunks = []
        subtitles = []
        elapsed = 0.0
        for index, (video, voice, subtitle, duration) in enumerate(clips):
            video_path = directory / f"clip_{index}.mp4"
            video_path.write_bytes(download(video))
            inputs += ["-i", str(video_path)]
            audio_chunks.append(normalized_audio(download(voice), directory, index, duration) if voice else b"\x00" * (int(duration * 22050) * 2))
            if subtitle.strip():
                text = subtitle.replace("\r", " ").replace("\n", " ").replace("-->", "→")[:300]
                subtitles.append(f"{index + 1}\n{format_srt(elapsed)} --> {format_srt(elapsed + duration)}\n{text}\n")
            elapsed += duration
        audio_path = directory / "voice_track.wav"
        with wave.open(str(audio_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"".join(audio_chunks))
        inputs += ["-i", str(audio_path)]
        filter_parts = [f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p,setpts=PTS-STARTPTS[v{i}]" for i in range(len(clips))]
        filter_parts.append("".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]")
        video_map = "[v]"
        if subtitles:
            srt_path = directory / "subtitles.srt"
            srt_path.write_text("\n".join(subtitles), encoding="utf-8")
            filter_parts.append(f"[v]subtitles={srt_path.as_posix()}[outv]")
            video_map = "[outv]"
        output = directory / "final.mp4"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(filter_parts), "-map", video_map, "-map", f"{len(clips)}:a", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", "-t", str(elapsed), str(output)]
        subprocess.run(command, check=True, timeout=max(120, int(elapsed * 4)))
        return MediaResult(content=output.read_bytes(), mime="video/mp4", width=1280, height=720, duration=elapsed, model="ffmpeg-render-v1")


def format_srt(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"
