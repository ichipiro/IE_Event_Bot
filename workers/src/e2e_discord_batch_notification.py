"""通常作成通知の所有・再試行・読戻し・回収を固定2件に制限する。"""

from hashlib import sha256
from urllib.parse import quote

from discord_notion_sync import (
    _build_event_created_message,
    _discord_add_reaction,
    _discord_send_message,
    _notify_discord_event_created,
)
from e2e_discord_batch_state import NOTIFICATION_SERVICE, check_batch_owner
from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_notion_probe import _env_text, _event_name
from e2e_discord_probe import (
    _has_own_check_reaction,
    _request_stage,
    _verify_targets,
)


class NotificationError(Exception):
    pass


class NotificationEnv(_DeltaEnv):
    def __init__(self, env):
        super().__init__(env)
        self.EVENT_CREATE_CHANNEL_ID = _env_text(env, "EVENT_CREATE_CHANNEL_ID")
        self.EVENT_CREATE_ROLE_ID = _env_text(env, "EVENT_CREATE_ROLE_ID")


class NotificationBatch:
    def __init__(self, env, store, manifest):
        self.env = NotificationEnv(env)
        self.store = store
        self.manifest = manifest
        self.channel = self.env.EVENT_CREATE_CHANNEL_ID
        self.role = self.env.EVENT_CREATE_ROLE_ID

    @staticmethod
    async def verify_target(env, stages):
        if not all(
            _env_text(env, k)
            for k in ("EVENT_CREATE_CHANNEL_ID", "EVENT_CREATE_ROLE_ID")
        ):
            raise NotificationError("discord_notification_target_required")
        error = await _verify_targets(env, stages, {})
        if error:
            raise NotificationError(error)

    async def save(self):
        await self.store.put_e2e_manifest(NOTIFICATION_SERVICE, self.manifest)

    async def find(self, slot):
        status, messages = await _request_stage(
            self.env,
            {},
            {},
            "notification_find",
            "GET",
            f"/channels/{quote(self.channel, safe='')}/messages?limit=50",
        )
        if status != 200 or not isinstance(messages, list):
            raise NotificationError("discord_notification_search_failed")
        matches = [
            m
            for m in messages
            if isinstance(m, dict)
            and m.get("channel_id") == self.channel
            and f"イベント名: {_event_name(slot['run_id'])}"
            in str(m.get("content") or "").splitlines()
        ]
        if len(matches) > 1:
            raise NotificationError("discord_notification_search_ambiguous")
        if matches and not matches[0].get("id"):
            raise NotificationError("discord_notification_search_failed")
        return str(matches[0]["id"]) if matches else ""

    def matches(self, message, slot):
        return (
            isinstance(message, dict)
            and message.get("id") == slot.get("message_id")
            and message.get("channel_id") == self.channel
            and f"イベント名: {_event_name(slot['run_id'])}"
            in str(message.get("content") or "").splitlines()
            and sha256(str(message.get("content") or "").encode()).hexdigest()
            == slot.get("message_content_sha256")
            and message.get("mention_roles") == [self.role]
            and message.get("mention_everyone") is False
        )

    def path(self, slot):
        return f"/channels/{quote(self.channel, safe='')}/messages/{quote(slot['message_id'], safe='')}"

    async def read(self, slot, *, reaction=None):
        status, message = await _request_stage(
            self.env,
            {},
            {},
            "notification_read",
            "GET",
            self.path(slot),
        )
        if status != 200 or not self.matches(message, slot):
            raise NotificationError("discord_notification_message_mismatch")
        if reaction is not None and _has_own_check_reaction(message) != reaction:
            raise NotificationError("discord_notification_reaction_mismatch")

    async def notify(self, env, event, *, delivery):
        await check_batch_owner(self.store, self.manifest)
        slots = self.manifest["fixtures"]
        slot = next(
            (s for s in slots if s["discord_event_id"] == str(event.get("id"))), None
        )
        if (
            slot is None
            or delivery.get("channel_id") != self.channel
            or delivery.get("message_id") != slot.get("message_id")
        ):
            raise NotificationError("discord_notification_owner_mismatch")

        async def send(scoped_env, channel, content, allowed_mentions=None):
            await check_batch_owner(self.store, self.manifest)
            if (
                channel != self.channel
                or slot["create_attempted"]["message"]
                or content != _build_event_created_message(self.env, event)
                or f"イベント名: {_event_name(slot['run_id'])}"
                not in content.splitlines()
            ):
                raise NotificationError("discord_notification_create_forbidden")
            if await self.find(slot):
                raise NotificationError("discord_notification_message_collision")
            slot["message_content_sha256"] = sha256(content.encode()).hexdigest()
            slot["create_attempted"]["message"] = True
            await self.save()
            message_id = await _discord_send_message(
                scoped_env,
                channel,
                content,
                allowed_mentions=allowed_mentions,
            )
            # 応答不明時は再投稿せず、cleanupの検索で回収する。
            if not message_id:
                raise NotificationError("discord_notification_post_unresolved")
            slot["message_id"] = message_id
            await self.save()
            await self.read(slot, reaction=False)
            return message_id

        async def react(scoped_env, channel, message_id, emoji):
            await check_batch_owner(self.store, self.manifest)
            if (
                channel != self.channel
                or message_id != slot.get("message_id")
                or emoji != "✅"
            ):
                raise NotificationError("discord_notification_reaction_forbidden")
            await self.read(slot)
            if slot is slots[0] and not slot.get("reaction_deferred"):
                # 初回だけAPI呼出し前に失敗を返し、通常queueのnotify再試行を通す。
                slot["reaction_deferred"] = True
                self.manifest["stages"]["notification_reaction_deferred"] = 200
                await self.save()
                return False
            ok = await _discord_add_reaction(scoped_env, channel, message_id, emoji)
            if ok:
                await self.read(slot, reaction=True)
                slot["reaction_done"] = True
                await self.save()
            return ok

        return await _notify_discord_event_created(
            env,
            event,
            delivery=delivery,
            send_message=send,
            add_reaction=react,
        )

    async def read_state(self, pending):
        slots = self.manifest["fixtures"]
        for index, slot in enumerate(slots):
            found = await self.find(slot)
            if pending and index == 1:
                if (
                    found
                    or slot.get("message_id")
                    or slot["create_attempted"]["message"]
                ):
                    raise NotificationError(
                        "discord_notification_pending_message_exists"
                    )
            else:
                if not found or found != slot.get("message_id"):
                    raise NotificationError("discord_notification_message_mismatch")
                await self.read(slot, reaction=bool(slot.get("reaction_done")))
        if not pending:
            if not all(s.get("reaction_done") for s in slots):
                raise NotificationError("discord_notification_reaction_missing")
            self.manifest["stages"]["notification_final_readback"] = 200

    def queue(self, pending):
        if not pending:
            return []
        first, second = self.manifest["fixtures"]
        queue = [
            {
                "op": "upsert",
                "id": second["discord_event_id"],
                "notification": {"channel_id": self.channel},
            }
        ]
        if not first.get("reaction_done"):
            queue.insert(
                0,
                {
                    "op": "notify",
                    "id": first["discord_event_id"],
                    "notification": {
                        "channel_id": self.channel,
                        "message_id": first["message_id"],
                    },
                },
            )
        return queue

    async def cleanup(self, slot, index):
        if slot.get("message_cleanup_done"):
            return True
        if not slot["create_attempted"]["message"]:
            slot["message_cleanup_done"] = True
            await self.save()
            return True
        await check_batch_owner(self.store, self.manifest)
        if not slot.get("message_id"):
            found = await self.find(slot)
            if not found:
                return False
            slot["message_id"] = found
            await self.save()
        for _ in range(4):
            await check_batch_owner(self.store, self.manifest)
            status, message = await _request_stage(
                self.env,
                {},
                {},
                "notification_cleanup_read",
                "GET",
                self.path(slot),
            )
            self.manifest["stages"][f"notification_{index}_cleanup_read"] = status
            if status == 404:
                slot["message_cleanup_done"] = True
                await self.save()
                return True
            if status != 200:
                continue
            if not self.matches(message, slot):
                return False
            status, _ = await _request_stage(
                self.env,
                {},
                {},
                "notification_delete",
                "DELETE",
                self.path(slot),
            )
            self.manifest["stages"][f"notification_{index}_delete"] = status
            if status not in (204, 404):
                continue
            # 別のGETで不在を確認してから回収完了にする。
        return False
