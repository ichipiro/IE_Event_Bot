"""run所有イベントだけでDiscord差分取得とNotion更新を検証する。"""

from copy import deepcopy
from urllib.parse import quote

from discord_notion_sync import (
    _apply_discord_event_diff,
    _fingerprint,
    _list_discord_scheduled_events,
    _sync_discord_event_upsert,
    _sync_discord_event_delete,
)
from e2e_discord_notion_probe import (
    _discord_event_is_owned,
    _env_text,
    _new_run_id,
    _notion_page_matches,
    _notion_page_is_owned,
    _target_fingerprints,
    _configuration_error,
    _finish_sync_probe,
    cleanup_discord_notion_sync_probe,
    run_discord_notion_sync_probe,
)
from e2e_discord_delta_state import delta_owner_matches, valid_delta_checkpoint
from e2e_discord_probe import _request_stage as _discord_request_stage
from e2e_notion_probe import (
    _canonical_id,
    _find_page_by_marker,
    _page_archived,
    _request_stage as _notion_request_stage,
)


DISCORD_DELTA_MANIFEST_SERVICE = "discord_delta"
_MANIFEST_KIND = "discord_delta_sync"
_QUEUE_KEY = "sync:discord_notion_queue"
_CANCELED_STATUS = 4


class _DeltaEnv:
    """通常の通知先と状態bindingを差分処理へ渡さない。"""

    EVENT_CREATE_CHANNEL_ID = ""
    EVENT_CREATE_ROLE_ID = ""
    STATE_KV = None
    SYNC_COORDINATOR = None
    DISCORD_NOTION_MAX_CHANGES_PER_RUN = "1"

    def __init__(self, env):
        self._env = env

    def __getattr__(self, name):
        return getattr(self._env, name, None)


class _DeltaState:
    """snapshotとqueueを1資源・1実行へ閉じ込める。"""

    def __init__(self, event_id: str, *, store=None, owner: dict | None = None):
        self.event_id = event_id
        self.store = store
        self.owner = deepcopy(owner or {})
        self.revision: int | None = None
        self.snapshot: dict[str, str] = {}
        self.queue: list[dict] = []
        self.snapshot_writes = 0
        self.queue_writes = 0

    async def restore(self) -> None:
        if self.store is None:
            return
        manifest = await self.store.get_e2e_manifest(DISCORD_DELTA_MANIFEST_SERVICE)
        if not isinstance(manifest, dict) or not delta_owner_matches(manifest, self.owner):
            raise ValueError("discord_delta_state_owner_mismatch")
        checkpoint = manifest.get("delta_checkpoint")
        if checkpoint is not None and not valid_delta_checkpoint(checkpoint, self.event_id):
            raise ValueError("discord_delta_state_invalid_checkpoint")
        revision = checkpoint["revision"] if checkpoint is not None else 0
        if self.revision is not None and self.revision != revision:
            raise ValueError("discord_delta_state_revision_mismatch")
        self.snapshot = deepcopy(checkpoint["snapshot"]) if checkpoint is not None else {}
        self.queue = deepcopy(checkpoint["queue"]) if checkpoint is not None else []
        self.revision = revision

    async def checkpoint(self) -> None:
        if self.store is None:
            return
        if self.revision is None:
            raise ValueError("discord_delta_state_not_restored")
        checkpoint = {
            "revision": self.revision + 1,
            "snapshot": dict(self.snapshot), "queue": deepcopy(self.queue),
        }
        await self.store.put_e2e_delta_checkpoint(self.owner, checkpoint)
        self.revision += 1

    @staticmethod
    def enabled() -> bool:
        return True

    async def get_discord_snapshot(self) -> dict:
        return dict(self.snapshot)

    async def set_discord_snapshot(self, value: dict) -> None:
        if set(value) - {self.event_id}:
            raise ValueError("discord_delta_state_target_mismatch")
        self.snapshot = dict(value)
        self.snapshot_writes += 1

    async def get_json(self, key: str, default=None):
        if key != _QUEUE_KEY:
            raise ValueError("discord_delta_state_key_forbidden")
        return deepcopy(self.queue)

    async def put_json_if_changed(self, key: str, value: list) -> bool:
        if key != _QUEUE_KEY:
            raise ValueError("discord_delta_state_key_forbidden")
        if len(value) > 1 or any(
            op.get("id") != self.event_id or op.get("op") not in ("upsert", "delete")
            for op in value
        ):
            raise ValueError("discord_delta_state_target_mismatch")
        changed = self.queue != value
        self.queue = deepcopy(value)
        self.queue_writes += 1
        return changed


