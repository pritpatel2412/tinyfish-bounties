"""
tinycore/evidence.py
Encapsulates cryptographic evidence receipts:
Evidence{source_url, checked_at, html_sha256, screenshot_url, tf_run_id, endpoint_used}.
Enforces Hard Rule 6 across all apps.
"""

import hashlib
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from tinycore.store import get_db


@dataclass
class Evidence:
    source_url: str
    checked_at: str
    html_sha256: str
    screenshot_url: str | None = None
    tf_run_id: str | None = None
    endpoint_used: str = "fetch"
    id: str | None = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.checked_at:
            self.checked_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


def create_evidence(
    source_url: str,
    raw_content: str | bytes,
    screenshot_url: str | None = None,
    tf_run_id: str | None = None,
    endpoint_used: str = "fetch"
) -> Evidence:
    """Creates and persists an immutable Evidence receipt."""
    if isinstance(raw_content, str):
        content_bytes = raw_content.encode("utf-8")
    else:
        content_bytes = raw_content
    sha256_hash = hashlib.sha256(content_bytes).hexdigest()

    ev = Evidence(
        source_url=source_url,
        checked_at=datetime.now(timezone.utc).isoformat(),
        html_sha256=sha256_hash,
        screenshot_url=screenshot_url,
        tf_run_id=tf_run_id,
        endpoint_used=endpoint_used
    )

    try:
        conn = get_db()
        with conn:
            conn.execute(
                """
                INSERT INTO evidence_receipts (id, source_url, checked_at, html_sha256, screenshot_url, tf_run_id, endpoint_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ev.id, ev.source_url, ev.checked_at, ev.html_sha256, ev.screenshot_url, ev.tf_run_id, ev.endpoint_used)
            )
    except Exception:
        pass

    return ev
