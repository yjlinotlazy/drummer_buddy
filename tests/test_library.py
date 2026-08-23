from pathlib import Path

import pytest

from drummer_buddy.config import Config
from drummer_buddy.database import Database
from drummer_buddy.library import ArchivedDuplicateError, DuplicateSongError, Library, LibraryError, parse_youtube_id
from drummer_buddy.jobs import JobStore


@pytest.fixture
def library(tmp_path: Path) -> Library:
    config = Config(library_dir=tmp_path)
    (tmp_path / ".incoming").mkdir()
    (tmp_path / "songs").mkdir()
    database = Database(config.database_path)
    database.initialize()
    return Library(config, database)


def test_youtube_url_parsing() -> None:
    assert parse_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://youtu.be/dQw4w9WgXcQ?t=4") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(LibraryError):
        parse_youtube_id("https://example.com/not-youtube")


def test_local_import_duplicate_archive_and_restore(library: Library, tmp_path: Path) -> None:
    source = tmp_path / "outside.mp3"
    source.write_bytes(b"fake audio")
    song = library.import_local(str(source), "Song", "Artist")
    assert song["source_type"] == "local"
    assert library.media_path(song["id"]).read_bytes() == b"fake audio"

    with pytest.raises(DuplicateSongError):
        library.import_local(str(source))

    library.set_archived(song["id"], True)
    with pytest.raises(ArchivedDuplicateError):
        library.import_local(str(source))
    restored = library.set_archived(song["id"], False)
    assert restored["archived"] is False


def test_youtube_lifecycle(library: Library) -> None:
    song = library.add_youtube("dQw4w9WgXcQ", "Example")
    assert song["youtube_id"] == "dQw4w9WgXcQ"
    assert library.list_songs() == [song]

    updated = library.update_song(song["id"], "Renamed", "Someone")
    assert updated["title"] == "Renamed"
    archived = library.set_archived(song["id"], True)
    assert archived["archived"] is True
    assert library.list_songs() == []
    assert len(library.list_songs(archived=True)) == 1


def test_latest_drumless_result_becomes_playable(library: Library, tmp_path: Path) -> None:
    source = tmp_path / "track.mp3"
    source.write_bytes(b"raw")
    song = library.import_local(str(source))
    store = JobStore(library.database, library.config)
    job = store.create(song["id"], "drumless")
    assert store.begin(job["id"])
    result_dir = tmp_path / "songs" / song["id"] / "cache" / "jobs" / job["id"]
    result_dir.mkdir(parents=True)
    (result_dir / "original.flac").write_bytes(b"normalized")
    (result_dir / "drumless.flac").write_bytes(b"without drums")
    manifest = {"files": {"original": "original.flac", "drumless": "drumless.flac"}}
    store.finish(
        job["id"],
        "succeeded",
        result={"directory": str(result_dir.relative_to(tmp_path)), "manifest": manifest},
    )

    assert library.get_song(song["id"])["assets"]["drumless"] is True
    assert library.media_path(song["id"], "original").read_bytes() == b"normalized"
    assert library.media_path(song["id"], "drumless").read_bytes() == b"without drums"
