import json
import time
from hmac import compare_digest
from inspect import isawaitable
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from workers import Response, WorkerEntrypoint

from discord_notion_sync import run_discord_notion_poll_sync
from google_auth import describe_google_auth_sources, set_google_access_token
from google_apply_sync import apply_google_events
from google_calendar_sync import run_google_delta_fetch
from google_watch import ensure_watch_active
from health_checks import run_connectivity_checks
from jobs import run_auto_clean_job, run_day_before_reminder_job, run_qa_notification_job
from state import StateStore
from sync_lock_do import SyncCoordinator


def _json_response(payload: dict, status: int = 200) -> Response:
    """JSON レスポンスを統一フォーマットで返す。"""
    return Response(
        json.dumps(payload, ensure_ascii=False),
        status=status,
        headers={"content-type": "application/json; charset=utf-8"},
    )


def _header(request, name: str) -> str | None:
    """HTTP ヘッダ値を trim して取得する。未設定時は None。"""
    value = request.headers.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_env(value: str | None, default: bool = False) -> bool:
    """環境変数文字列を bool として解釈する。"""
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _detail_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"ok": bool(value)}


class Default(WorkerEntrypoint):
    """
    Worker のエントリポイント。
    - HTTP ルーティング（fetch）
    - 定期ジョブ実行（scheduled）
    - 同期ディスパッチ制御（cooldown / lock / mode）
    """

    async def fetch(self, request):
        """
        HTTP エンドポイントを振り分ける。
        - 管理/ジョブ系は `_authorized` で保護
        - 成功/失敗の要約は `state.set_last_result` に保存
        - `/gcal/webhook` は dedupe 後に sync dispatch を呼ぶ
        """
        parsed_url = urlparse(request.url)
        path = parsed_url.path
        method = str(request.method or "GET").upper()
        state = StateStore(self.env)

        # ヘルスチェック
        if path == "/health" and method == "GET":
            return _json_response(
                {
                    "ok": True,
                    "kv_state_enabled": state.enabled(),
                }
            )
        
        # 手動同期の入口
        if path in ("/sync/all", "/gcal/sync"):
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            return await self._run_sync_dispatch(request, state, source="manual")

        # Discord → Notion のポーリング同期を実行
        if path == "/sync/discord-notion":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            result = _detail_dict(await run_discord_notion_poll_sync(self.env, state))
            if state.enabled():
                await state.set_last_result("sync_discord_notion", result)
            return _json_response(result, status=200 if result.get("ok") else 500)

        # 管理API経由で Google access token を手動登録する入口
        if path == "/admin/google-token":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            if method != "POST":
                return _json_response({"ok": False, "error": "method_not_allowed"}, status=405)
            body_text = await request.text()
            try:
                payload = json.loads(body_text or "{}")
            except Exception:
                payload = {}
            token = str((payload or {}).get("access_token") or "").strip()
            expires_in = (payload or {}).get("expires_in")
            ok = await set_google_access_token(state, token, expires_in)
            return _json_response({"ok": ok}, status=200 if ok else 400)
        
        # Google Calendar watch を ensure する
        if path == "/admin/gcal/watch/ensure":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            result = await ensure_watch_active(self.env, state)
            if state.enabled() and str(result.get("action") or "") != "noop_valid":
                await state.set_last_result("gcal_watch_ensure", result)
            return _json_response(result, status=200 if result.get("ok") else 500)

        # 移行状況・システム状態の確認API
        if path == "/admin/migration-status":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            include_checks = self._to_bool_query(parsed_url.query, "include_checks")
            status_payload = await self._migration_status(state, include_checks=include_checks)
            return _json_response(status_payload, status=200)

        # Google Calendar webhook 通知の受信口
        if path == "/gcal/webhook":
            if method != "POST":
                return _json_response({"ok": False, "error": "method_not_allowed"}, status=405)
            required_token = str(getattr(self.env, "GCAL_WEBHOOK_TOKEN", "") or "").strip()
            if not required_token:
                return Response("webhook unavailable", status=503)
            channel_token = _header(request, "X-Goog-Channel-Token")
            if not channel_token or not compare_digest(
                channel_token.encode("utf-8"),
                required_token.encode("utf-8"),
            ):
                return Response("unauthorized", status=401)
            if state.enabled() and StateStore.is_gcal_dedupe_enabled(self.env):
                goog_channel = _header(request, "X-Goog-Channel-ID")
                goog_msg = _header(request, "X-Goog-Message-Number")
                # 重複チェック
                duplicated = await state.mark_google_message_seen(
                    goog_channel or "",
                    goog_msg or "",
                )
                if duplicated:
                    return Response("", status=204)
            sync_resp = await self._run_sync_dispatch(request, state, source="webhook")
            if int(sync_resp.status) >= 500:
                return Response("sync failed", status=500)
            return Response("", status=204)

        # Q&A 未回答更新通知ジョブを実行
        if path == "/jobs/qa-check":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            detail = _detail_dict(await run_qa_notification_job(self.env, state, return_detail=True))
            ok = bool(detail.get("ok"))
            if state.enabled():
                await state.set_last_result(
                    "job_qa_check",
                    {"mode": "native", **detail},
                )
            return _json_response({"mode": "native", **detail}, status=200 if ok else 500)
        
        # 前日リマインドジョブを実行
        if path == "/jobs/reminder":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            detail = _detail_dict(
                await run_day_before_reminder_job(self.env, state, return_detail=True)
            )
            ok = bool(detail.get("ok"))
            if state.enabled():
                await state.set_last_result(
                    "job_reminder",
                    {"mode": "native", **detail},
                )
            return _json_response({"mode": "native", **detail}, status=200 if ok else 500)

        # Notion cleanup ジョブを実行
        if path == "/jobs/cleanup":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            detail = _detail_dict(await run_auto_clean_job(self.env, state, return_detail=True))
            ok = bool(detail.get("ok"))
            if state.enabled():
                await state.set_last_result(
                    "job_cleanup",
                    {"mode": "native", **detail},
                )
            return _json_response({"mode": "native", **detail}, status=200 if ok else 500)
        """
        全部まとめて実行する。
        手順:
        1) 同期ディスパッチ
        2) QA通知
        3) 前日リマインド
        4) クリーンアップ
        """
        if path == "/jobs/run-all":
            if not self._authorized(request):
                return Response("unauthorized", status=401)
            sync_response = await self._run_sync_dispatch(request, state, source="jobs")
            sync_ok = int(sync_response.status) < 300
            qa_detail = _detail_dict(
                await run_qa_notification_job(self.env, state, return_detail=True)
            )
            qa_ok = bool(qa_detail.get("ok"))
            reminder_detail = _detail_dict(
                await run_day_before_reminder_job(self.env, state, return_detail=True)
            )
            reminder_ok = bool(reminder_detail.get("ok"))
            cleanup_detail = _detail_dict(
                await run_auto_clean_job(self.env, state, return_detail=True)
            )
            cleanup_ok = bool(cleanup_detail.get("ok"))
            all_ok = sync_ok and qa_ok and reminder_ok and cleanup_ok
            if state.enabled():
                await state.set_last_result(
                    "job_run_all",
                    {
                        "ok": all_ok,
                        "sync_ok": sync_ok,
                        "qa": qa_detail,
                        "reminder": reminder_detail,
                        "cleanup": cleanup_detail,
                    },
                )
            return _json_response(
                {
                    "ok": all_ok,
                    "sync_ok": sync_ok,
                    "qa": qa_detail,
                    "reminder": reminder_detail,
                    "cleanup": cleanup_detail,
                },
                status=200 if all_ok else 500,
            )
        
        # 未定義 -> 404 フォールバック
        return _json_response(
            {
                "ok": False,
                "error": "not_found",
                "path": path,
            },
            status=404,
        )

    async def scheduled(self, controller, env, ctx):
        """
        Cron Trigger 実行エントリ。
        有効化フラグに応じて sync / watch / jobs を順に実行し、
        実行結果を配列で返す。
        controller, env, ctx は Workers の scheduled handler で渡される引数。
        """
        # 全体同期を実行するかどうか
        run_sync = _bool_env(getattr(self.env, "CRON_ENABLE_SYNC", "true"), default=True)
        # Discord -> Notion のポーリング同期を実行するかどうか
        run_discord_notion_sync = _bool_env(
            getattr(self.env, "CRON_ENABLE_DISCORD_NOTION_SYNC", "false"),
            default=False,
        )
        # Google Calendar watch の ensure をするかどうか
        run_watch_ensure = _bool_env(
            getattr(self.env, "CRON_ENABLE_GCAL_WATCH_ENSURE", "false"),
            default=False,
        )
        # Q&A 通知ジョブを走らせるかどうか
        run_qa = _bool_env(getattr(self.env, "CRON_ENABLE_QA", "true"), default=True)
        # 前日リマインドジョブを走らせるかどうか
        run_reminder = _bool_env(
            getattr(self.env, "CRON_ENABLE_REMINDER", "true"),
            default=True,
        )
        # Notion cleanup ジョブを走らせるかどうか
        run_cleanup = _bool_env(
            getattr(self.env, "CRON_ENABLE_AUTO_CLEAN", "true"),
            default=True,
        )

        results = [] # 結果配列
        # 全体同期を実行
        if run_sync:
            sync_response = await self._run_sync_dispatch(None, StateStore(self.env), source="cron")
            results.append(
                {
                    "path": "/sync/all",
                    "ok": int(sync_response.status) < 300,
                    "status": int(sync_response.status),
                }
            )
        if run_discord_notion_sync:
            result = _detail_dict(
                await run_discord_notion_poll_sync(self.env, StateStore(self.env))
            )
            result["path"] = "/sync/discord-notion"
            results.append(result)
            if StateStore(self.env).enabled():
                await StateStore(self.env).set_last_result("sync_discord_notion", result)
        if run_watch_ensure:
            watch_result = _detail_dict(await ensure_watch_active(self.env, StateStore(self.env)))
            watch_result["path"] = "/admin/gcal/watch/ensure"
            results.append(watch_result)
            if StateStore(self.env).enabled() and str(watch_result.get("action") or "") != "noop_valid":
                await StateStore(self.env).set_last_result("gcal_watch_ensure", watch_result)
        if run_qa:
            qa_detail = _detail_dict(
                await run_qa_notification_job(
                    self.env,
                    StateStore(self.env),
                    return_detail=True,
                )
            )
            ok = bool(qa_detail.get("ok"))
            results.append({"ok": ok, "path": "/jobs/qa-check", "status": 200 if ok else 500})
            if StateStore(self.env).enabled():
                await StateStore(self.env).set_last_result(
                    "job_qa_check",
                    {"mode": "native", "source": "cron", **qa_detail},
                )
        if run_reminder:
            reminder_detail = _detail_dict(
                await run_day_before_reminder_job(
                    self.env,
                    StateStore(self.env),
                    return_detail=True,
                )
            )
            ok = bool(reminder_detail.get("ok"))
            results.append({"ok": ok, "path": "/jobs/reminder", "status": 200 if ok else 500})
            if StateStore(self.env).enabled():
                await StateStore(self.env).set_last_result(
                    "job_reminder",
                    {"mode": "native", "source": "cron", **reminder_detail},
                )
        if run_cleanup:
            cleanup_detail = _detail_dict(
                await run_auto_clean_job(
                    self.env,
                    StateStore(self.env),
                    return_detail=True,
                )
            )
            ok = bool(cleanup_detail.get("ok"))
            results.append({"ok": ok, "path": "/jobs/cleanup", "status": 200 if ok else 500})
            if StateStore(self.env).enabled():
                await StateStore(self.env).set_last_result(
                    "job_cleanup",
                    {"mode": "native", "source": "cron", **cleanup_detail},
                )
        return results

    def _authorized(self, request) -> bool:
        """
        Bearer 認可判定。
        INTERNAL_API_TOKEN 未設定時は安全側に倒して拒否する。
        """
        required_token = str(getattr(self.env, "INTERNAL_API_TOKEN", "") or "").strip()
        if not required_token:
            return False
        auth_header = _header(request, "Authorization")
        if not auth_header:
            return False
        # ヘッダが Bearer で始まるか確認
        if not auth_header.lower().startswith("bearer "):
            return False
        # 実際のトークン部分を取り出す
        token = auth_header[7:].strip()
        return compare_digest(token.encode("utf-8"), required_token.encode("utf-8"))

    async def _run_sync_dispatch(self, request, state: StateStore, source: str):
        """
        同期処理の中核ディスパッチ。
        手順:
        1) KV クールダウン判定
        2) Durable Object ロック取得（有効時）
        3) mode に応じて Google fetch/apply + Discord poll sync 実行
        4) 成功時はカーソル/最終時刻/last_result を更新
        5) finally でロック解放
        """
        # 同期間隔を取得
        sync_interval = self._sync_interval_seconds()
        # KV クールダウン判定
        if (
            state.enabled()
            and StateStore.is_kv_sync_cooldown_enabled(self.env)
            and await state.should_skip_sync_by_cooldown(sync_interval)
        ):
            return _json_response(
                {
                    "ok": True,
                    "status": "cooldown_skip",
                    "interval_seconds": sync_interval,
                    "source": source,
                },
                status=200,
            )
        lock_owner = None
        # Durable Object ロック要求(別の実行がまだ進行中なら失敗)
        if self._durable_lock_enabled():
            acquired = await self._acquire_sync_lock(source=source)
            if not acquired.get("ok"):
                return _json_response(
                    {
                        "ok": True,
                        "status": "in_progress_skip",
                        "source": source,
                        "lock": acquired,
                    },
                    status=200,
                )
            lock_owner = acquired.get("owner")
        try:
            mode = self._sync_all_mode()
            # Google 差分取得
            google_result = await run_google_delta_fetch(self.env, state, commit_cursor=False)
            apply_result = {"ok": True, "skipped": True}
            if google_result.get("ok"):
                apply_result = await apply_google_events(
                    self.env,
                    state,
                    google_result.get("items") or [],
                )
            """
            - Google差分取得成功
            - Google apply 成功
            - state 利用可能
            この3つがそろったときだけ、次回カーソルを保存する。
            """
            if google_result.get("ok") and apply_result.get("ok") and state.enabled():
                next_cursor = str(google_result.get("next_updated_min") or "")
                if next_cursor:
                    await state.set_sync_updated_min(next_cursor)
            discord_result = {"ok": True, "skipped": True}
            if self._sync_all_include_discord_notion():
                discord_result = await run_discord_notion_poll_sync(self.env, state)
            # 全体成功判定
            ok = (
                bool(google_result.get("ok"))
                and bool(apply_result.get("ok"))
                and bool(discord_result.get("ok"))
            )
            # 成功時に最終同期時刻を保存
            if ok and state.enabled():
                await state.set_sync_last_epoch_now()
            if state.enabled():
                await state.set_last_result(
                    "sync_all",
                    {
                        "ok": ok,
                        "mode": mode,
                        "google_ok": bool(google_result.get("ok")),
                        "google_apply_ok": bool(apply_result.get("ok")),
                        "discord_notion_ok": bool(discord_result.get("ok")),
                    },
                )
            return _json_response(
                {
                    "ok": ok,
                    "mode": mode,
                    "source": source,
                    "google": google_result,
                    "google_apply": apply_result,
                    "discord_notion": discord_result,
                },
                status=200 if ok else 500,
            )
        # ロック解除
        finally:
            if lock_owner:
                await self._release_sync_lock(lock_owner)

    def _sync_interval_seconds(self) -> float:
        """同期クールダウン秒数を返す。"""
        value = getattr(self.env, "SYNC_INTERVAL_SECONDS", "300")
        try:
            return max(0.0, float(value))
        except Exception:
            return 300.0

    def _sync_all_mode(self) -> str:
        """同期モード名を返す。現行実装は native 固定。"""
        return "native"

    def _sync_all_include_discord_notion(self) -> bool:
        """/sync/all で Discord->Notion を実行するか。"""
        return _bool_env(
            getattr(self.env, "SYNC_ALL_INCLUDE_DISCORD_NOTION", "false"),
            default=False,
        )

    def _durable_lock_enabled(self) -> bool:
        """Durable Object ロック有効/無効。"""
        return _bool_env(getattr(self.env, "SYNC_DO_LOCK_ENABLED", "true"), default=True)

    async def _acquire_sync_lock(self, source: str):
        """
        SyncCoordinator Durable Object で排他ロックを取得する。
        失敗時は `ok: false` を返し、呼び出し側で skip させる。
        """
        # SYNC_COORDINATOR : Workers の Durable Object namespace
        do_ns = getattr(self.env, "SYNC_COORDINATOR", None)
        if do_ns is None:
            return {"ok": True, "owner": None, "mode": "no_binding"}
        # owner を作る
        owner = f"{source}-{int(time.time())}-{uuid4()}"
        stage = "get_stub"
        try:
            # 同期ロックはグローバルに1つ
            stub = self._get_sync_stub(do_ns)
            # SyncCoordinator acquire をRPCで呼ぶ
            stage = "rpc_call"
            result = await self._do_stub_rpc(
                stub,
                {
                    "action": "acquire",
                    "owner": owner,
                    "ttl_seconds": self._sync_lock_ttl_seconds(),
                },
            )
            stage = "decode_rpc"
            data = self._decode_do_rpc(result)
            if not data.get("ok"):
                return {"ok": False, "status": 409 if data.get("locked") else 503, **data}
            return {"ok": True, "owner": data.get("owner") or owner}
        except Exception as exc:
            error_type = type(exc).__name__
            print(
                json.dumps(
                    {
                        "event": "sync_lock_acquire_failed",
                        "stage": stage,
                        "error_type": error_type,
                    },
                    ensure_ascii=False,
                )
            )
            return {
                "ok": False,
                "error": "do_acquire_exception",
                "stage": stage,
                "error_type": error_type,
            }

    async def _release_sync_lock(self, owner: str):
        """取得済みロックを解放する。解放失敗は握りつぶす。"""
        do_ns = getattr(self.env, "SYNC_COORDINATOR", None)
        if do_ns is None or not owner:
            return
        try:
            stub = self._get_sync_stub(do_ns)
            # SyncCoordinator release をRPCで呼ぶ
            await self._do_stub_rpc(
                stub,
                {"action": "release", "owner": owner},
            )
        except Exception:
            return

    def _sync_lock_ttl_seconds(self) -> float:
        """ロック TTL を秒で返す（最小 10 秒）。"""
        raw = str(getattr(self.env, "SYNC_DO_LOCK_TTL_SECONDS", "120") or "120")
        try:
            return max(10.0, float(raw))
        except Exception:
            return 120.0

    async def _migration_status(self, state: StateStore, *, include_checks: bool = False) -> dict:
        """
        運用診断用ステータスを組み立てる。
        含む情報:
        - 環境変数充足
        - 認証ソース状態
        - 同期カーソル/最終時刻
        - watch 状態
        - last_results
        - lock 状態
        """
        connectivity_checks = None
        # include_checks 実行時は先に外部疎通を実行し、google_auth 診断に直近エラーを反映する
        if include_checks:
            connectivity_checks = await run_connectivity_checks(self.env, state)
        # Google 認証
        google_auth = await describe_google_auth_sources(self.env, state)
        # 最後に同期成功した時刻
        sync_last_epoch = await state.get_sync_last_epoch() if state.enabled() else 0.0
        # Google 差分取得用のカーソル
        sync_updated_min = await state.get_sync_updated_min() if state.enabled() else None
        # watch 状態
        watch_state = await state.get_json("gcal_watch_state", None) if state.enabled() else None
        if isinstance(watch_state, dict):
            watch_state = dict(watch_state)
            watch_state.pop("webhook_token_sha256", None)
        last_results = {}
        if state.enabled():
            for key in (
                "sync_all",
                "sync_discord_notion",
                "job_qa_check",
                "job_reminder",
                "job_cleanup",
                "job_run_all",
                "gcal_watch_ensure",
            ):
                last_results[key] = await state.get_last_result(key)
        payload = {
            "ok": True,
            "mode": self._sync_all_mode(),
            "kv_enabled": state.enabled(),
            "features": {
                "sync_all_include_discord_notion": self._sync_all_include_discord_notion(),
            },
            "required_envs": {
                "notion_token": bool(getattr(self.env, "NOTION_TOKEN", None)),
                "notion_internal_db": bool(getattr(self.env, "NOTION_EVENT_INTERNAL_ID", None)),
                "google_calendar_id": bool(getattr(self.env, "GOOGLE_CALENDAR_ID", None)),
                "discord_token": bool(getattr(self.env, "DISCORD_TOKEN", None)),
                "discord_guild_id": bool(getattr(self.env, "DISCORD_GUILD_ID", None)),
            },
            "google_auth": google_auth,
            "sync_state": {
                "last_epoch": sync_last_epoch,
                "updated_min": sync_updated_min,
            },
            "watch_state": watch_state,
            "last_results": last_results,
            "sync_lock": await self._sync_lock_status(),
        }
        if include_checks:
            payload["connectivity_checks"] = connectivity_checks
        return payload

    async def _sync_lock_status(self):
        """Durable Object から現在のロック状態を取得する。"""
        do_ns = getattr(self.env, "SYNC_COORDINATOR", None)
        if do_ns is None:
            return {"enabled": False, "reason": "no_binding"}
        try:
            stub = self._get_sync_stub(do_ns)
            # Durable Object status をRPCで取得する
            result = await self._do_stub_rpc(
                stub,
                {"action": "status"},
            )
            data = self._decode_do_rpc(result)
            return {"enabled": True, "status": 200, **data}
        except Exception as exc:
            return {"enabled": True, "error": "status_exception", "detail": str(exc)[:200]}

    @staticmethod
    def _get_sync_stub(do_ns):
        """
        Durable Object namespace から "global" stub を取得(DO生成)する。
        """
        return do_ns.getByName("global")

    @staticmethod
    async def _do_stub_rpc(stub, payload: dict):
        """SyncCoordinator RPCをJSON文字列だけで呼び出す。"""
        result = stub.sync_state(json.dumps(payload, ensure_ascii=False))
        # Python Workersのbinding wrapperがRPC結果を追加のcoroutineで包む場合がある。
        for _ in range(3):
            if not isawaitable(result):
                return result
            result = await result
        raise TypeError("sync_state_rpc_awaitable_depth_exceeded")

    @staticmethod
    def _decode_do_rpc(value) -> dict:
        """SyncCoordinator RPCのJSON文字列を辞書へ復元する。"""
        data = json.loads(str(value or "{}"))
        if not isinstance(data, dict):
            raise TypeError("invalid_sync_state_rpc_result")
        return data

    @staticmethod
    def _to_bool_query(query_string: str, key: str) -> bool:
        """クエリ文字列中の bool 値（1/true/yes/on）を判定する。"""
        if not query_string:
            return False
        # URL のクエリ文字列を辞書に変換
        parsed = parse_qs(query_string, keep_blank_values=False)
        values = parsed.get(key) or []
        if not values:
            return False
        # 最初の値だけ使う
        value = str(values[0]).strip().lower()
        return value in ("1", "true", "yes", "on")