async def _poll_owned_event(
    env, state: _DeltaState, event: dict, run_id: str,
    stages: dict[str, int], phase: str, *, created: int, updated: int,
    page_id: str | None = None,
) -> bool:
    events, error = await _list_discord_scheduled_events(env)
    stages[f"{phase}_list"] = 200 if not error and events is not None else 500
    if error and error.startswith("discord_list_failed:"):
        status_text = error.removeprefix("discord_list_failed:")
        if status_text.isdigit() and 400 <= int(status_text) <= 599:
            stages[f"{phase}_list"] = int(status_text)
    if error or events is None:
        return False
    # 欠落・重複・所有marker変更は削除と推測せず、適用前に止める。
    matches = [item for item in events if str(item.get("id") or "") == state.event_id]
    owned = len(matches) == 1 and _discord_event_is_owned(
        matches[0],
        event_id=state.event_id,
        guild_id=_env_text(env, "DISCORD_GUILD_ID"),
        run_id=run_id,
    )
    if not owned or _fingerprint(matches[0]) != _fingerprint(event):
        stages[f"{phase}_owned"] = 409
        return False
    stages[f"{phase}_owned"] = 200

    return await _apply_owned_diff(
        env, state, matches, page_id, stages, phase,
        created=created, updated=updated, deleted=0,
    )


async def _apply_owned_diff(
    env, state: _DeltaState, events: list[dict], page_id: str | None,
    stages: dict[str, int], phase: str, *, created: int, updated: int, deleted: int,
) -> bool:
    async def apply_owned(env, owned_event, token):
        return await _sync_discord_event_upsert(
            env, owned_event, token, expected_internal_page_id=page_id,
        )

    async def delete_owned(env, event_id, token):
        if event_id != state.event_id or not page_id:
            return False
        return await _sync_discord_event_delete(
            env, event_id, token, expected_internal_page_id=page_id,
        )

    try:
        await state.restore()
    except Exception:
        stages[f"{phase}_checkpoint_read"] = 409
        return False
    stages[f"{phase}_checkpoint_read"] = 200
    result = await _apply_discord_event_diff(
        env, state, events, upsert_runner=apply_owned, delete_runner=delete_owned,
    )
    try:
        await state.checkpoint()
    except Exception:
        stages[f"{phase}_checkpoint_write"] = 409
        return False
    stages[f"{phase}_checkpoint_write"] = 200
    ok = (
        result.get("ok") is True
        and result.get("created") == created
        and result.get("updated") == updated
        and result.get("deleted") == deleted
        and result.get("processed_changes") == created + updated + deleted
        and result.get("pending_changes") == 0
        and result.get("error_count") == 0
        and state.snapshot == {str(item["id"]): _fingerprint(item) for item in events}
        and state.queue == []
    )
    stages[f"{phase}_diff"] = 200 if ok else 500
    return ok


async def _verify_owned_page(
    env, event: dict, page_id: str, run_id: str,
    stages: dict[str, int], retries: dict[str, int], phase: str,
    *, archived: bool = False,
) -> bool:
    database_id = _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    if not archived:
        found_id, error = await _find_page_by_marker(
            env, database_id, "メッセージID", str(event["id"]),
            stages, retries, f"{phase}_find", "notion_page",
        )
        if error or _canonical_id(found_id) != _canonical_id(page_id):
            return False
    status, page = await _notion_request_stage(
        env, stages, retries, f"{phase}_read", "GET",
        f"/pages/{quote(page_id, safe='')}",
    )
    if archived:
        return (
            200 <= status < 300 and isinstance(page, dict) and _page_archived(page)
            and _notion_page_is_owned(
                page, page_id=page_id, database_id=database_id,
                discord_event_id=str(event["id"]), run_id=run_id,
            )
        )
    return (
        200 <= status < 300 and isinstance(page, dict)
        and _notion_page_matches(
            page, page_id=page_id, database_id=database_id,
            discord_event=event, run_id=run_id,
        )
    )


