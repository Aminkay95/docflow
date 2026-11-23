from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.services.retention import purge_due_files

pytestmark = pytest.mark.integration


def test_retention_expires_source_and_preserves_extraction_result(api):
    pdf = docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Total 10"]]))
    first = post_files(api, {"job_ref": "Expired"}, pdf).json()
    second = post_files(api, {"job_ref": "Current"}, pdf).json()
    drain(api)
    storage = api.app.state.storage
    now = api.app.state.clock.now()
    with api.session_factory() as session:
        job = session.get(Job, first["job_id"])
        job.status = "completed"
        job.terminal_at = now - timedelta(hours=7)
        job.terminal_reason = "completed"
        session.commit()
        files = {row.job_id: row for row in session.scalars(select(File))}
        first_file = files[first["job_id"]]
        second_file = files[second["job_id"]]
        first_page = session.scalar(select(Page).where(Page.file_id == first_file.id))
        first_id = first_file.id
        first_keys = (first_file.storage_key, first_page.image_key, first_page.thumbnail_key)
        current_key = second_file.storage_key

    url = f"/v1/results/{first['result_id']}"
    before = api.client.get(url, headers=api.headers).json()
    assert purge_due_files(api.session_factory, storage, now=now, retention_hours=6) == 1
    assert purge_due_files(api.session_factory, storage, now=now, retention_hours=6) == 0
    for key in filter(None, first_keys):
        with pytest.raises(FileNotFoundError):
            storage.get(key)
    with storage.get(current_key) as source:
        assert source.read(4) == b"%PDF"
    with api.session_factory() as session:
        assert session.get(File, first_id).purged_at is not None
        assert session.scalar(select(Page).where(Page.file_id == first_id)).text_layer is None
    assert api.client.get(url, headers=api.headers).json() == before
