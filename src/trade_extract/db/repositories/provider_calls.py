"""Each attempt is committed independently of extraction and worker transactions."""

from sqlalchemy.orm import Session, sessionmaker

from trade_extract.db.models.provider_call import ProviderCall
from trade_extract.domain.ids import new_id
from trade_extract.domain.inference import ProviderAttempt


class ProviderCallRecorder:
    def __init__(
        self,
        factory: sessionmaker[Session],
        *,
        provider: str,
        reader: str,
        operation: str = "extract",
        job_id: str | None = None,
        document_id: str | None = None,
        page_id: str | None = None,
    ) -> None:
        self.factory = factory
        self.metadata = dict(
            provider=provider,
            reader=reader,
            operation=operation,
            job_id=job_id,
            document_id=document_id,
            page_id=page_id,
        )

    def __call__(self, attempt: ProviderAttempt) -> None:
        status = (
            "ok"
            if attempt.status == "succeeded"
            else (
                "rate_limited"
                if attempt.error_code == "http_429"
                else "timeout"
                if attempt.error_code == "provider_timeout"
                else "error"
            )
        )
        with self.factory() as session, session.begin():
            session.add(
                ProviderCall(
                    id=new_id("pcl_"),
                    **{**self.metadata, "reader": attempt.reader},
                    segment_id=attempt.segment_id,
                    model=attempt.model,
                    prompt_hash=attempt.prompt_hash,
                    attempt=attempt.attempt,
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    cached_tokens=attempt.cached_tokens,
                    latency_ms=attempt.latency_ms,
                    status=status,
                    error_code=attempt.error_code,
                )
            )
