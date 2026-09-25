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


def render_timeline(
    clips: list[tuple[Asset, Asset | None, str, float]],
    extra_audio: list[tuple[Asset, float, float, str]] | None = None,
) -> MediaResult:
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
        extra_audio = extra_audio or []
        extra_inputs: list[tuple[int, float, float, str]] = []
        for extra_index, (asset, start_seconds, duration_seconds, kind) in enumerate(extra_audio):
            extra_path = directory / f"extra_{extra_index}.wav"
            extra_path.write_bytes(download(asset))
            input_index = len(clips) + 1 + extra_index
            inputs += ["-i", str(extra_path)]
            extra_inputs.append((input_index, start_seconds, duration_seconds, kind))
        filter_parts = [f"[{i}:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,format=yuv420p,setpts=PTS-STARTPTS[v{i}]" for i in range(len(clips))]
        filter_parts.append("".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]")
        video_map = "[v]"
        if subtitles:
            srt_path = directory / "subtitles.srt"
            srt_path.write_text("\n".join(subtitles), encoding="utf-8")
            filter_parts.append(f"[v]subtitles={srt_path.as_posix()}[outv]")
            video_map = "[outv]"
        audio_map = f"{len(clips)}:a"
        if extra_inputs:
            filter_parts.append(f"[{len(clips)}:a]volume=1.0[voice]")
            audio_labels = ["[voice]"]
            for index, (input_index, start_seconds, duration_seconds, kind) in enumerate(extra_inputs):
                volume = 0.18 if kind == "MUSIC" else 0.55
                delay_ms = max(0, round(start_seconds * 1000))
                label = f"extra{index}"
                filter_parts.append(
                    f"[{input_index}:a]atrim=0:{duration_seconds:.3f},asetpts=PTS-STARTPTS,"
                    f"adelay={delay_ms}:all=1,volume={volume}[{label}]"
                )
                audio_labels.append(f"[{label}]")
            filter_parts.append(
                "".join(audio_labels)
                + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
                "alimiter=limit=0.95[aout]"
            )
            audio_map = "[aout]"
        output = directory / "final.mp4"
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(filter_parts), "-map", video_map, "-map", audio_map, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", "-t", str(elapsed), str(output)]
        subprocess.run(command, check=True, timeout=max(120, int(elapsed * 4)))
        return MediaResult(content=output.read_bytes(), mime="video/mp4", width=1280, height=720, duration=elapsed, model="ffmpeg-render-v1")


def format_srt(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"
