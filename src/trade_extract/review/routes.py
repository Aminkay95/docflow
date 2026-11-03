"""Internal session-authenticated two-track review console."""

import hmac
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trade_extract.api.deps import get_session
from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ReviewItem
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.review import ReviewResolution
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.repositories.evidence import conflicts_for
from trade_extract.review.auth import principal, signer, verify_password
from trade_extract.services.jobs import read_document
from trade_extract.services.review import resolve

router = APIRouter(prefix="/review", include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    reason: str = Field(min_length=4, max_length=4000)
    field_path: str | None = None
    corrected_value: dict[str, Any] | None = None
    chosen_document_id: str | None = None
    expected_fingerprint: str | None = None


def render(request: Request, title: str, **context: Any) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="console.html",
        context=dict(
            title=title,
            csrf=getattr(request.state, "review_session", {}).get("csrf", ""),
            read_only=getattr(request.state, "review_role", None) == "auditor",
            **context,
        ),
    )


def history(
    session: Session, *, document_id: str | None = None, conflict_id: str | None = None
) -> list[dict[str, Any]]:
    query = (
        select(ReviewResolution, Reviewer.display_name)
        .join(Reviewer, Reviewer.id == ReviewResolution.reviewer_id)
        .join(ReviewItem, ReviewItem.id == ReviewResolution.review_item_id)
    )
    query = (
        query.where(ReviewItem.document_id == document_id)
        if document_id
        else query.where(ReviewItem.conflict_id == conflict_id)
    )
    return [
        dict(
            field_path=label.field_path,
            outcome=label.outcome,
            reason=label.reason,
            created_at=label.created_at,
            reviewer_id=label.reviewer_id,
            reviewer_name=name,
            chosen_document_id=label.chosen_document_id,
        )
        for label, name in session.execute(
            query.order_by(ReviewResolution.created_at, ReviewResolution.id)
        )
    ]


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    token = secrets.token_urlsafe(32)
    response = render(request, "Log in", login=True, login_csrf=token)
    response.set_cookie(
        "review_login",
        signer(request).dumps(token),
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        max_age=600,
    )
    return response


@router.post("/login")
def login(
    request: Request,
    username: str = Form(min_length=1, max_length=128),
    password: str = Form(min_length=1, max_length=1024),
    csrf: str = Form(min_length=1, max_length=256),
    session: Session = Depends(get_session),
) -> Response:
    try:
        expected = signer(request).loads(request.cookies.get("review_login", ""), max_age=600)
        if not hmac.compare_digest(csrf, expected):
            raise ValueError("csrf")
    except Exception:
        raise HTTPException(403, "Invalid login CSRF token") from None
    now = request.app.state.clock.now()
    with request.app.state.review_login_lock:
        attempts = request.app.state.review_login_attempts
        for key in list(attempts):
            window = 60 if key.startswith("ip:") else 3600
            if not attempts[key] or attempts[key][-1] <= now.timestamp() - window:
                del attempts[key]
        for key, window, limit in (
            (f"ip:{request.client.host if request.client else ''}", 60, 5),
            (f"user:{username.lower()}", 3600, 10),
        ):
            queue = attempts[key]
            while queue and queue[0] <= now.timestamp() - window:
                queue.popleft()
            if len(queue) >= limit:
                raise HTTPException(429, "Try again later")
            queue.append(now.timestamp())
    reviewer = session.scalar(
        select(Reviewer).where(func.lower(Reviewer.username) == username.lower())
    )
    if (
        reviewer is None
        or not reviewer.is_active
        or not verify_password(password, reviewer.password_hash)
    ):
        raise HTTPException(401, "Invalid username or password")
    reviewer.last_login_at = now
    session.commit()
    response = RedirectResponse("/review", status_code=303)
    response.delete_cookie("review_login")
    response.set_cookie(
        "review_session",
        signer(request).dumps(
            dict(
                id=reviewer.id,
                epoch=reviewer.session_epoch,
                issued=now.timestamp(),
                seen=now.timestamp(),
                csrf=secrets.token_urlsafe(32),
            )
        ),
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        max_age=28800,
    )
    return response


