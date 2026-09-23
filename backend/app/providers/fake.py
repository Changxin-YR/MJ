import hashlib
import io
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from app.providers.base import MediaResult


class FakeImageProvider:
    def generate(self, prompt: str, negative_prompt: str = "") -> MediaResult:
        digest = hashlib.sha256(prompt.encode()).digest()
        background = (22 + digest[0] // 4, 26 + digest[1] // 4, 42 + digest[2] // 4)
        image = Image.new("RGB", (1280, 720), background)
        draw = ImageDraw.Draw(image)
        for i in range(12):
            x = (digest[i] * 31 + i * 117) % 1280
            y = (digest[-i - 1] * 13 + i * 47) % 720
            radius = 80 + digest[i] // 2
            color = (min(255, background[0] + 10 + i * 4), min(255, background[1] + i * 7), min(255, background[2] + i * 3))
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        draw.rounded_rectangle((64, 500, 1216, 660), radius=24, fill=(10, 14, 24))
        title = "FRAMEFORGE · FAKE PROVIDER"
        draw.text((92, 526), title, fill=(141, 222, 224))
        safe_prompt = prompt.encode("ascii", "ignore").decode()[:100] or "Storyboard preview"
        draw.text((92, 570), safe_prompt, fill="white")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return MediaResult(content=output.getvalue(), mime="image/png", width=1280, height=720, model="fake-image-v1")


class FakeVideoProvider:
    def __init__(self):
        self.jobs: dict[str, MediaResult] = {}

    def submit(self, image: bytes, duration: float, prompt: str) -> str:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            output = root / "output.mp4"
            source.write_bytes(image)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "24", "-i", str(source), "-t", str(min(duration, 30)), "-vf", "scale=640:360,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-movflags", "+faststart", str(output)], check=True, timeout=90)
            result = MediaResult(content=output.read_bytes(), mime="video/mp4", width=640, height=360, duration=min(duration, 30), model="fake-video-v1")
        remote_id = str(uuid4())
        self.jobs[remote_id] = result
        return remote_id

    def get_status(self, remote_job_id: str) -> str:
        return "SUCCEEDED" if remote_job_id in self.jobs else "NOT_FOUND"

    def cancel(self, remote_job_id: str) -> None:
        self.jobs.pop(remote_job_id, None)

    def fetch_result(self, remote_job_id: str) -> MediaResult:
        return self.jobs.pop(remote_job_id)


class FakeTTSProvider:
    def synthesize(self, text: str, duration: float) -> MediaResult:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audio.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={min(duration, 30)}", "-ar", "22050", "-ac", "1", str(output)], check=True, timeout=30)
            return MediaResult(content=output.read_bytes(), mime="audio/wav", duration=min(duration, 30), model="fake-tts-v1")
