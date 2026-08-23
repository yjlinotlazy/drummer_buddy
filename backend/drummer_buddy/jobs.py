from __future__ import annotations

import json
import os
import selectors
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

from .config import Config
from .database import Database
from .library import SongNotFoundError, utc_now


TERMINAL_STATUSES = {"cancelled", "succeeded", "failed", "interrupted"}


class JobError(Exception):
    pass


class JobNotFoundError(JobError):
    pass


def job_dict(row) -> dict:
    result = dict(row)
    result["parameters"] = json.loads(result.pop("parameters_json"))
    result["result"] = json.loads(result.pop("result_json")) if result["result_json"] else None
    return result


class JobStore:
    def __init__(self, database: Database, config: Config):
        self.database = database
        self.config = config

    def recover_interrupted(self) -> int:
        with self.database.connect() as connection:
            result = connection.execute(
                """UPDATE analysis_jobs
                   SET status = 'interrupted', finished_at = ?, error = 'service stopped before task completed'
                   WHERE status IN ('running', 'cancelling')""",
                (utc_now(),),
            )
        return result.rowcount

    def create(self, song_id: str, job_type: str, parameters: dict | None = None) -> dict:
        with self.database.connect() as connection:
            song = connection.execute("SELECT id, source_type FROM songs WHERE id = ?", (song_id,)).fetchone()
        if song is None:
            raise SongNotFoundError(song_id)
        if song["source_type"] != "local":
            raise JobError("YouTube songs cannot be processed")
        if job_type not in {"mock", "drumless", "score"}:
            raise JobError("unsupported job type")
        if job_type != "mock":
            with self.database.connect() as connection:
                active = connection.execute(
                    """SELECT id FROM analysis_jobs
                       WHERE song_id = ? AND job_type = ? AND status IN ('queued', 'running', 'cancelling')
                       LIMIT 1""",
                    (song_id, job_type),
                ).fetchone()
            if active:
                raise JobError(f"a {job_type} task is already active for this song")
        job_id = str(uuid4())
        log_path = Path("songs") / song_id / "logs" / f"{job_id}.log"
        (self.config.library_dir / log_path).parent.mkdir(parents=True, exist_ok=True)
        timestamp = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO analysis_jobs
                   (id, song_id, job_type, status, stage, progress, message,
                    parameters_json, log_path, created_at)
                   VALUES (?, ?, ?, 'queued', 'queued', 0, 'Waiting to run', ?, ?, ?)""",
                (job_id, song_id, job_type, json.dumps(parameters or {}), log_path.as_posix(), timestamp),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return job_dict(row)

    def list(self, song_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM analysis_jobs"
        values: tuple = ()
        if song_id:
            query += " WHERE song_id = ?"
            values = (song_id,)
        query += " ORDER BY created_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [job_dict(row) for row in rows]

    def next_queued(self) -> dict | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
        return job_dict(row) if row else None

    def source_path(self, song_id: str) -> Path:
        with self.database.connect() as connection:
            row = connection.execute("SELECT source_path FROM songs WHERE id = ?", (song_id,)).fetchone()
        if row is None:
            raise SongNotFoundError(song_id)
        if not row["source_path"]:
            raise JobError("song has no local source")
        path = (self.config.library_dir / row["source_path"]).resolve()
        if not path.is_relative_to(self.config.library_dir) or not path.is_file():
            raise JobError("local source is unavailable")
        return path

    def drums_cache_path(self, song_id: str) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT result_json FROM analysis_jobs
                   WHERE song_id = ? AND job_type IN ('drumless', 'score') AND status = 'succeeded'
                   ORDER BY finished_at DESC LIMIT 1""",
                (song_id,),
            ).fetchone()
        if not row:
            return None
        result = json.loads(row["result_json"])
        filename = result["manifest"]["files"].get("drums")
        if not filename:
            return None
        path = (self.config.library_dir / result["directory"] / filename).resolve()
        return path if path.is_relative_to(self.config.library_dir) and path.is_file() else None

    def begin(self, job_id: str) -> bool:
        with self.database.connect() as connection:
            result = connection.execute(
                """UPDATE analysis_jobs SET status = 'running', stage = 'starting',
                   message = 'Starting worker', started_at = ? WHERE id = ? AND status = 'queued'""",
                (utc_now(), job_id),
            )
        return result.rowcount == 1

    def update_progress(self, job_id: str, stage: str, progress: float, message: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE analysis_jobs SET stage = ?, progress = ?, message = ?
                   WHERE id = ? AND status = 'running'""",
                (stage, max(0, min(1, progress)), message, job_id),
            )

    def finish(self, job_id: str, status: str, *, result: dict | None = None, error: str | None = None) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("job must finish in a terminal state")
        progress = 1 if status == "succeeded" else self.get(job_id)["progress"]
        message = "Completed" if status == "succeeded" else status.capitalize()
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE analysis_jobs SET status = ?, stage = ?, progress = ?, message = ?,
                   result_json = ?, error = ?, finished_at = ? WHERE id = ?""",
                (
                    status,
                    status,
                    progress,
                    message,
                    json.dumps(result) if result is not None else None,
                    error,
                    utc_now(),
                    job_id,
                ),
            )

    def request_cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] == "queued":
            self.finish(job_id, "cancelled")
        elif job["status"] == "running":
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE analysis_jobs SET status = 'cancelling', message = 'Cancelling' WHERE id = ?",
                    (job_id,),
                )
        return self.get(job_id)