async def _confirm_source_absent(env, event_id, event_path, stages, retries, phase):
    status, _ = await _discord_request_stage(
        env, stages, retries, f"{phase}_source_read", "GET", event_path,
    )
    if status != 404:
        return False
    events, error = await _list_discord_scheduled_events(env)
    absent = (
        not error and events is not None
        and not any(str(item.get("id") or "") == event_id for item in events)
    )
    stages[f"{phase}_list"] = 200 if absent else 500
    return absent


async def _cancel_and_remove(
    env, scoped_env, state, event, page_id, run_id, stages, retries,
) -> bool:
    guild_id = _env_text(env, "DISCORD_GUILD_ID")
    event_id = str(event["id"])
    event_path = (
        f"/guilds/{quote(guild_id, safe='')}/scheduled-events/{quote(event_id, safe='')}"
    )
    # 変更操作の直前にも所有権を再確認する。
    status, current = await _discord_request_stage(
        env, stages, retries, "delta_before_cancel", "GET", event_path,
    )
    if not (200 <= status < 300 and isinstance(current, dict)):
        return False
    if _fingerprint(current) != _fingerprint(event) or not _discord_event_is_owned(
        current, event_id=event_id, guild_id=guild_id, run_id=run_id,
    ):
        return False
    status, canceled = await _discord_request_stage(
        env, stages, retries, "delta_source_cancel", "PATCH", event_path,
        {"status": _CANCELED_STATUS},
    )
    if not (200 <= status < 300 and isinstance(canceled, dict)):
        return False
    expected = {**event, "status": _CANCELED_STATUS}
    if _fingerprint(canceled) != _fingerprint(expected) or not _discord_event_is_owned(
        canceled, event_id=event_id, guild_id=guild_id, run_id=run_id,
    ):
        return False
    events, error = await _list_discord_scheduled_events(scoped_env)
    stages["delta_cancel_list"] = 200 if not error and events is not None else 500
    if error or events is None:
        return False
    matches = [item for item in events if str(item.get("id") or "") == event_id]
    if len(matches) > 1 or (matches and (
        _fingerprint(matches[0]) != _fingerprint(expected)
        or not _discord_event_is_owned(
            matches[0], event_id=event_id, guild_id=guild_id, run_id=run_id,
        )
    )):
        return False
    if not await _verify_owned_page(
        env, event, page_id, run_id, stages, retries, "delta_before_cancel_apply",
    ):
        return False
    listed = bool(matches)
    stages["delta_cancel_listed" if listed else "delta_cancel_missing"] = 200
    if not await _apply_owned_diff(
        scoped_env, state, matches, page_id, stages, "delta_cancel",
        created=0, updated=int(listed), deleted=int(not listed),
    ):
        return False
    if not await _verify_owned_page(
        env, canceled, page_id, run_id, stages, retries, "delta_after_cancel",
        archived=not listed,
    ):
        return False

    status, current = await _discord_request_stage(
        env, stages, retries, "delta_before_delete", "GET", event_path,
    )
    if status != 404:
        if not (200 <= status < 300 and isinstance(current, dict)):
            return False
        if _fingerprint(current) != _fingerprint(expected) or not _discord_event_is_owned(
            current, event_id=event_id, guild_id=guild_id, run_id=run_id,
        ):
            return False
        status, _ = await _discord_request_stage(
            env, stages, retries, "delta_source_delete", "DELETE", event_path,
        )
        if status not in (204, 404):
            return False
    if not await _confirm_source_absent(
        env, event_id, event_path, stages, retries, "delta_delete",
    ):
        return False
    if not await _verify_owned_page(
        env, canceled, page_id, run_id, stages, retries, "delta_before_delete_apply",
        archived=not listed,
    ):
        return False
    if not await _apply_owned_diff(
        scoped_env, state, [], page_id, stages, "delta_delete",
        created=0, updated=0, deleted=int(listed),
    ):
        return False
    # cleanupが後からarchiveして検証失敗を隠す前に、適用結果を読み戻す。
    if not await _verify_owned_page(
        env, canceled, page_id, run_id, stages, retries, "delta_after_delete",
        archived=True,
    ):
        return False
    if not await _confirm_source_absent(
        env, event_id, event_path, stages, retries, "delta_deleted_unchanged",
    ):
        return False
    return await _apply_owned_diff(
        scoped_env, state, [], page_id, stages, "delta_deleted_unchanged",
        created=0, updated=0, deleted=0,
    )


