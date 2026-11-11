"""Content-free worker progress committed independently of extraction results."""

from sqlalchemy import select

from trade_extract.db.models.task import JobTask

STAGES = {
    "preparing": "Preparing source pages",
    "identifying": "Identifying documents",
    "reading": "Reading document pages",
    "extracting": "Extracting fields",
    "validating": "Checking extracted results",
    "finalizing": "Finalizing results",
}
KINDS = {
    "prepare_file": "preparing",
    "classify_segment": "identifying",
    "extract_segment": "reading",
    "finalize_submission": "finalizing",
}


def report(session, stage: str) -> None:
    callback = session.info.get("extraction_progress")
    if callback:
        callback(stage)


def write(factory, task_id, owner, attempt, claimed_at, clock, stage) -> bool:
    if stage not in STAGES:
        raise ValueError("unknown_progress_stage")
    with factory() as session:
        task = session.scalar(select(JobTask).where(JobTask.id == task_id).with_for_update())
        if (
            task is None
            or task.status != "running"
            or task.locked_by != owner
            or task.attempts != attempt
            or task.locked_at != claimed_at
        ):
            return False
        previous = (task.payload or {}).get("progress", {})
        token = claimed_at.isoformat() if claimed_at else None
        if previous.get("claim") != token or previous.get("attempt") != attempt:
            previous = {"attempt": attempt, "claim": token, "stages": []}
        stages = list(previous.get("stages", []))
        if previous.get("stage") != stage:
            stamp = clock.now().isoformat()
            stages = [row for row in stages if row["stage"] != stage]
            stages.append({"stage": stage, "at": stamp})
            previous = {**previous, "stage": stage, "updated_at": stamp, "stages": stages[-6:]}
        task.payload = {**(task.payload or {}), "progress": previous}
        session.commit()
    return True
