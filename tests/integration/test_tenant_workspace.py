import re

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from tests.integration.test_lifecycle import docs, drain
from tests.support import make_pdf
from trade_extract.db.models.client import Client
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.models.submission import Submission
from trade_extract.review.auth import password_hash

pytestmark = pytest.mark.integration


def sign_in(api, client_id, username):
    with api.session_factory() as session:
        session.add(
            Reviewer(
                id=f"usr_{username}",
                client_id=client_id,
                username=username,
                display_name=username,
                password_hash=password_hash("tenant-password"),
                role="client",
                is_active=False,
                client_login_enabled=True,
            )
        )
        session.commit()
    page = api.client.get("/review/login")
    token = re.search(r'name="csrf" type="hidden" value="([^"]+)"', page.text)[1]
    response = api.client.post(
        "/review/login",
        data={"username": username, "password": "tenant-password", "csrf": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/workspace"
    dashboard = api.client.get("/workspace")
    assert dashboard.status_code == 200
    assert dashboard.headers["cache-control"] == "no-store"
    csrf = re.search(r'id="csrf" value="([^"]+)"', dashboard.text)[1]
    return {"X-CSRF-Token": csrf}


def test_customer_cannot_access_another_clients_data(api, second_tenant):
    other_key, other_id = second_tenant
    pdf = docs(make_pdf([["COMMERCIAL INVOICE", "Seller Buyer Goods Total 10"]]))
    other_headers = {"Authorization": f"Bearer {other_key}"}
    other = api.client.post(
        "/v1/extract", headers=other_headers, data={"job_ref": "Other Only"}, files=pdf
    )
    assert other.status_code == 202
    other_job = other.json()["job_id"]
    drain(api)
    other_detail = api.client.get(f"/v1/jobs/{other_job}", headers=other_headers).json()
    other_doc = other_detail["documents"][0]["document_id"]

    assert api.client.get("/workspace").status_code == 401
    landing = api.client.get("/")
    assert landing.status_code == 200
    assert 'alt="Doc Hover"' in landing.text
    assert "Other Only" not in landing.text
    assert api.client.get("/review/static/landing.css").status_code == 200
    csrf = sign_in(api, api.client_id, "customer")
    assert "Other Only" not in api.client.get("/workspace").text
    assert api.client.get(f"/workspace?before={other_job}").status_code == 404
    assert api.client.get(f"/workspace/jobs/{other_job}").status_code == 404
    assert api.client.get(f"/workspace/documents/{other_doc}").status_code == 404
    for path in (
        "/review",
        "/review/clients",
        "/review/activity",
        "/review/queues/extraction",
        f"/review/jobs/{other_job}",
        f"/review/jobs/{other_job}/progress",
        f"/review/documents/{other_doc}",
        f"/review/media/documents/{other_doc}/source",
    ):
        assert api.client.get(path).status_code == 403, path

    for path in (
        f"/v1/results/{other.json()['result_id']}",
        f"/v1/jobs/{other_job}",
        f"/v1/jobs/{other_job}/documents",
        f"/v1/jobs/{other_job}/conflicts",
        f"/v1/documents/{other_doc}",
    ):
        assert api.client.get(path, headers=api.headers).status_code == 404, path

    assert api.client.post("/workspace/jobs", files=pdf).status_code == 403
    own = api.client.post(
        f"/workspace/jobs?client_id={other_id}",
        headers={**csrf, "Idempotency-Key": "tenant-idem"},
        data={"job_ref": "Own Shipment"},
        files=pdf,
    )
    assert own.status_code == 201, own.text
    own_url = own.json()["job_url"]
    assert "Own Shipment" in api.client.get(own_url).text
    assert "Own Shipment" in api.client.get("/workspace").text
    drain(api)
    own_job = own_url.rsplit("/", 1)[-1]
    own_detail = api.client.get(f"/v1/jobs/{own_job}", headers=api.headers).json()
    own_doc = own_detail["documents"][0]["document_id"]
    assert api.client.get(f"/workspace/documents/{own_doc}").status_code == 200
    assert api.client.get(f"/v1/jobs/{own_job}", headers=other_headers).status_code == 404

    with api.session_factory() as session:
        with pytest.raises(IntegrityError, match="fk_(submissions_job|files_submission)_tenant"):
            session.execute(
                update(Submission).where(Submission.job_id == own_job).values(client_id=other_id)
            )
        session.rollback()
        with pytest.raises(IntegrityError, match="ck_reviewers_role"):
            session.execute(
                update(Reviewer).where(Reviewer.id == "usr_customer").values(is_active=True)
            )
        session.rollback()
        session.get(Client, api.client_id).status = "suspended"
        session.commit()
    assert api.client.get("/workspace").status_code == 403
    assert api.client.get(f"/workspace/documents/{own_doc}").status_code == 403
    assert (
        api.client.post("/review/logout", headers=csrf, follow_redirects=False).status_code == 303
    )
    assert api.client.get("/workspace").status_code == 401
    with api.session_factory() as session:
        session.delete(session.get(Reviewer, "usr_customer"))
        session.commit()
