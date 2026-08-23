from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .config import Config
from .database import Database


SUPPORTED_EXTENSIONS = {".mp3", ".wav", ".flac", ".mp4"}
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LibraryError(Exception):
    pass


class SongNotFoundError(LibraryError):
    pass


class DuplicateSongError(LibraryError):
    def __init__(self, song: dict):
        super().__init__("source is already in the library")
        self.song = song


class ArchivedDuplicateError(DuplicateSongError):
    pass


def complete_local_path(value: str, limit: int = 40) -> list[dict]:
    candidate = value.strip()
    home = Path.home()
    use_tilde = candidate == "~" or candidate.startswith(f"~{os.sep}")
    if not candidate or candidate == "~":
        directory = home
        prefix = ""
    else:
        expanded = Path(candidate).expanduser()
        if candidate.endswith(os.sep):
            directory = expanded
            prefix = ""
        else:
            directory = expanded.parent
            prefix = expanded.name
        if not directory.is_absolute():
            directory = directory.absolute()

    try:
        entries = [
            entry
            for entry in directory.iterdir()
            if entry.name.casefold().startswith(prefix.casefold())
            and (entry.is_dir() or entry.suffix.lower() in SUPPORTED_EXTENSIONS)
        ]
    except OSError:
        return []
    entries.sort(key=lambda entry: (not entry.is_dir(), entry.name.casefold()))

    suggestions = []
    for entry in entries[:limit]:
        if use_tilde:
            try:
                display_path = f"~/{entry.relative_to(home).as_posix()}"
            except ValueError:
                display_path = str(entry.absolute())
        else:
            display_path = str(entry.absolute())
        is_directory = entry.is_dir()
        suggestions.append({"path": f"{display_path}{os.sep}" if is_directory else display_path, "is_dir": is_directory})
    return suggestions


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_youtube_id(value: str) -> str:
    candidate = value.strip()
    if YOUTUBE_ID.fullmatch(candidate):
        return candidate
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            parts = parsed.path.strip("/").split("/")
            video_id = parts[1] if len(parts) > 1 else ""
    if not YOUTUBE_ID.fullmatch(video_id):
        raise LibraryError("invalid YouTube URL or video ID")
    return video_id


