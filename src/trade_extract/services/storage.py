from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable

from trade_extract.config import Settings


@runtime_checkable
class ObjectStorage(Protocol):
    def put(self, key: str, stream: BinaryIO, content_type: str) -> None: ...

    def get(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def presigned_get(self, key: str, expires_in: int) -> str: ...


def source_key(*, client_id: str, job_id: str, file_id: str, extension: str) -> str:
    return f"clients/{client_id}/jobs/{job_id}/files/{file_id}/source.{extension}"


def _normalise_key(key: str) -> PurePosixPath:
    candidate = PurePosixPath(key.replace("\\", "/"))
    if candidate.is_absolute() or any(part in ("..", "") for part in candidate.parts):
        raise ValueError("storage key escapes the storage root")
    return candidate


class FilesystemStorage:
    """Development adapter: atomic writes under a root and path-escape rejection."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / _normalise_key(key)).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage key escapes the storage root") from exc
        if candidate.is_symlink():
            raise ValueError("storage key resolves to a symlink")
        return candidate

    def put(self, key: str, stream: BinaryIO, content_type: str) -> None:
        del content_type
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        try:
            with os.fdopen(handle, "wb") as temp:
                shutil.copyfileobj(stream, temp, length=1024 * 1024)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def put_path(self, key: str, path: str | Path) -> None:
        with Path(path).open("rb") as handle:
            self.put(key, handle, "application/octet-stream")

    def get(self, key: str) -> BinaryIO:
        return self._path(key).open("rb")

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix.rstrip("/"))
        if not base.exists():
            return 0
        deleted = 0
        if base.is_file():
            base.unlink()
            return 1
        for child in sorted(base.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
                deleted += 1
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)
        return deleted

    def presigned_get(self, key: str, expires_in: int) -> str:
        del expires_in
        return self._path(key).as_uri()


class S3Storage:
    """S3-compatible adapter (R2 or AWS S3)."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        region: str,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region or None,
        )

    def put(self, key: str, stream: BinaryIO, content_type: str) -> None:
        self.client.upload_fileobj(
            stream,
            self.bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "ServerSideEncryption": "AES256",
            },
        )

    def get(self, key: str) -> BinaryIO:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"]

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        deleted = 0
        paginator = self.client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Versions", []):
                self.client.delete_object(
                    Bucket=self.bucket, Key=item["Key"], VersionId=item["VersionId"]
                )
                deleted += 1
            for item in page.get("DeleteMarkers", []):
                self.client.delete_object(
                    Bucket=self.bucket, Key=item["Key"], VersionId=item["VersionId"]
                )
                deleted += 1
        return deleted

    def presigned_get(self, key: str, expires_in: int) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "filesystem":
        return FilesystemStorage(settings.storage_dir)
    return S3Storage(
        bucket=settings.s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        region=settings.s3_region,
    )
