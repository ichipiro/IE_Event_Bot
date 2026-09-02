import json
import time
from datetime import datetime, timezone
from inspect import isawaitable


_JS_ABSENT_VALUES = frozenset(("jsnull", "jsundefined"))
_LEGACY_E2E_MANIFEST_KEYS = {
    "google": "e2e:google_calendar_crud",
    "discord": "e2e:discord_crud",
    "notion": "e2e:notion_crud",
}


def _bool_env(value: str | None, default: bool = False) -> bool:
    """環境変数文字列を bool として解釈する。"""
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _json_text(payload) -> str:
    """JSON 比較/保存用に安定した文字列表現へ正規化する。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class StateStore:
    """
    Workers KV 上の状態アクセスを集約する。
    - 同期カーソル/最終実行時刻の保持
    - Google webhook 重複通知の抑止
    - gcal<->discord/notion マッピング保持
    - ジョブ結果の保存
    """

    def __init__(self, env):
        self.env = env

    def enabled(self) -> bool:
        """STATE_KV バインディングの有無を返す。"""
        return getattr(self.env, "STATE_KV", None) is not None

    def _kv(self):
        """内部ヘルパー: KV バインディングを返す。"""
        return getattr(self.env, "STATE_KV", None)

    def _sync_do(self):
        """内部ヘルパー: SyncCoordinator Durable Object namespace を返す。"""
        return getattr(self.env, "SYNC_COORDINATOR", None)

    def e2e_manifest_enabled(self) -> bool:
        """E2E cleanup 所有権用 Durable Object binding の有無を返す。"""
        return self._sync_do() is not None

    @staticmethod
    def _sync_do_stub(do_ns):
        """Durable Object namespace から global stub を取得する。"""
        if do_ns is None:
            return None
        return do_ns.getByName("global")

    @staticmethod
    async def _sync_do_rpc(stub, action: str, payload: dict | None = None):
        """SyncCoordinator RPCをJSON文字列だけで呼び出し、結果辞書を返す。"""
        if stub is None:
            return None
        body = _json_text({"action": action, **(payload or {})})
        result = stub.sync_state(body)
        # Python Workersのbinding wrapperがRPC結果を追加のcoroutineで包む場合がある。
        for _ in range(3):
            if not isawaitable(result):
                break
            result = await result
        if isawaitable(result):
            raise TypeError("sync_state_rpc_awaitable_depth_exceeded")
        try:
            data = json.loads(str(result or "{}"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def get_text(self, key: str) -> str | None:
        """KV から文字列を取得し、未設定値と空文字は None として扱う。"""
        kv = self._kv()
        if kv is None:
            return None
        value = await kv.get(key)
        if value is None:
            return None
        text = str(value).strip()
        if text in _JS_ABSENT_VALUES:
            return None
        return text or None

    async def put_text(self, key: str, value: str):
        """KV へ文字列を書き込む。"""
        kv = self._kv()
        if kv is None:
            return
        await kv.put(key, str(value))

    async def put_text_if_changed(self, key: str, value: str) -> bool:
        """現在値と異なる場合だけ KV へ文字列を書き込む。"""
        next_value = str(value)
        current = await self.get_text(key)
        if current == next_value:
            return False
        await self.put_text(key, next_value)
        return True

    async def get_json(self, key: str, default=None):
        """KV の JSON 文字列を辞書等へ復元する。失敗時は default。"""
        text = await self.get_text(key)
        if not text:
            return default
        try:
            return json.loads(text)
        except Exception:
            return default

    async def put_json(self, key: str, payload):
        """Python オブジェクトを JSON 化して KV へ保存する。"""
        await self.put_text(
            key,
            _json_text(payload),
        )

    async def put_json_if_changed(self, key: str, payload) -> bool:
        """現在値と異なる場合だけ JSON を KV へ保存する。"""
        next_text = _json_text(payload)
        current = await self.get_text(key)
        if current == next_text:
            return False
        await self.put_text(key, next_text)
        return True

    async def get_e2e_manifest(self, service: str) -> dict | None:
        """E2E cleanup manifest を強整合な Durable Object から取得する。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_manifest_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "get_e2e_manifest",
            {"service": str(service or "")},
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("e2e_manifest_read_failed")
        manifest = result.get("manifest")
        if manifest is None:
            return None
        if not isinstance(manifest, dict):
            raise RuntimeError("e2e_manifest_read_failed")
        return manifest

    async def put_e2e_manifest(self, service: str, payload: dict) -> None:
        """E2E cleanup manifest を強整合な Durable Object へ保存する。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_manifest_durable_object_required")
        if not isinstance(payload, dict):
            raise RuntimeError("e2e_manifest_invalid_payload")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "put_e2e_manifest",
            {
                "service": str(service or ""),
                "manifest_json": _json_text(payload),
            },
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("e2e_manifest_write_failed")

    async def attach_e2e_webhook_watch(
        self,
        *,
        run_id: str,
        channel_id: str,
        resource_id: str,
        expiration: str,
        watch_status: int,
    ) -> bool:
        """watch応答をrun所有manifestへ原子的に紐付ける。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_manifest_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "attach_e2e_webhook_watch",
            {
                "run_id": str(run_id or ""),
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "expiration": str(expiration or ""),
                "watch_status": watch_status,
            },
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("e2e_webhook_watch_attach_failed")
        return result.get("notification_received") is True

    async def record_e2e_webhook_delivery(
        self,
        *,
        channel_id: str,
        resource_id: str,
        resource_state: str,
        message_number: str,
    ) -> bool:
        """有効なrun所有watchへのGoogle初回通知だけを原子的に記録する。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_manifest_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "record_e2e_webhook_delivery",
            {
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "resource_state": str(resource_state or ""),
                "message_number": str(message_number or ""),
            },
        )
        if isinstance(result, dict) and result.get("ok") is True:
            if type(result.get("accepted")) is not bool:
                raise RuntimeError("e2e_webhook_delivery_record_failed")
            return result["accepted"]
        if isinstance(result, dict) and result.get("error") in {
            "inactive_e2e_webhook_delivery",
            "e2e_webhook_delivery_target_mismatch",
            "e2e_webhook_delivery_resource_mismatch",
            "e2e_webhook_delivery_notification_mismatch",
            "invalid_e2e_webhook_resource_id",
        }:
            return False
        raise RuntimeError("e2e_webhook_delivery_record_failed")

    async def attach_e2e_webhook_change_watch(
        self,
        *,
        run_id: str,
        channel_id: str,
        resource_id: str,
        expiration: str,
        watch_status: int,
    ) -> bool:
        """変更通知E2Eのwatch応答をrun所有manifestへ原子的に紐付ける。"""
        result = await self._e2e_webhook_change_rpc(
            "attach_e2e_webhook_change_watch",
            {
                "run_id": str(run_id or ""),
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "expiration": str(expiration or ""),
                "watch_status": watch_status,
            },
        )
        if result.get("ok") is not True:
            raise RuntimeError("e2e_webhook_change_watch_attach_failed")
        return result.get("notification_received") is True

    async def record_e2e_webhook_change_sync(
        self,
        *,
        channel_id: str,
        resource_id: str,
        resource_state: str,
        message_number: str,
    ) -> bool:
        """変更通知E2Eのwatch所有者へ届いた初回syncだけを記録する。"""
        result = await self._e2e_webhook_change_rpc(
            "record_e2e_webhook_change_sync",
            {
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "resource_state": str(resource_state or ""),
                "message_number": str(message_number or ""),
            },
        )
        if result.get("ok") is True and type(result.get("accepted")) is bool:
            return result["accepted"]
        if str(result.get("error") or "") in {
            "inactive_e2e_webhook_change",
            "e2e_webhook_change_target_mismatch",
            "e2e_webhook_change_resource_mismatch",
            "e2e_webhook_change_sync_mismatch",
            "invalid_e2e_webhook_change_resource_id",
        }:
            return False
        raise RuntimeError("e2e_webhook_change_sync_record_failed")

    async def prepare_e2e_webhook_change(
        self,
        *,
        run_id: str,
        updated_min: str,
    ) -> None:
        """event更新前のcursorと実行予定を原子的に記録する。"""
        result = await self._e2e_webhook_change_rpc(
            "prepare_e2e_webhook_change",
            {"run_id": str(run_id or ""), "updated_min": str(updated_min or "")},
        )
        if result.get("ok") is not True:
            raise RuntimeError("e2e_webhook_change_prepare_failed")

    async def record_e2e_webhook_change_update(
        self,
        *,
        run_id: str,
        update_status: int,
    ) -> None:
        """Google event更新のHTTP statusをrun所有manifestへ記録する。"""
        result = await self._e2e_webhook_change_rpc(
            "record_e2e_webhook_change_update",
            {"run_id": str(run_id or ""), "update_status": update_status},
        )
        if result.get("ok") is not True:
            raise RuntimeError("e2e_webhook_change_update_record_failed")

    async def claim_e2e_webhook_change(
        self,
        *,
        channel_id: str,
        resource_id: str,
        resource_state: str,
        message_number: str,
    ) -> dict[str, bool]:
        """run所有watchの最初のexists通知だけをdispatch用にclaimする。"""
        result = await self._e2e_webhook_change_rpc(
            "claim_e2e_webhook_change",
            {
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "resource_state": str(resource_state or ""),
                "message_number": str(message_number or ""),
            },
        )
        if result.get("ok") is True and all(
            type(result.get(key)) is bool for key in ("accepted", "duplicate")
        ):
            return {
                "accepted": result["accepted"],
                "duplicate": result["duplicate"],
            }
        if str(result.get("error") or "") in {
            "inactive_e2e_webhook_change",
            "e2e_webhook_change_target_mismatch",
            "e2e_webhook_change_resource_mismatch",
            "e2e_webhook_change_notification_mismatch",
            "invalid_e2e_webhook_change_resource_id",
        }:
            return {"accepted": False, "duplicate": False}
        raise RuntimeError("e2e_webhook_change_claim_failed")

    async def complete_e2e_webhook_change(
        self,
        *,
        run_id: str,
        channel_id: str,
        resource_id: str,
        message_number: str,
        dispatch: dict,
    ) -> None:
        """claimしたexists通知の同期dispatch結果を一度だけ確定する。"""
        result = await self._e2e_webhook_change_rpc(
            "complete_e2e_webhook_change",
            {
                "run_id": str(run_id or ""),
                "channel_id": str(channel_id or ""),
                "resource_id": str(resource_id or ""),
                "message_number": str(message_number or ""),
                **dict(dispatch or {}),
            },
        )
        if result.get("ok") is not True:
            raise RuntimeError("e2e_webhook_change_complete_failed")

    async def _e2e_webhook_change_rpc(self, action: str, payload: dict) -> dict:
        """変更通知E2E用のDurable Object RPCをfail closedで呼び出す。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_manifest_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            action,
            payload,
        )
        if not isinstance(result, dict):
            raise RuntimeError("e2e_webhook_change_rpc_failed")
        return result

    async def get_legacy_e2e_manifest(self, service: str) -> dict | None:
        """旧KV manifestを移行判定専用に読む。所有権の正本にはしない。"""
        key = _LEGACY_E2E_MANIFEST_KEYS.get(str(service or "").strip().lower())
        if key is None:
            raise RuntimeError("invalid_e2e_manifest_service")
        value = await self.get_json(key, None)
        return value if isinstance(value, dict) else None

    async def mark_google_message_seen(self, channel_id: str, message_number: str) -> bool:
        """
        Google webhook 重複通知抑止用。
        返り値:
            True: 既に処理済み
            False: 未処理だったので今回マークした
        """
        if not channel_id or not message_number:
            return False
        do_ns = self._sync_do()
        if do_ns is not None:
            stub = self._sync_do_stub(do_ns)
            result = await self._sync_do_rpc(
                stub,
                "mark_google_message_seen",
                {
                    "channel_id": channel_id,
                    "message_number": message_number,
                    "ttl_seconds": self.google_message_dedupe_ttl_seconds(self.env),
                },
            )
            if isinstance(result, dict) and "duplicate" in result:
                return bool(result.get("duplicate"))
        key = f"gcal_msg:{channel_id}:{message_number}"
        # 存在確認
        existing = await self.get_text(key)
        if existing is not None:
            return True
        await self.put_text(key, "1")
        return False

    async def mark_e2e_google_message_seen(
        self,
        channel_id: str,
        message_number: str,
        owner_run_id: str,
    ) -> bool:
        """E2E所有者付きのGoogle webhook重複状態を強整合に記録する。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_google_message_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "mark_google_message_seen",
            {
                "channel_id": str(channel_id or ""),
                "message_number": str(message_number or ""),
                "owner_run_id": str(owner_run_id or ""),
                "ttl_seconds": self.google_message_dedupe_ttl_seconds(self.env),
            },
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("e2e_google_message_mark_failed")
        if type(result.get("duplicate")) is not bool:
            raise RuntimeError("e2e_google_message_mark_failed")
        return result["duplicate"]

    async def clear_e2e_google_message_seen(
        self,
        channel_id: str,
        message_number: str,
        owner_run_id: str,
    ) -> bool:
        """所有runが一致するE2E webhook重複状態だけを削除する。"""
        do_ns = self._sync_do()
        if do_ns is None:
            raise RuntimeError("e2e_google_message_durable_object_required")
        result = await self._sync_do_rpc(
            self._sync_do_stub(do_ns),
            "clear_e2e_google_message_seen",
            {
                "channel_id": str(channel_id or ""),
                "message_number": str(message_number or ""),
                "owner_run_id": str(owner_run_id or ""),
            },
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("e2e_google_message_clear_failed")
        if type(result.get("cleared")) is not bool:
            raise RuntimeError("e2e_google_message_clear_failed")
        return result["cleared"]

    async def get_sync_updated_min(self) -> str | None:
        """Google差分同期カーソル(updatedMin)を取得する。"""
        return await self.get_text("sync:updated_min")

    async def set_sync_updated_min(self, updated_min: str):
        """Google差分同期カーソル(updatedMin)を保存する。"""
        if updated_min:
            await self.put_text_if_changed("sync:updated_min", str(updated_min))

    async def get_sync_last_epoch(self) -> float:
        """最後に同期成功した時刻(epoch秒)を取得する。"""
        do_ns = self._sync_do()
        if do_ns is not None:
            stub = self._sync_do_stub(do_ns)
            result = await self._sync_do_rpc(stub, "get_sync_last_epoch")
            try:
                return float((result or {}).get("last_epoch") or 0.0)
            except Exception:
                return 0.0
        text = await self.get_text("sync:last_epoch")
        if not text:
            return 0.0
        try:
            return float(text)
        except Exception:
            return 0.0

    async def set_sync_last_epoch_now(self):
        """最後の同期時刻を現在時刻で更新する。"""
        now_epoch = time.time()
        do_ns = self._sync_do()
        if do_ns is not None:
            stub = self._sync_do_stub(do_ns)
            await self._sync_do_rpc(stub, "set_sync_last_epoch", {"last_epoch": now_epoch})
            return
        await self.put_text("sync:last_epoch", str(now_epoch))

    async def should_skip_sync_by_cooldown(self, interval_seconds: float) -> bool:
        """クールダウン判定。直近実行から interval 未満なら True。"""
        if interval_seconds <= 0:
            return False
        now = time.time()
        last_epoch = await self.get_sync_last_epoch()
        return (now - last_epoch) < interval_seconds

    async def get_gcal_discord_map(self) -> dict:
        """GoogleイベントID -> DiscordイベントID の対応表を取得する。"""
        value = await self.get_json("map:gcal_discord", {})
        return value if isinstance(value, dict) else {}

    async def set_gcal_discord_map(self, data: dict):
        """GoogleイベントID -> DiscordイベントID の対応表を保存する。"""
        await self.put_json_if_changed("map:gcal_discord", data or {})

    async def get_gcal_notion_map(self) -> dict:
        """GoogleイベントID -> NotionページID の対応表を取得する。"""
        value = await self.get_json("map:gcal_notion", {"internal": {}, "external": {}})
        if not isinstance(value, dict):
            return {"internal": {}, "external": {}}
        value.setdefault("internal", {})
        value.setdefault("external", {})
        return value

    async def set_gcal_notion_map(self, data: dict):
        """GoogleイベントID -> NotionページID の対応表を保存する。"""
        payload = data if isinstance(data, dict) else {"internal": {}, "external": {}}
        payload.setdefault("internal", {})
        payload.setdefault("external", {})
        await self.put_json_if_changed("map:gcal_notion", payload)

    async def get_discord_snapshot(self) -> dict:
        """Discordポーリング差分検知用スナップショットを取得する。"""
        value = await self.get_json("discord:snapshot", {})
        return value if isinstance(value, dict) else {}

    async def set_discord_snapshot(self, data: dict):
        """Discordポーリング差分検知用スナップショットを保存する。"""
        await self.put_json_if_changed("discord:snapshot", data or {})

    async def set_last_result(self, op_name: str, payload: dict):
        """ジョブ/同期結果を `result:<op_name>` に保存する。"""
        if not op_name:
            return
        existing = await self.get_last_result(op_name)
        min_interval = self.result_write_min_interval_seconds(self.env)
        if isinstance(existing, dict):
            existing_payload = existing.get("payload") or {}
            existing_updated_at = str(existing.get("updated_at") or "")
            try:
                existing_dt = datetime.fromisoformat(existing_updated_at.replace("Z", "+00:00"))
            except Exception:
                existing_dt = None
            if existing_payload == (payload or {}) and existing_dt is not None:
                elapsed = (datetime.now(timezone.utc) - existing_dt).total_seconds()
                if elapsed < min_interval:
                    return
        now_iso = datetime.now(timezone.utc).isoformat()
        data = {
            "updated_at": now_iso,
            "payload": payload or {},
        }
        await self.put_json_if_changed(f"result:{op_name}", data)

    async def get_last_result(self, op_name: str):
        """`result:<op_name>` の最新結果を取得する。"""
        if not op_name:
            return None
        value = await self.get_json(f"result:{op_name}", None)
        return value if isinstance(value, dict) else None

    @staticmethod
    def result_write_min_interval_seconds(env) -> float:
        """同一内容の last_result を再保存する最小間隔を返す。"""
        raw = getattr(env, "KV_RESULT_MIN_WRITE_SECONDS", "3600")
        try:
            return max(0.0, float(raw))
        except Exception:
            return 3600.0

    @staticmethod
    def google_message_dedupe_ttl_seconds(env) -> float:
        """Google webhook 重複抑止の保持秒数を返す。"""
        raw = getattr(env, "GCAL_DEDUPE_TTL_SECONDS", "86400")
        try:
            return max(60.0, float(raw))
        except Exception:
            return 86400.0

    @staticmethod
    def is_kv_sync_cooldown_enabled(env) -> bool:
        """同期クールダウン機能の有効/無効を返す。"""
        return _bool_env(getattr(env, "KV_SYNC_COOLDOWN_ENABLED", "true"), default=True)

    @staticmethod
    def is_gcal_dedupe_enabled(env) -> bool:
        """Google webhook 重複抑止機能の有効/無効を返す。"""
        return _bool_env(getattr(env, "KV_GCAL_DEDUPE_ENABLED", "true"), default=True)