@router.post("/logout")
@router.post("/logout-all")
def logout(
    request: Request,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    if request.url.path.endswith("logout-all"):
        reviewer.session_epoch += 1
        session.commit()
    response = RedirectResponse("/review/login", status_code=303)
    response.delete_cookie("review_session")
    return response


@router.get("")
@router.get("/queues/{track}")
@router.get("/queues/{track}/rows")
def queue(
    request: Request,
    track: str | None = None,
    cursor: str | None = None,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    if track not in (None, "extraction", "conflicts"):
        raise HTTPException(404)
    query = select(ReviewItem).where(ReviewItem.status.in_(("open", "in_progress")))
    if track:
        query = query.where(
            ReviewItem.track == ("conflict" if track == "conflicts" else "extraction")
        )
        if cursor:
            previous = session.scalar(query.where(ReviewItem.id == cursor))
            if previous is None:
                raise HTTPException(400, "Invalid queue cursor; return to the queue")
            query = query.where(
                (ReviewItem.created_at > previous.created_at)
                | ((ReviewItem.created_at == previous.created_at) & (ReviewItem.id > previous.id))
            )
    counts = dict(
        session.execute(
            select(ReviewItem.track, func.count())
            .where(ReviewItem.status.in_(("open", "in_progress")))
            .group_by(ReviewItem.track)
        ).all()
    )
    # Dashboard has independent counts/links; only a selected track gets a list.
    items = (
        list(session.scalars(query.order_by(ReviewItem.created_at, ReviewItem.id).limit(101)))
        if track
        else []
    )
    next_cursor = items[99].id if len(items) > 100 else None
    return render(
        request,
        "Review queues",
        items=items[:100],
        track=track,
        counts=counts,
        next_cursor=next_cursor,
    )


@router.get("/documents/{document_id}")
def document(
    request: Request,
    document_id: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    row = session.get(Document, document_id)
    if row is None:
        raise HTTPException(404)
    item = session.scalar(
        select(ReviewItem).where(
            ReviewItem.document_id == row.id, ReviewItem.status.in_(("open", "in_progress"))
        )
    )
    payload = read_document(session, row.id, row.client_id)
    from trade_extract.domain.conditioning import CoordinateTransform
    from trade_extract.domain.provenance import BBox

    pages = list(session.scalars(select(Page).where(Page.file_id == row.file_id)))
    transforms = {
        p.page_index: CoordinateTransform(
            **{k: tuple(v) for k, v in p.conditioning["transform"].items()}
        )
        for p in pages
        if (p.conditioning or {}).get("transform")
    }
    display_fields = {**(payload["fields"] or {}), **payload["extension"]}
    boxes = {
        path: transforms[field["provenance"]["page_index"]]
        .box(BBox.model_validate(field["provenance"]["bbox"]))
        .model_dump()
        for path, field in display_fields.items()
        if field["provenance"]["page_index"] in transforms
    }
    return render(
        request,
        "Extraction review",
        document=payload,
        display_fields=display_fields,
        boxes=boxes,
        item=item,
        labels=history(session, document_id=row.id),
    )


@router.get("/conflicts/{conflict_id}")
def conflict(
    request: Request,
    conflict_id: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    row = session.get(ConflictRecord, conflict_id)
    if row is None:
        raise HTTPException(404)
    evidence = conflicts_for(
        session, job_id=row.job_id, client_id=row.client_id, conflict_ids=[row.id]
    )[0]
    item = session.scalar(
        select(ReviewItem).where(
            ReviewItem.conflict_id == row.id, ReviewItem.status.in_(("open", "in_progress"))
        )
    )
    return render(
        request,
        "Conflict review",
        conflict=evidence,
        item=item,
        labels=history(session, conflict_id=row.id),
    )


@router.post("/queues/{item_id}/resolve")
def resolution(
    request: Request,
    item_id: str,
    payload: ResolutionInput,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(404)
    job = session.get_one(Job, item.job_id)
    label = resolve(
        session,
        item_id=item.id,
        client_id=job.client_id,
        reviewer=reviewer,
        now=request.app.state.clock.now(),
        **payload.model_dump(),
    )
    session.commit()
    return dict(resolution_id=label.id)


@router.post("/queues/{item_id}/{action}")
def assignment(
    request: Request,
    item_id: str,
    action: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    item = session.get(ReviewItem, item_id)
    if item is None:
        raise HTTPException(404)
    session.execute(select(Job).where(Job.id == item.job_id).with_for_update()).one()
    session.refresh(item, with_for_update=True)
    if action not in ("claim", "release"):
        raise HTTPException(404)
    if item.status not in ("open", "in_progress") or item.assigned_to not in (None, reviewer.id):
        raise HTTPException(409, "Item changed; reload")
    item.assigned_to = reviewer.id if action == "claim" else None
    item.status = "in_progress" if action == "claim" else "open"
    session.commit()
    return dict(status=item.status)


@router.get("/media/documents/{document_id}/pages/{page_index}")
@router.get("/media/documents/{document_id}/pages/{page_index}/thumb")
def media(
    request: Request,
    document_id: str,
    page_index: int,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None or not document.page_start <= page_index <= document.page_end:
        raise HTTPException(404)
    page = session.scalar(
        select(Page).where(Page.file_id == document.file_id, Page.page_index == page_index)
    )
    key = (
        (page.thumbnail_key if request.url.path.endswith("/thumb") else page.image_key)
        if page
        else None
    )
    if document.purged_at or not key:
        return HTMLResponse(
            "Source media expired. The extraction record remains available.",
            status_code=410,
            headers={"Cache-Control": "no-store"},
        )
    try:
        with request.app.state.storage.get(key) as stream:
            data = stream.read()
    except FileNotFoundError:
        raise HTTPException(410, "Source media expired") from None
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
