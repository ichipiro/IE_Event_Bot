"""認証・所有権確認後の1要求だけで、最初の外部書込み失敗を固定注入する。"""

import jobs


def install_failure(job_env, owner, prefix):
    # env wrapperへだけ設定する。モジュール関数や他HTTPのenvは変更しない。
    calls = []

    async def send(env, channel, content, **kwargs):
        calls.append("send")
        if len(calls) == 1:
            owner["stages"][f"{prefix}_failure_injected"] = 200
            return False
        return await jobs._discord_send_message(env, channel, content, **kwargs)

    async def archive(env, page_id):
        calls.append("archive")
        if len(calls) == 1:
            owner["stages"][f"{prefix}_failure_injected"] = 200
            return False
        return await jobs._notion_archive_page(env, page_id)

    if prefix == "cleanup_normal":
        job_env._job_archive_page = archive
    else:
        job_env._job_send_message = send
    owner["retry"] = True
    return calls


def check_failure(owner, prefix, status, detail, calls):
    expected_calls = 1 if prefix == "cleanup_normal" else 2
    if (status != 500 or detail.get("ok") is not False or len(calls) != expected_calls
            or owner["stages"].get(f"{prefix}_failure_injected") != 200):
        raise RuntimeError("job_retry_failure_not_observed")
    if prefix == "cleanup_normal":
        if detail != {"mode": "native", "ok": False, "scanned": 2, "archived": 0}:
            raise RuntimeError("job_retry_failure_detail_invalid")
    elif detail.get("failed_count") != 1:
        raise RuntimeError("job_retry_failure_detail_invalid")
    owner["failure_detail"] = detail
    owner["stages"][f"{prefix}_failed_http"] = status
    owner["stages"][f"{prefix}_fail"] = 200


def previous_phase(owner, phases, phase, before):
    if phase == "fail":
        return phases[phases.index(before) - 1]
    if phase == before and owner.get("retry"):
        return "fail"
    return phases[phases.index(phase) - 1]
