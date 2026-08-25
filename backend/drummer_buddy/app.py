from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import lru_cache
import json
import mimetypes
import os
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Config, load_config
from .database import Database
from .library import ArchivedDuplicateError, DuplicateSongError, Library, LibraryError, SongNotFoundError, complete_local_path
from .jobs import JobError, JobManager, JobNotFoundError, JobStore, TERMINAL_STATUSES
from .recorder import RecorderError, RecorderManager
from .playback import ServerPlaybackError, ServerPlayer


class LocalImport(BaseModel):
    path: str
    title: str | None = None
    artist: str = ""


class YouTubeImport(BaseModel):
    url: str
    title: str | None = None
    artist: str = ""


class SongUpdate(BaseModel):
    title: str
    artist: str = ""


class JobCreate(BaseModel):
    song_id: str
    job_type: str
    parameters: dict = Field(default_factory=dict)


class RecorderStart(BaseModel):
    directory: str
    song_name: str


class ServerPlayerLoad(BaseModel):
    song_id: str
    variant: str = "original"
    position: float = 0
    autoplay: bool = True
    loop: bool = False


class ServerPlayerCommand(BaseModel):
    action: str
    value: float | bool | None = None


def requested_byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        unit, raw_range = value.split("=", 1)
        if unit.strip().lower() != "bytes" or "," in raw_range:
            raise ValueError
        raw_start, raw_end = raw_range.split("-", 1)
        if not raw_start:
            suffix_length = int(raw_end)
            if suffix_length <= 0:
                raise ValueError
            return max(0, size - suffix_length), size - 1
        start = int(raw_start)
        end = min(int(raw_end), size - 1) if raw_end else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError
        return start, end
    except (ValueError, TypeError):
        raise HTTPException(status_code=416, headers={"Content-Range": f"bytes */{size}"})


def media_response(path: Path, range_header: str | None) -> Response:
    size = path.stat().st_size
    byte_range = requested_byte_range(range_header, size)
    start, end = byte_range or (0, size - 1)
    length = end - start + 1

    async def chunks():
        with path.open("rb") as media:
            media.seek(start)
            remaining = length
            while remaining:
                chunk = media.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f'inline; filename="{path.name.replace(chr(34), "")}"',
    }
    status_code = 200
    if byte_range:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status_code = 206
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return StreamingResponse(chunks(), status_code=status_code, media_type=media_type, headers=headers)


@lru_cache
def services() -> tuple[Config, Library]:
    config = load_config()
    database = Database(config.database_path)
    database.initialize()
    return config, Library(config, database)


@lru_cache
def job_manager() -> JobManager:
    config, _ = services()
    database = Database(config.database_path)
    database.initialize()
    return JobManager(JobStore(database, config))


@lru_cache
def recorder_manager() -> RecorderManager:
    return RecorderManager(services()[0])


@lru_cache
def server_player() -> ServerPlayer:
    return ServerPlayer(services()[0].library_dir)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    manager = job_manager()
    manager.start()
    try:
        yield
    finally:
        server_player().shutdown()
        recorder_manager().shutdown()
        manager.stop()


