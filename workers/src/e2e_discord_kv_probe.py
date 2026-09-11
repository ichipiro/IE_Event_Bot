"""所有Discordイベントを通常差分処理とKVへ接続するE2E。"""

from urllib.parse import quote

from discord_notion_sync import _apply_discord_event_diff, _fingerprint, _sync_discord_event_upsert
from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_fixture_state import SERVICE, KIND, FixtureDiscordKV, FixtureStore
from e2e_discord_notion_probe import (
    _configuration_error, _discord_event_is_owned, _env_text, _notion_page_matches,
    _target_fingerprints, cleanup_discord_notion_sync_probe, run_discord_notion_sync_probe,
)
from e2e_discord_probe import _request_stage as discord_request
from e2e_notion_probe import _request_stage as notion_request


async def run_discord_kv_probe(env, store, run_id: str, phase: str) -> dict:
    scope = _env_text(env, "E2E_STATE_SCOPE")
    if not scope or not store.enabled() or not store.e2e_manifest_enabled():
        return {"ok": False, "error": "discord_kv_bindings_required"}
    targets = _target_fingerprints(_env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID"))
    fixtures = FixtureStore(store, run_id, scope, targets)
    if phase == "prepare":
        async def apply(event, stages, retries):
            manifest = await fixtures.get_e2e_manifest(SERVICE)
            fixtures.check_owner(manifest)
            if str(event.get("id") or "") != manifest["discord_event_id"]:
                return False
            state = FixtureDiscordKV(store, manifest).state()

            async def create_owned(scoped_env, owned_event, token):
                return await _sync_discord_event_upsert(scoped_env, owned_event, token, require_new_internal_page=True)

            async def reject_delete(scoped_env, event_id, token):
                return False

            result = await _apply_discord_event_diff(
                _DeltaEnv(env), state, [event], upsert_runner=create_owned, delete_runner=reject_delete,
            )
            return result.get("ok") is True and all(result.get(k) == v for k, v in {
                "created": 1, "updated": 0, "deleted": 0, "processed_changes": 1,
                "pending_changes": 0, "error_count": 0,
            }.items())

        return await run_discord_notion_sync_probe(
            env, fixtures, run_id, manifest_service=SERVICE, manifest_kind=KIND,
            apply_runner=apply, pause_after_apply=True,
        )
    manifest = await fixtures.get_e2e_manifest(SERVICE)
    if phase == "cleanup":
        if manifest and manifest.get("dirty") is True:
            fixtures.check_owner(manifest)
        elif not manifest or manifest.get("last_run_id") != run_id or any(
            manifest.get("resource_fingerprints", {}).get(k) != v
            for k, v in {**targets, "state_scope_sha256": fixtures.scope_sha}.items()
        ):
            return {"ok": False, "error": "discord_kv_owner_mismatch"}
        return await cleanup_discord_notion_sync_probe(
            env, fixtures, run_id, manifest_service=SERVICE, manifest_kind=KIND,
        )
    if phase != "verify" or not manifest:
        return {"ok": False, "error": "discord_kv_not_prepared"}
    fixtures.check_owner(manifest)
    if manifest.get("stage") not in ("kv_prepared", "kv_verifying", "kv_verified"):
        return {"ok": False, "dirty": True, "error": "discord_kv_not_prepared"}
    manifest["stage"] = "kv_verifying"
    await fixtures.put_e2e_manifest(SERVICE, manifest)
    error = _configuration_error(env)
    if error:
        return {"ok": False, "dirty": True, "error": error}
    stages, retries = {}, {}
    event_id, page_id = manifest["discord_event_id"], manifest["notion_page_id"]
    guild, database = _env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    status, event = await discord_request(env, stages, retries, "kv_source_read", "GET",
        f"/guilds/{quote(guild, safe='')}/scheduled-events/{quote(event_id, safe='')}")
    if status != 200 or not isinstance(event, dict) or not _discord_event_is_owned(
        event, event_id=event_id, guild_id=guild, run_id=run_id,
    ):
        return {"ok": False, "dirty": True, "error": "discord_kv_source_mismatch"}
    status, page = await notion_request(env, stages, retries, "kv_page_read", "GET", f"/pages/{quote(page_id, safe='')}")
    if status != 200 or not isinstance(page, dict) or not _notion_page_matches(
        page, page_id=page_id, database_id=database, discord_event=event, run_id=run_id,
    ):
        return {"ok": False, "dirty": True, "error": "discord_kv_page_mismatch"}
    state = FixtureDiscordKV(store, manifest).state()
    if await state.get_discord_snapshot() != {event_id: _fingerprint(event)} or await state.get_json("sync:discord_notion_queue") != []:
        return {"ok": False, "dirty": True, "error": "discord_kv_not_ready"}
    manifest["stage"] = "kv_verified"
    manifest.setdefault("stages", {}).update({**stages, "kv_readback": 200})
    await fixtures.put_e2e_manifest(SERVICE, manifest)
    return {"ok": True, "dirty": True, "stage": "kv_verified", "stages": stages}
