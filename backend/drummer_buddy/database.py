from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    artist TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL CHECK (source_type IN ('local', 'youtube')),
    source_path TEXT,
    youtube_id TEXT,
    content_hash TEXT,
    original_filename TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    CHECK (
        (source_type = 'local' AND source_path IS NOT NULL AND content_hash IS NOT NULL)
        OR (source_type = 'youtube' AND youtube_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS songs_active_content_hash
    ON songs(content_hash) WHERE archived = 0 AND content_hash IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS songs_active_youtube_id
    ON songs(youtube_id) WHERE archived = 0 AND youtube_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS songs_archived_updated ON songs(archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    song_id TEXT NOT NULL REFERENCES songs(id),
    job_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed', 'interrupted'
    )),
    stage TEXT NOT NULL DEFAULT 'queued',
    progress REAL NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 1),
    message TEXT NOT NULL DEFAULT '',
    parameters_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    log_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS analysis_jobs_status_created ON analysis_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS analysis_jobs_song_created ON analysis_jobs(song_id, created_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(songs)")}
            if "tags_json" not in columns:
                connection.execute("ALTER TABLE songs ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
