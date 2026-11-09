"""DiscordからGoogleへ書いた内容を再取得した場合だけ識別する。"""

import hashlib
import json
from datetime import datetime, timezone

DISCORD_CONTENT_HASH = "ie_discord_content_hash"


def _event_time(part: dict) -> str:
    value = str(part.get("dateTime") or "")
    if not value:
        return str(part.get("date") or "")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is not None:
            return instant.astimezone(timezone.utc).isoformat()
    except ValueError:
        # 不正な日時の判定は通常適用側へ任せ、指紋では元の表記を保持する。
        return value
    return value


def google_content_hash(event: dict) -> str:
    """表示内容とUTCへ正規化した日時から安定した指紋を作る。"""
    content = {
        key: str(event.get(key) or "")
        for key in ("summary", "description", "location")
    }
    content["start"] = _event_time(event.get("start") or {})
    content["end"] = _event_time(event.get("end") or {})
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
