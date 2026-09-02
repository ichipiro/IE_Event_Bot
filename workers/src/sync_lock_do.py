import json
import re
import time
from uuid import uuid4

from workers import DurableObject, Response


_E2E_MANIFEST_KINDS = {
    "discord_google": "discord_google_sync",
    "discord_notion": "discord_notion_sync",
    "google": "google_calendar_event",
    "google_discord": "google_discord_sync",
    "google_notion": "google_notion_sync",
    "notion_cleanup": "notion_cleanup_job",
    "qa_notification": "qa_notification_job",
    "reminder": "day_before_reminder",
    "webhook_dispatch": "google_webhook_simulation",
    "webhook_delivery": "google_webhook_delivery",
    "webhook_change": "google_webhook_change_dispatch",
    "discord": "discord_event_message",
    "notion": "notion_pages",
}
_E2E_MANIFEST_MAX_BYTES = 32_768
_E2E_RUN_ID_PATTERN = re.compile(r"^E2E-\d{8}T\d{6}Z-[0-9a-f]{8}$")


def _decode_lock_record(value) -> dict:
    """
    storage.get("lock") の返り値を lock 辞書へ正規化する。
    - 新形式: JSON文字列
    - 旧形式: dict（互換）
    """
    # 文字列ならJSONとして読む
    if isinstance(value, str):
        try:
            data = json.loads(value or "{}")
        except Exception:
            data = {}
    # 辞書ならそのまま使う
    elif isinstance(value, dict):
        data = value
    else:
        data = {}
    if not isinstance(data, dict):
        return {}
    return {
        "owner": str(data.get("owner") or ""),
        "expires_at": float(data.get("expires_at") or 0),
    }


def _decode_json_record(value) -> dict:
    """storage 上の JSON 文字列/辞書を dict に正規化する。"""
    if isinstance(value, str):
        try:
            data = json.loads(value or "{}")
        except Exception:
            data = {}
    elif isinstance(value, dict):
        data = value
    else:
        data = {}
    return data if isinstance(data, dict) else {}