async def _verify_delta_changes(
    env, scoped_state: _DeltaState, event: dict, page_id: str, run_id: str,
    stages: dict, retries: dict, *, expected_writes: int,
) -> bool:
    scoped_env = _DeltaEnv(env)
    if not await _poll_owned_event(
        scoped_env, scoped_state, event, run_id, stages, "delta_unchanged",
        created=0, updated=0, page_id=page_id,
    ):
        return False
    # 更新前にも同一pageの所有権を読み戻し、未知の宛先を作成しない。
    if not await _verify_owned_page(
        env, event, page_id, run_id, stages, retries, "delta_before_update",
    ):
        return False
    event_path = (
        f"/guilds/{quote(_env_text(env, 'DISCORD_GUILD_ID'), safe='')}/"
        f"scheduled-events/{quote(str(event['id']), safe='')}"
    )
    description = str(event["description"]) + "\nE2E delta update"
    status, _ = await _discord_request_stage(
        env, stages, retries, "delta_source_update", "PATCH", event_path,
        {"description": description},
    )
    if not 200 <= status < 300:
        return False
    status, updated_event = await _discord_request_stage(
        env, stages, retries, "delta_source_read", "GET", event_path,
    )
    if not (200 <= status < 300 and isinstance(updated_event, dict)):
        return False
    if updated_event.get("description") != description:
        return False
    if not await _poll_owned_event(
        scoped_env, scoped_state, updated_event, run_id, stages, "delta_update",
        created=0, updated=1, page_id=page_id,
    ):
        return False
    if not await _verify_owned_page(
        env, updated_event, page_id, run_id, stages, retries, "delta_after_update",
    ):
        return False
    if not await _cancel_and_remove(
        env, scoped_env, scoped_state, updated_event, page_id, run_id, stages, retries,
    ):
        return False
    isolated = scoped_state.snapshot_writes == expected_writes and scoped_state.queue_writes == expected_writes
    stages["delta_state_isolated"] = 200 if isolated else 500
    await scoped_state.restore()
    persisted = scoped_state.revision == 6 and scoped_state.snapshot == {} and scoped_state.queue == []
    stages["delta_state_persisted"] = 200 if persisted else 500
    return isolated and persisted


async def run_discord_delta_probe(
    env, state, run_id: str | None = None, *, prepare_only: bool = False,
) -> dict:
    """所有1件の作成・無変更・更新・キャンセル・削除と資源回収を確認する。"""
    run_id = str(run_id or "").strip() or _new_run_id()
    scoped_env = _DeltaEnv(env)
    scoped_state = None

    async def apply(event, stages, retries):
        nonlocal scoped_state
        scoped_state = _DeltaState(str(event["id"]), store=state, owner={
            "run_id": run_id, "discord_event_id": str(event["id"]),
            "target_fingerprints": _target_fingerprints(
                _env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID"),
            ),
        })
        return await _poll_owned_event(
            scoped_env, scoped_state, event, run_id, stages, "delta_create",
            created=1, updated=0,
        )

    async def verify(event, page_id, stages, retries):
        if scoped_state is None:
            return False
        return await _verify_delta_changes(
            env, scoped_state, event, page_id, run_id, stages, retries, expected_writes=6,
        )

    return await run_discord_notion_sync_probe(
        env, state, run_id,
        manifest_service=DISCORD_DELTA_MANIFEST_SERVICE,
        manifest_kind=_MANIFEST_KIND,
        apply_runner=apply,
        verification_runner=verify,
        pause_after_apply=prepare_only,
    )


