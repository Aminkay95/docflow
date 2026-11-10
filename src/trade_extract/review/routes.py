"""Internal session-authenticated two-track review console."""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from trade_extract.api.deps import get_clock, get_session, get_settings, get_storage
from trade_extract.config import Settings
from trade_extract.db.models.client import Client
from trade_extract.db.models.document import Document
from trade_extract.db.models.evidence import ConflictRecord, ConflictSource, ReviewItem
from trade_extract.db.models.file import File
from trade_extract.db.models.job import Job
from trade_extract.db.models.page import Page
from trade_extract.db.models.password_reset import PasswordReset
from trade_extract.db.models.review import ReviewResolution
from trade_extract.db.models.reviewer import Reviewer
from trade_extract.db.models.submission import Submission
from trade_extract.db.repositories.evidence import conflicts_for, review_counts
from trade_extract.domain import errors
from trade_extract.domain.clock import Clock
from trade_extract.domain.ids import new_id
from trade_extract.review.auth import password_hash, principal, signer, verify_password
from trade_extract.review.line_items import invoice_line_items
from trade_extract.review.mail import send_password_reset_email
from trade_extract.services import ingest
from trade_extract.services.jobs import emit_terminal_result, job_result, read_document
from trade_extract.services.limits import (
    spool_submission,
    validate_idempotency_key,
    validate_submission,
)
from trade_extract.services.review import confirm_remaining, resolve
from trade_extract.services.storage import ObjectStorage