class JobManager:
    def __init__(self, store: JobStore):
        self.store = store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.store.recover_interrupted()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="drummer-buddy-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive() and self._process and self._process.poll() is None:
                self._terminate_process()
                self._thread.join(timeout=1)

    def enqueue(self, song_id: str, job_type: str, parameters: dict | None = None) -> dict:
        job = self.store.create(song_id, job_type, parameters)
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.store.request_cancel(job_id)
        self._wake.set()
        return job

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job and self.store.begin(job["id"]):
                self._execute(self.store.get(job["id"]))
                continue
            self._wake.wait(0.5)
            self._wake.clear()

    def _execute(self, job: dict) -> None:
        job_id = job["id"]
        temp_dir = self.store.config.library_dir / ".incoming" / "jobs" / job_id
        final_dir = self.store.config.library_dir / "songs" / job["song_id"] / "cache" / "jobs" / job_id
        log_path = self.store.config.library_dir / job["log_path"]
        temp_dir.mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            "-m",
            "drummer_buddy.worker",
            "--job-type",
            job["job_type"],
            "--output-dir",
            str(temp_dir),
            "--input-path",
            str(self.store.source_path(job["song_id"])),
            "--analysis-python",
            str(self.store.config.analysis_python or sys.executable),
            "--parameters",
            json.dumps(job["parameters"]),
        ]
        cached_drums = self.store.drums_cache_path(job["song_id"])
        if cached_drums:
            command.extend(["--cached-drums", str(cached_drums)])
        environment = os.environ.copy()
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"Starting: {' '.join(command)}\n")
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=environment,
                    start_new_session=True,
                )
                assert self._process.stdout is not None
                selector = selectors.DefaultSelector()
                selector.register(self._process.stdout, selectors.EVENT_READ)
                while self._process.poll() is None:
                    if self._stop.is_set():
                        self._terminate_process()
                        try:
                            self._process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            self._kill_process()
                        self.store.finish(job_id, "interrupted", error="service stopped before task completed")
                        return
                    if self.store.get(job_id)["status"] == "cancelling":
                        self._terminate_process()
                        try:
                            self._process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            self._kill_process()
                        self.store.finish(job_id, "cancelled")
                        return
                    for key, _ in selector.select(timeout=0.1):
                        line = key.fileobj.readline()
                        if line:
                            self._handle_line(job_id, line, log)
                for line in self._process.stdout:
                    self._handle_line(job_id, line, log)
                if self._process.returncode != 0:
                    raise JobError(f"worker exited with code {self._process.returncode}")

            manifest_path = temp_dir / "manifest.json"
            if not manifest_path.is_file():
                raise JobError("worker did not produce manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            temp_dir.replace(final_dir)
            self.store.finish(job_id, "succeeded", result={"directory": str(final_dir.relative_to(self.store.config.library_dir)), "manifest": manifest})
        except Exception as error:
            current = self.store.get(job_id)
            if current["status"] not in TERMINAL_STATUSES:
                self.store.finish(job_id, "failed", error=str(error))
        finally:
            self._process = None
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _terminate_process(self) -> None:
        if self._process and self._process.poll() is None:
            if os.name == "posix":
                os.killpg(self._process.pid, signal.SIGTERM)
            else:
                self._process.terminate()

    def _kill_process(self) -> None:
        if self._process and self._process.poll() is None:
            if os.name == "posix":
                os.killpg(self._process.pid, signal.SIGKILL)
            else:
                self._process.kill()

    def _handle_line(self, job_id: str, line: str, log) -> None:
        log.write(line)
        log.flush()
        try:
            event = json.loads(line)
            self.store.update_progress(
                job_id,
                str(event.get("stage", "running")),
                float(event.get("progress", 0)),
                str(event.get("message", "")),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
