from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from trade_extract.db.models.page import Page


def insert(
    session: Session,
    *,
    page_id: str,
    file_id: str,
    page_index: int,
    width_px: int,
    height_px: int,
    dpi: int,
    rotation_deg: float,
    text: str,
    image_key: str,
    thumbnail_key: str,
    now: datetime,
) -> Page:
    page = Page(
        id=page_id,
        file_id=file_id,
        page_index=page_index,
        width_px=width_px,
        height_px=height_px,
        dpi=dpi,
        rotation_deg=rotation_deg,
        text_layer_present=bool(text.strip()),
        text_layer_chars=len(text),
        text_layer=text,
        conditioning={},
        status="pending",
        image_key=image_key,
        thumbnail_key=thumbnail_key,
        created_at=now,
        updated_at=now,
    )
    session.add(page)
    return page


def list_for_file(session: Session, file_id: str) -> list[Page]:
    stmt = select(Page).where(Page.file_id == file_id).order_by(Page.page_index)
    return list(session.execute(stmt).scalars().all())


def count_for_file(session: Session, file_id: str) -> int:
    stmt = select(func.count()).select_from(Page).where(Page.file_id == file_id)
    return int(session.execute(stmt).scalar_one())


def delete_for_file(session: Session, file_id: str) -> None:
    session.execute(delete(Page).where(Page.file_id == file_id))
