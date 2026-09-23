from app.asset.storage import validate
from app.providers.fake import FakeImageProvider, FakeTTSProvider, FakeVideoProvider


def test_fake_media_providers_obey_contract():
    image = FakeImageProvider().generate("Courier on a rooftop")
    assert validate(image)[:2] == (1280, 720)
    video_provider = FakeVideoProvider()
    task_id = video_provider.submit(image.content, 2, "gentle motion")
    assert video_provider.get_status(task_id) == "SUCCEEDED"
    video = video_provider.fetch_result(task_id)
    assert validate(video)[2] >= 1.9
    voice = FakeTTSProvider().synthesize("Hello", 2)
    assert validate(voice)[2] >= 1.9
    video_provider.cancel(task_id)