router = APIRouter(prefix="/review", include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = logging.getLogger(__name__)

RESET_REQUEST_LIMIT = 3
RESET_REQUEST_WINDOW_SECONDS = 900

JOB_STATUSES = ("queued", "processing", "review_required", "completed", "failed")
JOBS_PER_PAGE = 25


def _badge(value: str | None) -> str:
    return {
        "completed": "success",
        "resolved": "success",
        "accepted": "success",
        "confirmed": "success",
        "active": "success",
        "trusted": "success",
        "processing": "info",
        "in_progress": "info",
        "client_notified": "info",
        "review_required": "warning",
        "open": "warning",
        "queued": "neutral",
        "unseen": "neutral",
        "waived": "neutral",
        "observed": "info",
        "failed": "danger",
        "unreadable": "danger",
        "unreadable_confirmed": "danger",
        "failure_confirmed": "danger",
        "suspended": "danger",
        "terminated": "danger",
    }.get(value or "", "neutral")


def _humanize(value: str | None) -> str:
    return (value or "").replace("_", " ").strip().title()


def _when(value: Any) -> str:
    return value.strftime("%d %b %Y, %H:%M") if value else "—"


templates.env.filters["badge"] = _badge
templates.env.filters["humanize"] = _humanize
templates.env.filters["when"] = _when


def counts_for(session: Session) -> dict[str, int]:
    return dict(
        session.execute(
            select(ReviewItem.track, func.count())
            .where(ReviewItem.status.in_(("open", "in_progress")))
            .group_by(ReviewItem.track)
        ).all()
    )


def resolutions_for(session: Session, limit: int) -> list[Any]:
    return list(
        session.execute(
            select(ReviewResolution, Reviewer.display_name, ReviewItem, Job.job_ref)
            .join(Reviewer, Reviewer.id == ReviewResolution.reviewer_id)
            .join(ReviewItem, ReviewItem.id == ReviewResolution.review_item_id)
            .join(Job, Job.id == ReviewItem.job_id)
            .order_by(ReviewResolution.created_at.desc(), ReviewResolution.id.desc())
            .limit(limit)
        ).all()
    )


class ResolutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str
    reason: str = Field(min_length=4, max_length=4000)
    field_path: str | None = None
    corrected_value: dict[str, Any] | None = None
    chosen_document_id: str | None = None
    expected_fingerprint: str | None = None


def _breadcrumbs(path: str) -> list[dict[str, str]]:
    """Route-aware breadcrumb trail for the review shell.

    Labels are deliberately generic where an id would be noisy or sensitive.
    The current page is the final entry and has no ``href``.
    """

    if path.startswith("/review/jobs/new"):
        return [
            {"label": "Jobs", "href": "/review/jobs"},
            {"label": "New job", "href": ""},
        ]
    if path.startswith("/review/jobs/"):
        return [
            {"label": "Jobs", "href": "/review/jobs"},
            {"label": "Job details", "href": ""},
        ]
    if path.startswith("/review/jobs"):
        return [{"label": "Jobs", "href": ""}]
    if path.startswith("/review/queues/extraction"):
        return [{"label": "Extraction queue", "href": ""}]
    if path.startswith("/review/queues/conflicts"):
        return [{"label": "Conflict queue", "href": ""}]
    if path.startswith("/review/documents/"):
        return [
            {"label": "Extraction queue", "href": "/review/queues/extraction"},
            {"label": "Document", "href": ""},
        ]
    if path.startswith("/review/conflicts/"):
        return [
            {"label": "Conflict queue", "href": "/review/queues/conflicts"},
            {"label": "Conflict", "href": ""},
        ]
    if path.startswith("/review/activity"):
        return [{"label": "Activity", "href": ""}]
    if path.startswith("/review/team"):
        return [{"label": "Team", "href": ""}]
    if path.startswith("/review/clients"):
        return [{"label": "Clients", "href": ""}]
    return [{"label": "Dashboard", "href": ""}]


def render(
    request: Request, title: str, template: str = "dashboard.html", **context: Any
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=dict(
            title=title,
            csrf=getattr(request.state, "review_session", {}).get("csrf", ""),
            read_only=getattr(request.state, "review_role", None) == "auditor",
            reviewer=getattr(request.state, "reviewer", None),
            counts=context.pop("counts", {}),
            breadcrumbs=_breadcrumbs(request.url.path),
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


def auth_response(
    request: Request,
    template: str,
    *,
    title: str,
    cookie: str,
    status_code: int = 200,
    **context: Any,
) -> Response:
    """Render an unauthenticated form with a double-submit CSRF cookie."""

    token = secrets.token_urlsafe(32)
    settings = request.app.state.settings
    response = templates.TemplateResponse(
        request=request,
        name=template,
        context=dict(
            title=title,
            csrf=token,
            reset_ttl_minutes=settings.password_reset_ttl_minutes,
            reset_min_length=settings.password_reset_min_length,
            **context,
        ),
        status_code=status_code,
    )
    response.set_cookie(
        cookie,
        signer(request).dumps(token),
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        max_age=600,
    )
    return response


def auth_csrf_ok(request: Request, cookie: str, submitted: str) -> bool:
    try:
        expected = signer(request).loads(request.cookies.get(cookie, ""), max_age=600)
        return hmac.compare_digest(submitted, expected)
    except Exception:
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def reset_row(
    session: Session,
    token: str,
    now: Any,
    *,
    lock: bool = False,
) -> PasswordReset | None:
    if not token:
        return None
    query = select(PasswordReset).where(PasswordReset.token_hash == token_digest(token))
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return None
    return row


def reset_request_limited(request: Request, key: str) -> bool:
    now = request.app.state.clock.now().timestamp()
    with request.app.state.review_reset_lock:
        attempts = request.app.state.review_reset_attempts
        queue = attempts[key]
        while queue and queue[0] <= now - RESET_REQUEST_WINDOW_SECONDS:
            queue.popleft()
        if len(queue) >= RESET_REQUEST_LIMIT:
            return True
        queue.append(now)
    return False


def issue_password_reset(request: Request, session: Session, reviewer: Reviewer, now: Any) -> None:
    settings = request.app.state.settings
    session.execute(
        update(PasswordReset)
        .where(PasswordReset.reviewer_id == reviewer.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    session.add(
        PasswordReset(
            id=new_id("pwr_"),
            reviewer_id=reviewer.id,
            token_hash=token_digest(token),
            expires_at=now + timedelta(minutes=settings.password_reset_ttl_minutes),
            requested_ip=request.client.host if request.client else None,
        )
    )
    session.commit()
    reset_url = f"{settings.base_url}/review/reset-password?token={quote(token)}"
    try:
        send_password_reset_email(settings, reviewer.email or "", reset_url)
    except Exception:  # noqa: BLE001 - delivery failure must not reveal account state
        logger.exception("password reset email delivery failed")


def login_response(
    request: Request,
    *,
    status_code: int = 200,
    error: str = "",
    notice: str = "",
    username: str = "",
) -> Response:
    token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context=dict(
            title="Log in",
            login=True,
            login_csrf=token,
            login_error=error,
            login_notice=notice,
            login_username=username,
        ),
        status_code=status_code,
    )
    response.set_cookie(
        "review_login",
        signer(request).dumps(token),
        httponly=True,
        secure=request.app.state.settings.environment == "production",
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    notice = (
        "Password updated. Sign in with your new password."
        if request.query_params.get("reset") == "1"
        else ""
    )
    return login_response(request, notice=notice)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request) -> Response:
    return auth_response(
        request,
        "forgot_password.html",
        title="Forgot password",
        cookie="review_forgot",
    )


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(min_length=3, max_length=320),
    csrf: str = Form(min_length=1, max_length=256),
    session: Session = Depends(get_session),
) -> Response:
    def page(**context: Any) -> Response:
        return auth_response(
            request,
            "forgot_password.html",
            title="Forgot password",
            cookie="review_forgot",
            email=email,
            **context,
        )

    if not auth_csrf_ok(request, "review_forgot", csrf):
        return page(status_code=403, error="Your request expired. Reload the page and try again.")
    if reset_request_limited(request, request.client.host if request.client else "unknown"):
        return page(
            status_code=429,
            error="Too many reset requests. Wait a few minutes and try again.",
        )
    reviewer = session.scalar(
        select(Reviewer).where(
            func.lower(Reviewer.email) == email.strip().lower(),
            Reviewer.is_active.is_(True),
        )
    )
    if reviewer is not None:
        issue_password_reset(request, session, reviewer, request.app.state.clock.now())
    return page(sent=True)


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(
    request: Request,
    token: str = "",
    session: Session = Depends(get_session),
) -> Response:
    if reset_row(session, token, request.app.state.clock.now()) is None:
        return auth_response(
            request,
            "reset_password.html",
            title="Reset password",
            cookie="review_reset",
            invalid=True,
            status_code=400,
        )
    return auth_response(
        request,
        "reset_password.html",
        title="Reset password",
        cookie="review_reset",
        reset_token=token,
    )


@router.post("/reset-password")
def reset_password(
    request: Request,
    token: str = Form(min_length=1, max_length=256),
    password: str = Form(min_length=1, max_length=1024),
    confirm: str = Form(min_length=1, max_length=1024),
    csrf: str = Form(min_length=1, max_length=256),
    session: Session = Depends(get_session),
) -> Response:
    settings = request.app.state.settings
    now = request.app.state.clock.now()

    def page(**context: Any) -> Response:
        return auth_response(
            request,
            "reset_password.html",
            title="Reset password",
            cookie="review_reset",
            reset_token=token,
            **context,
        )

    if not auth_csrf_ok(request, "review_reset", csrf):
        return page(
            status_code=403,
            error="Your reset form expired. Reload the link and try again.",
        )
    row = reset_row(session, token, now, lock=True)
    if row is None:
        return auth_response(
            request,
            "reset_password.html",
            title="Reset password",
            cookie="review_reset",
            invalid=True,
            status_code=400,
        )
    if password != confirm:
        return page(status_code=400, error="The two passwords do not match.")
    if len(password) < settings.password_reset_min_length:
        return page(
            status_code=400,
            error=f"Use at least {settings.password_reset_min_length} characters.",
        )
    reviewer = session.get(Reviewer, row.reviewer_id)
    if reviewer is None or not reviewer.is_active:
        return auth_response(
            request,
            "reset_password.html",
            title="Reset password",
            cookie="review_reset",
            invalid=True,
            status_code=400,
        )
    reviewer.password_hash = password_hash(password)
    reviewer.session_epoch += 1
    reviewer.updated_at = now
    row.used_at = now
    session.commit()
    response = RedirectResponse("/review/login?reset=1", status_code=303)
    response.delete_cookie("review_reset")
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
        return login_response(
            request,
            status_code=403,
            error="Your sign-in page expired. Reload the page and try again.",
        )
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
                return login_response(
                    request,
                    status_code=429,
                    error="Too many sign-in attempts. Wait a minute and try again.",
                    username=username,
                )
            queue.append(now.timestamp())
    reviewer = session.scalar(
        select(Reviewer).where(func.lower(Reviewer.username) == username.lower())
    )
    if (
        reviewer is None
        or not reviewer.is_active
        or not verify_password(password, reviewer.password_hash)
    ):
        return login_response(
            request,
            status_code=401,
            error="Invalid username or password.",
            username=username,
        )
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
def dashboard(
    request: Request,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    counts = counts_for(session)
    totals = dict(
        jobs=session.scalar(select(func.count()).select_from(Job)) or 0,
        active_jobs=session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(("queued", "processing", "review_required")))
        )
        or 0,
        documents=session.scalar(select(func.count()).select_from(Document)) or 0,
        completed_jobs=session.scalar(
            select(func.count()).select_from(Job).where(Job.status == "completed")
        )
        or 0,
    )
    status_rows = list(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    recent_jobs = list(
        session.execute(
            select(Job, Client.name)
            .join(Client, Client.id == Job.client_id)
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .limit(6)
        ).all()
    )
    return render(
        request,
        "Dashboard",
        subtitle="Review throughput and extraction activity at a glance.",
        counts=counts,
        totals=totals,
        status_rows=status_rows,
        recent_jobs=recent_jobs,
        recent_activity=resolutions_for(session, 6),
    )


@router.get("/queues")
def queues_index() -> Response:
    return RedirectResponse("/review", status_code=303)


@router.get("/queues/{track}")
@router.get("/queues/{track}/rows")
def queue(
    request: Request,
    track: str,
    cursor: str | None = None,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    if track not in ("extraction", "conflicts"):
        raise HTTPException(404)
    query = select(ReviewItem).where(
        ReviewItem.status.in_(("open", "in_progress")),
        ReviewItem.track == ("conflict" if track == "conflicts" else "extraction"),
    )
    if cursor:
        previous = session.scalar(query.where(ReviewItem.id == cursor))
        if previous is None:
            raise HTTPException(400, "Invalid queue cursor; return to the queue")
        query = query.where(
            (ReviewItem.created_at > previous.created_at)
            | ((ReviewItem.created_at == previous.created_at) & (ReviewItem.id > previous.id))
        )
    items = list(session.scalars(query.order_by(ReviewItem.created_at, ReviewItem.id).limit(101)))
    next_cursor = items[99].id if len(items) > 100 else None
    return render(
        request,
        "Extraction review" if track == "extraction" else "Conflict review",
        template="queue.html",
        subtitle=(
            "Verify extracted values against the source pages."
            if track == "extraction"
            else "Record the client's decision when source documents disagree."
        ),
        items=items[:100],
        track=track,
        counts=counts_for(session),
        next_cursor=next_cursor,
    )


@router.get("/jobs")
def jobs(
    request: Request,
    status: str | None = None,
    client: str | None = None,
    q: str | None = None,
    page: int = 1,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    if status not in (None, *JOB_STATUSES):
        raise HTTPException(404)
    page = max(1, page)
    conditions = []
    if status:
        conditions.append(Job.status == status)
    if client:
        conditions.append(Job.client_id == client)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        conditions.append(
            or_(Job.job_ref.ilike(pattern), Job.id.ilike(pattern), Client.name.ilike(pattern))
        )
    base = select(Job, Client.name).join(Client, Client.id == Job.client_id)
    count_query = select(func.count()).select_from(Job).join(Client, Client.id == Job.client_id)
    if conditions:
        base = base.where(*conditions)
        count_query = count_query.where(*conditions)
    total = session.scalar(count_query) or 0
    rows = list(
        session.execute(
            base.order_by(Job.updated_at.desc(), Job.id.desc())
            .offset((page - 1) * JOBS_PER_PAGE)
            .limit(JOBS_PER_PAGE)
        ).all()
    )
    pages = max(1, (total + JOBS_PER_PAGE - 1) // JOBS_PER_PAGE)
    return render(
        request,
        "Jobs",
        template="jobs.html",
        subtitle="Every extraction job, its progress and its review load.",
        rows=rows,
        total=total,
        page=page,
        pages=pages,
        status=status,
        client=client,
        query=q or "",
        status_counts=dict(
            session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        ),
        clients=list(session.scalars(select(Client).order_by(Client.name))),
        counts=counts_for(session),
    )


INTERNAL_CLIENT_NAME = "Internal Review"
INTERNAL_CLIENT_SLUG = "internal-review"


def _default_client(session: Session) -> Client:
    """Pick the active tenant that receives internal uploads.

    A brand-new local stack has no clients at all. The first upload creates a
    dedicated internal tenant so the console is usable without a CLI step.
    """

    client = session.scalar(
        select(Client).where(Client.status == "active").order_by(Client.name).limit(1)
    )
    if client is not None:
        return client
    if session.scalar(select(Client.id).limit(1)) is not None:
        raise errors.invalid_request("client_id", "There is no active client. Activate one first.")
    client = Client(
        id=new_id("cli_"),
        name=INTERNAL_CLIENT_NAME,
        slug=INTERNAL_CLIENT_SLUG,
        status="active",
    )
    session.add(client)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        client = session.scalar(
            select(Client).where(Client.slug == INTERNAL_CLIENT_SLUG, Client.status == "active")
        )
        if client is None:
            raise
    return client


def _field_rows(document: Document) -> list[dict[str, Any]]:
    """Flatten one retained extraction snapshot for display."""

    extraction = document.extraction or {}
    fields = {
        **(extraction.get("fields") or {}),
        **(extraction.get("extension") or {}),
    }
    rows: list[dict[str, Any]] = []
    for path, field in fields.items():
        if not isinstance(field, dict):
            continue
        provenance = field.get("provenance") or {}
        page_index = provenance.get("page_index")
        rows.append(
            {
                "path": path,
                "value": field.get("value", ""),
                "agreement": field.get("agreement"),
                "page": page_index + 1 if isinstance(page_index, int) else None,
            }
        )
    return rows


@router.get("/jobs/new")
def new_job(
    request: Request,
    client_id: str | None = None,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    if reviewer.role == "auditor":
        raise HTTPException(403, "Read-only account")
    clients = list(
        session.scalars(select(Client).where(Client.status == "active").order_by(Client.name))
    )
    selected = next((row for row in clients if row.id == client_id), None)
    if selected is None and clients:
        selected = clients[0]
    return render(
        request,
        "Create job",
        template="new_job.html",
        subtitle="Upload documents and run the full extraction pipeline.",
        clients=clients,
        clients_unavailable=not clients and session.scalar(select(Client.id).limit(1)) is not None,
        selected_client=selected,
        max_file_mb=max(1, round(settings.max_file_bytes / (1024 * 1024))),
        max_documents=settings.max_documents_per_group,
        counts=counts_for(session),
    )


@router.post("/jobs/new")
async def create_job(
    request: Request,
    client_id: str | None = None,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    storage: ObjectStorage = Depends(get_storage),
    clock: Clock = Depends(get_clock),
) -> JSONResponse:
    idempotency_key = validate_idempotency_key(request.headers.get("Idempotency-Key"), settings)
    client = None
    if client_id:
        client = session.get(Client, client_id)
        if client is None or client.status != "active":
            raise errors.invalid_request("client_id", "The selected client is not active.")
    raw = await spool_submission(request.stream(), request.headers.get("content-type"), settings)
    validated = None
    try:
        validated = await run_in_threadpool(validate_submission, raw, settings)
        if client is None:
            client = await run_in_threadpool(_default_client, session)
        accepted = await run_in_threadpool(
            ingest.accept,
            session,
            client=client,
            validated=validated,
            idempotency_key=idempotency_key,
            storage=storage,
            settings=settings,
            clock=clock,
        )
    finally:
        if validated is not None:
            validated.cleanup()
        else:
            raw.cleanup()

    return JSONResponse(
        status_code=201,
        content={
            "job_id": accepted.job_id,
            "job_url": f"/review/jobs/{accepted.job_id}",
            "result_id": accepted.result_id,
            "submitted_documents": accepted.submitted_documents,
            "duplicate_documents": accepted.duplicate_documents,
        },
    )


@router.get("/jobs/{job_id}")
def job_detail(
    request: Request,
    job_id: str,
    document_id: str | None = None,
    page: int = 0,
    tab: str | None = None,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404)
    client = session.get(Client, job.client_id)
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.job_id == job.id)
            .order_by(Document.created_at, Document.id)
        )
    )
    conflicts = list(
        session.execute(
            select(ConflictRecord, func.count(ConflictSource.id))
            .outerjoin(ConflictSource, ConflictSource.conflict_id == ConflictRecord.id)
            .where(ConflictRecord.job_id == job.id)
            .group_by(ConflictRecord.id)
            .order_by(ConflictRecord.created_at, ConflictRecord.id)
        ).all()
    )
    items = list(
        session.scalars(
            select(ReviewItem)
            .where(ReviewItem.job_id == job.id)
            .order_by(ReviewItem.created_at.desc(), ReviewItem.id.desc())
        )
    )
    submissions = list(
        session.scalars(
            select(Submission)
            .where(Submission.job_id == job.id)
            .order_by(Submission.created_at.desc(), Submission.id.desc())
        )
    )
    selected = next((row for row in documents if row.id == document_id), None)
    if document_id and selected is None:
        raise HTTPException(404, "Document does not belong to this job")
    active_ids = {
        item.document_id
        for item in items
        if item.track == "extraction" and item.status in ("open", "in_progress")
    }
    if selected is None:
        selected = next(
            (row for row in documents if row.id in active_ids), documents[0] if documents else None
        )
    workspace = workspace_data(session, selected) if selected else {}
    line_item_table = workspace.get("line_item_table") or {}
    scalar_fields = workspace.get("scalar_fields") or {}
    active_tab = "fields"
    show_items = tab == "items" or (
        tab is None and not scalar_fields and bool(line_item_table.get("rows"))
    )
    if line_item_table and show_items:
        active_tab = "items"
    if selected:
        page = max(selected.page_start, min(page, selected.page_end))
    document_fields = {document.id: _field_rows(document) for document in documents}
    open_reviews = sum(review_counts(session, job.id))
    pending = any(row.status in ("queued", "processing") for row in submissions)
    result = job_result(session, job.id, job.client_id)
    return render(
        request,
        "Job details",
        template="job.html",
        job=job,
        client=client,
        documents=documents,
        document_fields=document_fields,
        selected=selected,
        selected_page=page,
        active_tab=active_tab,
        **workspace,
        has_fields=any(document_fields.values()),
        processing=job.status in ("queued", "processing")
        or any(document.status in ("queued", "processing") for document in documents),
        conflicts=conflicts,
        items=items,
        submissions=submissions,
        result_json=result,
        ready_to_push=job.status == "completed"
        and not pending
        and not open_reviews
        and not any(row.status in ("queued", "processing") for row in documents)
        and not any(conflict.status == "open" for conflict, _ in conflicts),
        open_reviews=open_reviews,
        counts=counts_for(session),
    )


@router.post("/jobs/{job_id}/done")
def finish_job(
    job_id: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> JSONResponse:
    job = session.scalar(
        select(Job)
        .where(Job.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise HTTPException(404, "Job not found")
    pending = session.scalar(
        select(Submission.id)
        .where(Submission.job_id == job.id, Submission.status.in_(("queued", "processing")))
        .limit(1)
    )
    pending_document = session.scalar(
        select(Document.id)
        .where(Document.job_id == job.id, Document.status.in_(("queued", "processing")))
        .limit(1)
    )
    conflict = session.scalar(
        select(ConflictRecord.id)
        .where(ConflictRecord.job_id == job.id, ConflictRecord.status == "open")
        .limit(1)
    )
    if (
        job.status != "completed"
        or pending
        or pending_document
        or conflict
        or sum(review_counts(session, job.id))
    ):
        raise HTTPException(409, "Finish processing and resolve all reviews and conflicts first.")
    result = job_result(session, job.id, job.client_id)
    session.commit()
    emit_terminal_result(result, phase="review.result")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get("/activity")
def activity(
    request: Request,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    rows = resolutions_for(session, 100)
    outcomes = dict(
        session.execute(
            select(ReviewResolution.outcome, func.count()).group_by(ReviewResolution.outcome)
        ).all()
    )
    return render(
        request,
        "Activity",
        template="activity.html",
        subtitle="Decisions recorded by the review team, newest first.",
        rows=rows,
        outcomes=outcomes,
        counts=counts_for(session),
    )


@router.get("/team")
def team(
    request: Request,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    members = list(
        session.scalars(select(Reviewer).order_by(Reviewer.display_name, Reviewer.username))
    )
    assigned = dict(
        session.execute(
            select(ReviewItem.assigned_to, func.count())
            .where(
                ReviewItem.status.in_(("open", "in_progress")),
                ReviewItem.assigned_to.is_not(None),
            )
            .group_by(ReviewItem.assigned_to)
        ).all()
    )
    return render(
        request,
        "Team",
        template="team.html",
        subtitle="People with access to this review workspace.",
        members=members,
        assigned=assigned,
        counts=counts_for(session),
    )


@router.get("/clients")
def clients(
    request: Request,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    rows = list(
        session.execute(
            select(Client, func.count(Job.id))
            .outerjoin(Job, Job.client_id == Client.id)
            .group_by(Client.id)
            .order_by(Client.name)
        ).all()
    )
    return render(
        request,
        "Clients",
        template="clients.html",
        subtitle="Tenants submitting documents through the API.",
        rows=rows,
        counts=counts_for(session),
    )


def workspace_data(session: Session, row: Document) -> dict[str, Any]:
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
        if field.get("provenance", {}).get("page_index") in transforms
        and field["provenance"].get("bbox")
    }
    return dict(
        document=payload,
        display_fields=display_fields,
        **invoice_line_items(row.document_type, display_fields, payload.get("tables") or {}),
        boxes=boxes,
        labels=history(session, document_id=row.id),
        item=session.scalar(
            select(ReviewItem).where(
                ReviewItem.document_id == row.id,
                ReviewItem.status.in_(("open", "in_progress")),
            )
        ),
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
    workspace = workspace_data(session, row)
    return render(
        request,
        "Extraction review",
        template="document.html",
        subtitle=f"Document {row.id}",
        **workspace,
        job=session.get(Job, row.job_id),
        client=session.get(Client, row.client_id),
        counts=counts_for(session),
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
        template="conflict.html",
        subtitle=f"{evidence['field_label']} · {row.status}",
        conflict=evidence,
        item=item,
        job=session.get(Job, row.job_id),
        client=session.get(Client, row.client_id),
        labels=history(session, conflict_id=row.id),
        counts=counts_for(session),
    )


@router.post("/documents/{document_id}/confirm-remaining")
def confirm_document(
    request: Request,
    document_id: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    try:
        count = confirm_remaining(
            session, document_id=document_id, reviewer=reviewer, now=request.app.state.clock.now()
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"confirmed": count}


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


@router.get("/media/documents/{document_id}/source")
def media_source(
    request: Request,
    document_id: str,
    reviewer: Reviewer = Depends(principal),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404)
    file = session.get(File, document.file_id)
    if document.purged_at or file is None or file.purged_at or not file.storage_key:
        return HTMLResponse(
            "Original document expired. The extraction record remains available.",
            status_code=410,
            headers={"Cache-Control": "no-store"},
        )
    try:
        with request.app.state.storage.get(file.storage_key) as stream:
            data = stream.read()
    except FileNotFoundError:
        raise HTTPException(410, "Original document expired") from None
    return Response(
        data,
        media_type=file.content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(file.filename, safe='')}",
        },
    )