def song_dict(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["archived"] = bool(result["archived"])
    return result


class Library:
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.database = database

    def list_songs(self, archived: bool = False) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM songs WHERE archived = ? ORDER BY updated_at DESC", (int(archived),)
            ).fetchall()
        return [self._decorate_song(song_dict(row)) for row in rows]

    def get_song(self, song_id: str) -> dict:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM songs WHERE id = ?", (song_id,)).fetchone()
        if row is None:
            raise SongNotFoundError(song_id)
        return self._decorate_song(song_dict(row))

    def _decorate_song(self, song: dict) -> dict:
        assets = {"drumless": False, "score": False}
        if song["source_type"] == "local":
            with self.database.connect() as connection:
                rows = connection.execute(
                    """SELECT job_type, result_json FROM analysis_jobs
                       WHERE song_id = ? AND status = 'succeeded' AND job_type IN ('drumless', 'score')
                       ORDER BY finished_at DESC""",
                    (song["id"],),
                ).fetchall()
            for row in rows:
                if not assets[row["job_type"]]:
                    assets[row["job_type"]] = True
        song["assets"] = assets
        return song

    def _find_duplicate(self, field: str, value: str) -> dict | None:
        if field not in {"content_hash", "youtube_id"}:
            raise ValueError("invalid duplicate field")
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM songs WHERE {field} = ? ORDER BY archived ASC LIMIT 1", (value,)
            ).fetchone()
        return song_dict(row) if row else None

    def import_local(self, source_value: str, title: str | None = None, artist: str = "") -> dict:
        source = Path(source_value).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise LibraryError("source path must be a readable regular file")
        if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise LibraryError("supported formats are MP3, WAV, FLAC, and MP4")

        incoming = self.config.library_dir / ".incoming" / f"{uuid4()}.part"
        digest = hashlib.sha256()
        try:
            with source.open("rb") as source_file, incoming.open("xb") as target_file:
                while chunk := source_file.read(1024 * 1024):
                    target_file.write(chunk)
                    digest.update(chunk)
            content_hash = digest.hexdigest()
            duplicate = self._find_duplicate("content_hash", content_hash)
            if duplicate:
                error = ArchivedDuplicateError if duplicate["archived"] else DuplicateSongError
                raise error(duplicate)

            song_id = str(uuid4())
            song_dir = self.config.library_dir / "songs" / song_id
            source_dir = song_dir / "source"
            source_dir.mkdir(parents=True)
            destination = source_dir / source.name
            incoming.replace(destination)
            relative_path = destination.relative_to(self.config.library_dir).as_posix()
            timestamp = utc_now()
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO songs
                       (id, title, artist, source_type, source_path, content_hash,
                        original_filename, archived, created_at, updated_at)
                       VALUES (?, ?, ?, 'local', ?, ?, ?, 0, ?, ?)""",
                    (
                        song_id,
                        (title or source.stem).strip() or source.stem,
                        artist.strip(),
                        relative_path,
                        content_hash,
                        source.name,
                        timestamp,
                        timestamp,
                    ),
                )
            return self.get_song(song_id)
        finally:
            incoming.unlink(missing_ok=True)

    def add_youtube(self, value: str, title: str | None = None, artist: str = "") -> dict:
        video_id = parse_youtube_id(value)
        duplicate = self._find_duplicate("youtube_id", video_id)
        if duplicate:
            error = ArchivedDuplicateError if duplicate["archived"] else DuplicateSongError
            raise error(duplicate)
        song_id = str(uuid4())
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO songs
                   (id, title, artist, source_type, youtube_id, archived, created_at, updated_at)
                   VALUES (?, ?, ?, 'youtube', ?, 0, ?, ?)""",
                (song_id, (title or f"YouTube {video_id}").strip(), artist.strip(), video_id, timestamp, timestamp),
            )
        return self.get_song(song_id)

    def update_song(self, song_id: str, title: str, artist: str) -> dict:
        clean_title = title.strip()
        if not clean_title:
            raise LibraryError("title cannot be empty")
        with self.database.connect() as connection:
            result = connection.execute(
                "UPDATE songs SET title = ?, artist = ?, updated_at = ? WHERE id = ?",
                (clean_title, artist.strip(), utc_now(), song_id),
            )
        if result.rowcount == 0:
            raise SongNotFoundError(song_id)
        return self.get_song(song_id)

    def set_archived(self, song_id: str, archived: bool) -> dict:
        with self.database.connect() as connection:
            try:
                result = connection.execute(
                    "UPDATE songs SET archived = ?, updated_at = ? WHERE id = ?",
                    (int(archived), utc_now(), song_id),
                )
            except sqlite3.IntegrityError as error:
                raise LibraryError("an active copy of this source already exists") from error
        if result.rowcount == 0:
            raise SongNotFoundError(song_id)
        return self.get_song(song_id)

    def media_path(self, song_id: str, variant: str = "original") -> Path:
        song = self.get_song(song_id)
        if song["source_type"] != "local":
            raise LibraryError("this song has no local media")
        if variant not in {"original", "drumless"}:
            raise LibraryError("media variant must be original or drumless")
        generated = self.result_asset_path(song_id, "drumless", variant)
        if generated:
            path = generated
        elif variant == "drumless":
            raise LibraryError("drumless audio is not ready")
        else:
            path = (self.config.library_dir / song["source_path"]).resolve()
        if not path.is_relative_to(self.config.library_dir) or not path.is_file():
            raise LibraryError("local media is unavailable")
        return path

    def result_asset_path(self, song_id: str, job_type: str, asset: str) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM analysis_jobs
                   WHERE song_id = ? AND job_type = ? AND status = 'succeeded'
                   ORDER BY finished_at DESC LIMIT 1""",
                (song_id, job_type),
            ).fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"])
        filename = result.get("manifest", {}).get("files", {}).get(asset)
        if not filename:
            return None
        path = (self.config.library_dir / result["directory"] / filename).resolve()
        if not path.is_relative_to(self.config.library_dir) or not path.is_file():
            return None
        return path

    def score(self, song_id: str) -> dict:
        self.get_song(song_id)
        path = self.result_asset_path(song_id, "score", "score")
        if not path:
            raise LibraryError("score is not ready")
        return json.loads(path.read_text(encoding="utf-8"))