def duplicate_response(error: DuplicateSongError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "archived_duplicate" if isinstance(error, ArchivedDuplicateError) else "duplicate",
            "message": str(error),
            "song": error.song,
        },
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Drummer Buddy", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        config, _ = services()
        return {"status": "ok", "library_dir": str(config.library_dir)}

    @app.get("/api/files/complete")
    async def complete_file_path(path: str = Query("")) -> list[dict]:
        return complete_local_path(path)

    @app.get("/api/files/default")
    async def default_file_path() -> dict:
        directory = services()[0].recording_dir
        return {"path": f"{directory}{os.sep}"}

    @app.get("/api/songs")
    async def list_songs(archived: bool = Query(False)) -> list[dict]:
        return services()[1].list_songs(archived)

    @app.get("/api/songs/{song_id}")
    async def get_song(song_id: str) -> dict:
        try:
            return services()[1].get_song(song_id)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error

    @app.post("/api/songs/local", status_code=201)
    async def import_local(request: LocalImport) -> dict:
        try:
            return services()[1].import_local(request.path, request.title, request.artist)
        except DuplicateSongError as error:
            raise duplicate_response(error) from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/songs/youtube", status_code=201)
    async def import_youtube(request: YouTubeImport) -> dict:
        try:
            return services()[1].add_youtube(request.url, request.title, request.artist)
        except DuplicateSongError as error:
            raise duplicate_response(error) from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/recorder")
    async def recorder_status() -> dict:
        return recorder_manager().status()

    @app.get("/api/player")
    async def server_player_status() -> dict:
        return server_player().status()

    @app.post("/api/player/load")
    async def load_server_player(request: ServerPlayerLoad) -> dict:
        try:
            path = services()[1].media_path(request.song_id, request.variant)
            return server_player().load(path, request.song_id, request.variant, request.position, request.autoplay, request.loop)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except (LibraryError, ServerPlaybackError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/player/command")
    async def command_server_player(request: ServerPlayerCommand) -> dict:
        try:
            return server_player().command(request.action, request.value)
        except ServerPlaybackError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/recorder/start")
    async def start_recording(request: RecorderStart) -> dict:
        try:
            return recorder_manager().start(request.directory, request.song_name)
        except RecorderError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/recorder/stop")
    async def stop_recording() -> dict:
        try:
            return recorder_manager().stop()
        except RecorderError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/recorder/clean", status_code=201)
    async def clean_recording() -> dict:
        try:
            path = recorder_manager().completed_path()
            song = services()[1].register_local(str(path))
            recorder_manager().mark_imported(song["id"])
            return song
        except DuplicateSongError as error:
            raise duplicate_response(error) from error
        except (RecorderError, LibraryError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.patch("/api/songs/{song_id}")
    async def update_song(song_id: str, request: SongUpdate) -> dict:
        try:
            return services()[1].update_song(song_id, request.title, request.artist)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/songs/{song_id}/trim-preview", status_code=201)
    async def create_trim_preview(song_id: str) -> dict:
        try:
            services()[1].create_trim_preview(song_id)
            return {"url": f"/api/songs/{song_id}/trim-preview/media"}
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/songs/{song_id}/trim-preview/media")
    async def trim_preview_media(song_id: str, request: Request) -> Response:
        try:
            return media_response(services()[1].trim_preview_path(song_id), request.headers.get("range"))
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/songs/{song_id}/trim-commit")
    async def commit_trim_preview(song_id: str) -> dict:
        try:
            return services()[1].apply_trim_preview(song_id)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except DuplicateSongError as error:
            raise duplicate_response(error) from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/songs/{song_id}/archive")
    async def archive_song(song_id: str) -> dict:
        try:
            return services()[1].set_archived(song_id, True)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error

    @app.post("/api/songs/{song_id}/restore")
    async def restore_song(song_id: str) -> dict:
        try:
            return services()[1].set_archived(song_id, False)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/songs/{song_id}/media")
    async def song_media(song_id: str, request: Request, variant: str = "original") -> Response:
        try:
            path = services()[1].media_path(song_id, variant)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return media_response(path, request.headers.get("range"))

    @app.get("/api/songs/{song_id}/score")
    async def song_score(song_id: str) -> dict:
        try:
            return services()[1].score(song_id)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except LibraryError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/jobs")
    async def list_jobs(song_id: str | None = None) -> list[dict]:
        return job_manager().store.list(song_id)

    @app.post("/api/jobs", status_code=201)
    async def create_job(request: JobCreate) -> dict:
        try:
            parameters = dict(request.parameters)
            if request.job_type == "drumless":
                parameters.setdefault("format", services()[0].drumless_format)
            return job_manager().enqueue(request.song_id, request.job_type, parameters)
        except SongNotFoundError as error:
            raise HTTPException(status_code=404, detail="song not found") from error
        except JobError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        try:
            return job_manager().store.get(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict:
        try:
            return job_manager().cancel(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        try:
            job_manager().store.get(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

        async def stream():
            previous = ""
            while True:
                job = job_manager().store.get(job_id)
                payload = json.dumps(job)
                if payload != previous:
                    yield f"event: job\ndata: {payload}\n\n"
                    previous = payload
                if job["status"] in TERMINAL_STATUSES:
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/jobs/{job_id}/log")
    async def job_log(job_id: str) -> PlainTextResponse:
        try:
            job = job_manager().store.get(job_id)
        except JobNotFoundError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        config, _ = services()
        path = (config.library_dir / job["log_path"]).resolve()
        if not path.is_relative_to(config.library_dir):
            raise HTTPException(status_code=400, detail="invalid log path")
        content = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        return PlainTextResponse(content)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