class SyncCoordinator(DurableObject):
    """
    同期処理まわりの高頻度状態を扱う Durable Object。
    - acquire: ロック要求（TTL付き）
    - release: ロック解放
    - status: 現在ロック状態の参照
    - get/set_sync_last_epoch: 同期成功時刻
    - mark_google_message_seen: Google webhook 重複抑止
    - clear_e2e_google_message_seen: 所有run付きE2E重複状態の削除
    - attach/record_e2e_webhook_delivery: Google実通知とwatch応答の競合解決
    - *_e2e_webhook_change: Google変更通知の所有確認と単一dispatch claim
    - get/put_e2e_manifest: E2E cleanup 所有権の強整合な保持
    """

    async def sync_state(self, payload_json: str) -> str:
        """RPCで同期状態を操作し、FFI境界をJSON文字列だけで往復する。"""
        try:
            payload = json.loads(str(payload_json or "{}"))
        except Exception:
            payload = {}
        data, _ = await self._handle_action(payload)
        return json.dumps(data, ensure_ascii=False)

    async def fetch(self, request):
        """従来のHTTP形式による内部呼び出しとの互換性を維持する。"""
        try:
            payload = json.loads(await request.text() or "{}")
        except Exception:
            payload = {}
        data, status = await self._handle_action(payload)
        return Response(
            json.dumps(data, ensure_ascii=False),
            status=status,
            headers={"content-type": "application/json"},
        )

    async def _handle_action(self, payload: dict) -> tuple[dict, int]:
        """同期状態のactionを実行し、本文とHTTP互換statusを返す。"""

        # action と現在時刻を取得
        action = str(payload.get("action") or "").strip().lower()
        now = time.time()

        # ロック要求処理
        if action == "acquire":
            # 既存ロックが有効かつ他owner保有中なら 409 で拒否。
            owner = str(payload.get("owner") or f"owner-{uuid4()}")
            # ロックの有効秒数を決める
            ttl_seconds = float(payload.get("ttl_seconds") or 30)
            expires_at = now + max(1.0, ttl_seconds)
            # 現在のロック情報を読む(Durable Object のストレージから取得)
            current = _decode_lock_record(await self.ctx.storage.get("lock"))
            if current:
                current_owner = str(current.get("owner") or "")
                current_exp = float(current.get("expires_at") or 0)
                """
                ロック拒否条件(他人がロック中):
                - まだ期限切れしていない
                - owner が存在する
                - 今の要求者とは別 owner
                """
                if current_exp > now and current_owner and current_owner != owner:
                    return {"ok": False, "locked": True, "owner": current_owner}, 409
            # 他人の有効ロックが無ければ、自分のロック情報を書き込む
            # Python Workers の DO storage は dict 直putで DataCloneError になる場合があるため文字列化して保存
            await self.ctx.storage.put(
                "lock",
                json.dumps({"owner": owner, "expires_at": expires_at}, ensure_ascii=False),
            )
            return {"ok": True, "owner": owner, "expires_at": expires_at}, 200

        # ロック解放処理
        if action == "release":
            # owner 未指定なら強制解放、owner 指定時は一致する(自分のロック)場合のみ解放。
            owner = str(payload.get("owner") or "")
            current = _decode_lock_record(await self.ctx.storage.get("lock"))
            if current:
                current_owner = str(current.get("owner") or "")
                if not owner or owner == current_owner:
                    await self.ctx.storage.delete("lock")
            return {"ok": True}, 200

        if action == "status":
            # 監視用途。現在 lock と現在時刻(now)を返す。
            current = _decode_lock_record(await self.ctx.storage.get("lock"))
            sync_state = _decode_json_record(await self.ctx.storage.get("sync:last_epoch"))
            current["now"] = now
            return {
                "ok": True,
                "lock": current,
                "sync_last_epoch": float(sync_state.get("last_epoch") or 0.0),
            }, 200

        # 同期成功時刻の取得 (CD用)
        if action == "get_sync_last_epoch":
            state = _decode_json_record(await self.ctx.storage.get("sync:last_epoch"))
            return {"ok": True, "last_epoch": float(state.get("last_epoch") or 0.0)}, 200

        # 同期成功時刻の更新 (CD用)
        if action == "set_sync_last_epoch":
            last_epoch = float(payload.get("last_epoch") or now)
            await self.ctx.storage.put(
                "sync:last_epoch",
                json.dumps({"last_epoch": last_epoch}, ensure_ascii=False),
            )
            return {"ok": True, "last_epoch": last_epoch}, 200

        if action == "mark_google_message_seen":
            channel_id = str(payload.get("channel_id") or "").strip()
            message_number = str(payload.get("message_number") or "").strip()
            if not channel_id or not message_number:
                return {"ok": True, "duplicate": False, "skipped": True}, 200
            owner_run_id = str(payload.get("owner_run_id") or "").strip()
            if owner_run_id and not _E2E_RUN_ID_PATTERN.fullmatch(owner_run_id):
                return {"ok": False, "error": "invalid_e2e_owner_run_id"}, 400
            ttl_seconds = max(60.0, float(payload.get("ttl_seconds") or 86400))
            storage_key = f"gcal_msg:{channel_id}:{message_number}"
            current = _decode_json_record(await self.ctx.storage.get(storage_key))
            expires_at = float(current.get("expires_at") or 0.0)
            if expires_at > now:
                current_owner = str(current.get("owner_run_id") or "")
                if owner_run_id and current_owner != owner_run_id:
                    return {
                        "ok": False,
                        "error": "google_message_owner_mismatch",
                    }, 409
                return {"ok": True, "duplicate": True, "expires_at": expires_at}, 200
            next_expires_at = now + ttl_seconds
            record: dict[str, float | str] = {"expires_at": next_expires_at}
            if owner_run_id:
                record["owner_run_id"] = owner_run_id
            await self.ctx.storage.put(
                storage_key,
                json.dumps(record, ensure_ascii=False),
            )
            return {"ok": True, "duplicate": False, "expires_at": next_expires_at}, 200

        if action == "clear_e2e_google_message_seen":
            channel_id = str(payload.get("channel_id") or "").strip()
            message_number = str(payload.get("message_number") or "").strip()
            owner_run_id = str(payload.get("owner_run_id") or "").strip()
            if (
                not channel_id
                or not message_number
                or not _E2E_RUN_ID_PATTERN.fullmatch(owner_run_id)
            ):
                return {"ok": False, "error": "invalid_e2e_google_message_marker"}, 400

            storage_key = f"gcal_msg:{channel_id}:{message_number}"
            current = _decode_json_record(await self.ctx.storage.get(storage_key))
            if not current:
                return {"ok": True, "cleared": False}, 200
            if str(current.get("owner_run_id") or "") != owner_run_id:
                return {"ok": False, "error": "google_message_owner_mismatch"}, 409
            await self.ctx.storage.delete(storage_key)
            return {"ok": True, "cleared": True}, 200

        if action in (
            "attach_e2e_webhook_watch",
            "record_e2e_webhook_delivery",
        ):
            storage_key = "e2e:manifest:webhook_delivery"
            manifest = _decode_json_record(await self.ctx.storage.get(storage_key))
            if (
                manifest.get("kind") != "google_webhook_delivery"
                or manifest.get("dirty") is not True
                or not _E2E_RUN_ID_PATTERN.fullmatch(
                    str(manifest.get("run_id") or "")
                )
            ):
                return {
                    "ok": False,
                    "error": "inactive_e2e_webhook_delivery",
                }, 404

            channel_id = str(payload.get("channel_id") or "").strip()
            owned_channel_id = str(manifest.get("channel_id") or "").strip()
            if (
                not channel_id
                or len(channel_id) > 64
                or channel_id != owned_channel_id
            ):
                return {
                    "ok": False,
                    "error": "e2e_webhook_delivery_target_mismatch",
                }, 404

            resource_id = str(payload.get("resource_id") or "").strip()
            if not resource_id or len(resource_id) > 512:
                return {
                    "ok": False,
                    "error": "invalid_e2e_webhook_resource_id",
                }, 400

            known_resource_id = str(manifest.get("resource_id") or "").strip()
            notification = manifest.get("notification")
            if not isinstance(notification, dict):
                notification = {}
            notified_resource_id = str(notification.get("resource_id") or "").strip()
            if (
                (known_resource_id and known_resource_id != resource_id)
                or (notified_resource_id and notified_resource_id != resource_id)
            ):
                return {
                    "ok": False,
                    "error": "e2e_webhook_delivery_resource_mismatch",
                }, 409

            stages = manifest.get("stages")
            if not isinstance(stages, dict):
                stages = {}
                manifest["stages"] = stages

            if action == "attach_e2e_webhook_watch":
                run_id = str(payload.get("run_id") or "").strip()
                expiration = str(payload.get("expiration") or "").strip()
                watch_status = payload.get("watch_status")
                if (
                    run_id != str(manifest.get("run_id") or "")
                    or not _E2E_RUN_ID_PATTERN.fullmatch(run_id)
                    or len(expiration) > 64
                    or type(watch_status) is not int
                    or not 200 <= watch_status < 300
                ):
                    return {
                        "ok": False,
                        "error": "invalid_e2e_webhook_watch_attachment",
                    }, 400
                manifest["resource_id"] = resource_id
                if expiration:
                    manifest["expiration"] = expiration
                stages["watch_create"] = watch_status
                manifest["stage"] = (
                    "notification_received" if notification else "watch_attached"
                )
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {
                    "ok": True,
                    "notification_received": bool(notification),
                }, 200

            resource_state = str(payload.get("resource_state") or "").strip()
            message_number = str(payload.get("message_number") or "").strip()
            if resource_state != "sync" or message_number != "1":
                return {
                    "ok": False,
                    "error": "e2e_webhook_delivery_notification_mismatch",
                }, 404
            if notification:
                return {"ok": True, "accepted": True, "duplicate": True}, 200

            manifest["resource_id"] = resource_id
            manifest["notification"] = {
                "resource_id": resource_id,
                "resource_state": resource_state,
                "message_number": message_number,
                "received_at_epoch": now,
            }
            stages["webhook_sync_delivery"] = 204
            manifest["stage"] = "notification_received"
            await self.ctx.storage.put(
                storage_key,
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            return {"ok": True, "accepted": True, "duplicate": False}, 200

        if action in (
            "attach_e2e_webhook_change_watch",
            "record_e2e_webhook_change_sync",
            "prepare_e2e_webhook_change",
            "record_e2e_webhook_change_update",
            "claim_e2e_webhook_change",
            "complete_e2e_webhook_change",
        ):
            storage_key = "e2e:manifest:webhook_change"
            manifest = _decode_json_record(await self.ctx.storage.get(storage_key))
            run_id = str(manifest.get("run_id") or "").strip()
            watch = manifest.get("watch")
            if (
                manifest.get("kind") != "google_webhook_change_dispatch"
                or manifest.get("dirty") is not True
                or not _E2E_RUN_ID_PATTERN.fullmatch(run_id)
                or not isinstance(watch, dict)
            ):
                return {
                    "ok": False,
                    "error": "inactive_e2e_webhook_change",
                }, 404

            stages = manifest.get("stages")
            if not isinstance(stages, dict):
                stages = {}
                manifest["stages"] = stages

            if action == "prepare_e2e_webhook_change":
                requested_run_id = str(payload.get("run_id") or "").strip()
                updated_min = str(payload.get("updated_min") or "").strip()
                if (
                    requested_run_id != run_id
                    or len(updated_min) > 64
                    or not updated_min
                    or not str(watch.get("resource_id") or "").strip()
                    or not isinstance(manifest.get("sync_notification"), dict)
                    or isinstance(manifest.get("change_notification"), dict)
                ):
                    return {
                        "ok": False,
                        "error": "invalid_e2e_webhook_change_preparation",
                    }, 409
                manifest["updated_min"] = updated_min
                manifest["update_started"] = True
                manifest["stage"] = "google_update_started"
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {"ok": True}, 200

            if action == "record_e2e_webhook_change_update":
                requested_run_id = str(payload.get("run_id") or "").strip()
                update_status = payload.get("update_status")
                if (
                    requested_run_id != run_id
                    or manifest.get("update_started") is not True
                    or type(update_status) is not int
                    or not 0 <= update_status < 600
                ):
                    return {
                        "ok": False,
                        "error": "invalid_e2e_webhook_change_update",
                    }, 400
                stages["google_update"] = update_status
                manifest["stage"] = (
                    "dispatch_completed"
                    if isinstance(manifest.get("dispatch"), dict)
                    else "google_updated"
                )
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {"ok": True}, 200

            channel_id = str(payload.get("channel_id") or "").strip()
            owned_channel_id = str(watch.get("channel_id") or "").strip()
            if (
                not channel_id
                or len(channel_id) > 64
                or channel_id != owned_channel_id
            ):
                return {
                    "ok": False,
                    "error": "e2e_webhook_change_target_mismatch",
                }, 404

            resource_id = str(payload.get("resource_id") or "").strip()
            if not resource_id or len(resource_id) > 512:
                return {
                    "ok": False,
                    "error": "invalid_e2e_webhook_change_resource_id",
                }, 400
            known_resource_id = str(watch.get("resource_id") or "").strip()
            sync_notification = manifest.get("sync_notification")
            sync_resource_id = (
                str(sync_notification.get("resource_id") or "").strip()
                if isinstance(sync_notification, dict)
                else ""
            )
            if (
                (known_resource_id and known_resource_id != resource_id)
                or (sync_resource_id and sync_resource_id != resource_id)
            ):
                return {
                    "ok": False,
                    "error": "e2e_webhook_change_resource_mismatch",
                }, 409

            if action == "attach_e2e_webhook_change_watch":
                requested_run_id = str(payload.get("run_id") or "").strip()
                expiration = str(payload.get("expiration") or "").strip()
                watch_status = payload.get("watch_status")
                if (
                    requested_run_id != run_id
                    or len(expiration) > 64
                    or type(watch_status) is not int
                    or not 200 <= watch_status < 300
                ):
                    return {
                        "ok": False,
                        "error": "invalid_e2e_webhook_change_watch_attachment",
                    }, 400
                watch["resource_id"] = resource_id
                if expiration:
                    watch["expiration"] = expiration
                stages["watch_create"] = watch_status
                manifest["stage"] = (
                    "sync_notification_received"
                    if isinstance(sync_notification, dict)
                    else "watch_attached"
                )
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {
                    "ok": True,
                    "notification_received": isinstance(sync_notification, dict),
                }, 200

            resource_state = str(payload.get("resource_state") or "").strip()
            message_number = str(payload.get("message_number") or "").strip()
            if action == "record_e2e_webhook_change_sync":
                if resource_state != "sync" or message_number != "1":
                    return {
                        "ok": False,
                        "error": "e2e_webhook_change_sync_mismatch",
                    }, 404
                if isinstance(sync_notification, dict):
                    return {"ok": True, "accepted": True, "duplicate": True}, 200
                watch["resource_id"] = resource_id
                manifest["sync_notification"] = {
                    "resource_id": resource_id,
                    "message_number": message_number,
                    "received_at_epoch": now,
                }
                stages["webhook_sync_delivery"] = 204
                manifest["stage"] = "sync_notification_received"
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {"ok": True, "accepted": True, "duplicate": False}, 200

            if action == "claim_e2e_webhook_change":
                try:
                    parsed_message_number = int(message_number)
                except Exception:
                    parsed_message_number = 0
                if (
                    resource_state != "exists"
                    or parsed_message_number <= 1
                    or len(message_number) > 32
                    or known_resource_id != resource_id
                    or not isinstance(sync_notification, dict)
                    or manifest.get("update_started") is not True
                    or not str(manifest.get("updated_min") or "").strip()
                ):
                    return {
                        "ok": False,
                        "error": "e2e_webhook_change_notification_mismatch",
                    }, 404
                notification = manifest.get("change_notification")
                if isinstance(notification, dict):
                    return {"ok": True, "accepted": True, "duplicate": True}, 200
                manifest["change_notification"] = {
                    "resource_id": resource_id,
                    "message_number": message_number,
                    "received_at_epoch": now,
                }
                attempted = manifest.get("create_attempted")
                if not isinstance(attempted, dict):
                    attempted = {}
                    manifest["create_attempted"] = attempted
                attempted["webhook_dedupe"] = True
                stages["webhook_exists_claim"] = 200
                manifest["stage"] = "change_notification_claimed"
                await self.ctx.storage.put(
                    storage_key,
                    json.dumps(
                        manifest,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                return {"ok": True, "accepted": True, "duplicate": False}, 200

            requested_run_id = str(payload.get("run_id") or "").strip()
            notification = manifest.get("change_notification")
            if (
                requested_run_id != run_id
                or not isinstance(notification, dict)
                or str(notification.get("resource_id") or "") != resource_id
                or str(notification.get("message_number") or "") != message_number
                or isinstance(manifest.get("dispatch"), dict)
            ):
                return {
                    "ok": False,
                    "error": "invalid_e2e_webhook_change_completion",
                }, 409

            dispatch_status = payload.get("dispatch_status")
            selected_count = payload.get("selected_count")
            dedupe_calls = payload.get("dedupe_calls")
            processed = payload.get("processed")
            pending_events = payload.get("pending_events")
            error_count = payload.get("error_count")
            bool_fields = (
                "apply_ok",
                "notion_write_started",
                "cursor_written",
                "last_epoch_written",
                "last_result_written",
            )
            if (
                type(dispatch_status) is not int
                or not 100 <= dispatch_status < 600
                or any(
                    type(value) is not int or value < 0
                    for value in (
                        selected_count,
                        dedupe_calls,
                        processed,
                        pending_events,
                        error_count,
                    )
                )
                or any(type(payload.get(field)) is not bool for field in bool_fields)
            ):
                return {
                    "ok": False,
                    "error": "invalid_e2e_webhook_change_completion",
                }, 400
            dispatch = {
                "status": dispatch_status,
                "selected_count": selected_count,
                "dedupe_calls": dedupe_calls,
                "processed": processed,
                "pending_events": pending_events,
                "error_count": error_count,
                **{field: payload[field] for field in bool_fields},
            }
            manifest["dispatch"] = dispatch
            stages.update(
                {
                    "webhook_exists_delivery": dispatch_status,
                    "webhook_message_dedupe": 200 if dedupe_calls == 1 else 500,
                    "webhook_delta_fetch": 200 if selected_count == 1 else 500,
                    "webhook_cursor_isolated": 200
                    if payload["cursor_written"]
                    else 500,
                    "webhook_last_epoch_isolated": 200
                    if payload["last_epoch_written"]
                    else 500,
                    "webhook_last_result_isolated": 200
                    if payload["last_result_written"]
                    else 500,
                }
            )
            manifest["stage"] = "dispatch_completed"
            await self.ctx.storage.put(
                storage_key,
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            return {"ok": True}, 200

        if action in ("get_e2e_manifest", "put_e2e_manifest"):
            service = str(payload.get("service") or "").strip().lower()
            expected_kind = _E2E_MANIFEST_KINDS.get(service)
            if expected_kind is None:
                return {"ok": False, "error": "invalid_e2e_manifest_service"}, 400
            storage_key = f"e2e:manifest:{service}"

            if action == "get_e2e_manifest":
                raw_manifest = await self.ctx.storage.get(storage_key)
                if raw_manifest is None:
                    return {"ok": True, "manifest": None}, 200
                manifest = _decode_json_record(raw_manifest)
                if not manifest:
                    return {"ok": False, "error": "invalid_e2e_manifest_record"}, 500
                return {"ok": True, "manifest": manifest}, 200

            manifest_json = payload.get("manifest_json")
            if not isinstance(manifest_json, str):
                return {"ok": False, "error": "invalid_e2e_manifest_payload"}, 400
            if len(manifest_json.encode("utf-8")) > _E2E_MANIFEST_MAX_BYTES:
                return {"ok": False, "error": "e2e_manifest_too_large"}, 413
            try:
                manifest = json.loads(manifest_json)
            except Exception:
                manifest = None
            if not isinstance(manifest, dict):
                return {"ok": False, "error": "invalid_e2e_manifest_payload"}, 400
            if manifest.get("kind") != expected_kind:
                return {"ok": False, "error": "invalid_e2e_manifest_kind"}, 400
            if type(manifest.get("version")) is not int or manifest.get("version") != 1:
                return {"ok": False, "error": "invalid_e2e_manifest_version"}, 400
            if type(manifest.get("dirty")) is not bool:
                return {"ok": False, "error": "invalid_e2e_manifest_dirty"}, 400
            run_id_key = "run_id" if manifest["dirty"] else "last_run_id"
            run_id = str(manifest.get(run_id_key) or "")
            if not _E2E_RUN_ID_PATTERN.fullmatch(run_id):
                return {"ok": False, "error": "invalid_e2e_manifest_run_id"}, 400
            await self.ctx.storage.put(
                storage_key,
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            return {"ok": True}, 200

        # action が不明なら400を返す
        return {"ok": False, "error": "invalid_action"}, 400