async def resume_discord_delta_probe(env, state, run_id: str) -> dict:
    """準備完了境界から一度だけ続行し、同じrunの所有資源を回収する。"""
    failure = {"ok": False, "dirty": True, "cleanup_required": True}
    configuration_error = _configuration_error(env)
    if configuration_error:
        return {**failure, "error": configuration_error}
    if not state.e2e_manifest_enabled():
        return {**failure, "error": "sync_coordinator_required"}
    manifest = await state.get_e2e_manifest(DISCORD_DELTA_MANIFEST_SERVICE)
    if not isinstance(manifest, dict):
        return {**failure, "error": "delta_resume_not_prepared"}
    fingerprints = _target_fingerprints(
        _env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID"),
    )
    if manifest.get("dirty") is False:
        targets = manifest.get("resource_fingerprints") or {}
        if (
            manifest.get("kind") == _MANIFEST_KIND and manifest.get("last_run_id") == run_id
            and manifest.get("outcome") == "passed"
            and all(targets.get(key) == value for key, value in fingerprints.items())
        ):
            return {"ok": True, "dirty": False, "run_id": run_id, "status": "already_completed"}
        return {**failure, "dirty": False, "cleanup_required": False, "error": "delta_resume_not_prepared"}
    event_id = str(manifest.get("discord_event_id") or "")
    page_id = str(manifest.get("notion_page_id") or "")
    owner = {
        "run_id": run_id, "discord_event_id": event_id,
        "notion_page_id": page_id, "target_fingerprints": fingerprints,
    }
    if not delta_owner_matches(manifest, owner):
        return {**failure, "error": "delta_resume_owner_mismatch"}
    checkpoint = manifest.get("delta_checkpoint")
    if (
        manifest.get("stage") != "delta_prepared" or not page_id
        or not isinstance(checkpoint, dict) or not valid_delta_checkpoint(checkpoint, event_id)
        or checkpoint["revision"] != 1 or checkpoint["queue"] != []
        or set(checkpoint["snapshot"]) != {event_id}
    ):
        return {**failure, "error": "delta_resume_not_prepared"}

    stages = dict(manifest.get("stages") or {})
    retries = dict(manifest.get("rate_limit_retries") or {})
    event_path = (
        f"/guilds/{quote(_env_text(env, 'DISCORD_GUILD_ID'), safe='')}/"
        f"scheduled-events/{quote(event_id, safe='')}"
    )
    status, event = await _discord_request_stage(
        env, stages, retries, "delta_resume_source_read", "GET", event_path,
    )
    if not (
        status == 200 and isinstance(event, dict)
        and _discord_event_is_owned(event, event_id=event_id,
                                    guild_id=_env_text(env, "DISCORD_GUILD_ID"), run_id=run_id)
        and _fingerprint(event) == checkpoint["snapshot"][event_id]
        and await _verify_owned_page(env, event, page_id, run_id, stages, retries, "delta_resume_page")
    ):
        return {**failure, "error": "delta_resume_resource_mismatch", "stages": stages}
    if not await state.claim_e2e_delta_resume(owner):
        return {**failure, "error": "delta_resume_conflict"}
    manifest["stage"] = "delta_resuming"
    scoped_state = _DeltaState(event_id, store=state, owner=owner)
    try:
        verified = await _verify_delta_changes(
            env, scoped_state, event, page_id, run_id, stages, retries, expected_writes=5,
        )
    except Exception:
        verified = False
    stages["scenario_verification"] = 200 if verified else 500
    stages["delta_http_resume"] = 200 if verified else 500
    return await _finish_sync_probe(
        env, state, manifest, stages, retries,
        "" if verified else "discord_scenario_verification_failed",
        manifest_service=DISCORD_DELTA_MANIFEST_SERVICE, manifest_kind=_MANIFEST_KIND,
    )


async def cleanup_discord_delta_probe(env, state, expected_run_id=None) -> dict:
    """同じrun ID・対象fingerprintの外部資源だけを再回収する。"""
    return await cleanup_discord_notion_sync_probe(
        env, state, expected_run_id,
        manifest_service=DISCORD_DELTA_MANIFEST_SERVICE,
        manifest_kind=_MANIFEST_KIND,
    )
