"""Supabase Storage client (private `receipts` bucket) via the storage REST API.

Objects are keyed `{business_id}/{uuid}.{ext}` so access is business-scoped by
path. The bucket is private: `receipts.image_url` stores the bucket path, never
a public URL — the dashboard fetches short-lived signed URLs on demand.

Dry-run behavior mirrors the WhatsApp client: with placeholder credentials the
upload is skipped and a deterministic pseudo-path is returned, keeping the rest
of the pipeline exercisable locally.
"""
import logging
import uuid

import httpx

from app.core.config import get_settings, is_placeholder

logger = logging.getLogger("storage")

_EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}


def _dry_run() -> bool:
    return is_placeholder(get_settings().supabase_service_role_key)


async def upload_receipt_image(business_id: uuid.UUID, data: bytes, mime_type: str) -> str:
    """Returns the bucket-relative object path stored in receipts.image_url."""
    settings = get_settings()
    ext = _EXT_BY_MIME.get(mime_type, "bin")
    object_path = f"{business_id}/{uuid.uuid4()}.{ext}"

    if _dry_run():
        logger.info("[DRY-RUN storage] would upload %d bytes to %s", len(data), object_path)
        return object_path

    url = (
        f"{settings.supabase_url}/storage/v1/object/"
        f"{settings.supabase_receipts_bucket}/{object_path}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            content=data,
            headers={
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "Content-Type": mime_type,
                "x-upsert": "false",
            },
        )
        resp.raise_for_status()
    return object_path


async def create_signed_url(object_path: str, expires_in: int = 600) -> str | None:
    """Short-lived signed URL for dashboard display. None in dry-run."""
    settings = get_settings()
    if _dry_run():
        return None
    url = (
        f"{settings.supabase_url}/storage/v1/object/sign/"
        f"{settings.supabase_receipts_bucket}/{object_path}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            url,
            json={"expiresIn": expires_in},
            headers={"Authorization": f"Bearer {settings.supabase_service_role_key}"},
        )
        if resp.status_code >= 400:
            logger.error("Signing failed for %s: %s", object_path, resp.text[:300])
            return None
        signed = resp.json().get("signedURL", "")
    return f"{settings.supabase_url}/storage/v1{signed}" if signed else None
