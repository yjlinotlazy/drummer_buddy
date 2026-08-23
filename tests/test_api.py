from pathlib import Path

import yaml
import httpx2
import pytest

from drummer_buddy.app import create_app, services


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_library_api_and_media_range(tmp_path: Path, monkeypatch) -> None:
    library_dir = tmp_path / "library"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"library_dir": str(library_dir)}), encoding="utf-8")
    monkeypatch.setenv("DRUMMER_BUDDY_CONFIG", str(config_path))
    services.cache_clear()
    transport = httpx2.ASGITransport(app=create_app())

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/files/default")).json() == {"path": f"{library_dir / 'recordings'}/"}
        source = tmp_path / "sample.mp3"
        source.write_bytes(b"0123456789")
        (tmp_path / "samples").mkdir()
        (tmp_path / "sample.txt").write_text("not media", encoding="utf-8")
        completions = (await client.get("/api/files/complete", params={"path": str(tmp_path / "SAM")})).json()
        assert completions == [
            {"path": f"{tmp_path / 'samples'}/", "is_dir": True},
            {"path": str(source), "is_dir": False},
        ]
        created = await client.post(
            "/api/songs/local",
            json={"path": str(source), "title": "Local song", "artist": "Tester"},
        )
        assert created.status_code == 201
        song = created.json()

        response = await client.get(f"/api/songs/{song['id']}/media", headers={"Range": "bytes=2-5"})
        assert response.status_code == 206
        assert response.content == b"2345"

        assert (await client.post(f"/api/songs/{song['id']}/archive")).status_code == 200
        assert (await client.get("/api/songs")).json() == []
        assert len((await client.get("/api/songs?archived=true")).json()) == 1
        assert (await client.post(f"/api/songs/{song['id']}/restore")).status_code == 200

        youtube = await client.post(
            "/api/songs/youtube",
            json={"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Video"},
        )
        assert youtube.status_code == 201
        assert youtube.json()["youtube_id"] == "dQw4w9WgXcQ"
        assert (await client.post("/api/songs/youtube", json={"url": "dQw4w9WgXcQ"})).status_code == 409

    services.cache_clear()
