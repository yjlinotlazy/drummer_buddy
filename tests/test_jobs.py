import time
from pathlib import Path

from drummer_buddy.config import Config
from drummer_buddy.database import Database
from drummer_buddy.jobs import JobManager, JobStore
from drummer_buddy.library import Library


def wait_for(store: JobStore, job_id: str, statuses: set[str], timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get(job_id)
        if job["status"] in statuses:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {statuses}: {store.get(job_id)}")


def setup_jobs(tmp_path: Path) -> tuple[JobStore, JobManager, dict]:
    config = Config(library_dir=tmp_path)
    (tmp_path / ".incoming").mkdir()
    (tmp_path / "songs").mkdir()
    database = Database(config.database_path)
    database.initialize()
    library = Library(config, database)
    source = tmp_path / "source.mp3"
    source.write_bytes(b"audio")
    song = library.import_local(str(source))
    store = JobStore(database, config)
    return store, JobManager(store), song


def test_jobs_run_serially_and_commit_results(tmp_path: Path) -> None:
    store, manager, song = setup_jobs(tmp_path)
    first = manager.enqueue(song["id"], "mock", {"steps": 3, "delay": 0.02})
    second = manager.enqueue(song["id"], "mock", {"steps": 1, "delay": 0})
    manager.start()
    try:
        first_done = wait_for(store, first["id"], {"succeeded"})
        second_done = wait_for(store, second["id"], {"succeeded"})
    finally:
        manager.stop()

    assert second_done["started_at"] >= first_done["finished_at"]
    result_dir = tmp_path / first_done["result"]["directory"]
    assert (result_dir / "manifest.json").is_file()
    assert first_done["progress"] == 1
    assert (tmp_path / first_done["log_path"]).is_file()


def test_running_job_can_be_cancelled(tmp_path: Path) -> None:
    store, manager, song = setup_jobs(tmp_path)
    job = manager.enqueue(song["id"], "mock", {"steps": 100, "delay": 0.02})
    manager.start()
    try:
        wait_for(store, job["id"], {"running"})
        manager.cancel(job["id"])
        cancelled = wait_for(store, job["id"], {"cancelled"})
    finally:
        manager.stop()
    assert cancelled["finished_at"] is not None


def test_recovery_marks_running_job_interrupted(tmp_path: Path) -> None:
    store, _, song = setup_jobs(tmp_path)
    job = store.create(song["id"], "mock")
    assert store.begin(job["id"])
    assert store.recover_interrupted() == 1
    assert store.get(job["id"])["status"] == "interrupted"


def test_failure_and_shutdown_are_persisted(tmp_path: Path) -> None:
    store, manager, song = setup_jobs(tmp_path)
    failed_job = manager.enqueue(song["id"], "mock", {"fail": True})
    manager.start()
    failed = wait_for(store, failed_job["id"], {"failed"})
    assert "worker exited" in failed["error"]

    interrupted_job = manager.enqueue(song["id"], "mock", {"steps": 100, "delay": 0.02})
    wait_for(store, interrupted_job["id"], {"running"})
    manager.stop()
    assert store.get(interrupted_job["id"])["status"] == "interrupted"
