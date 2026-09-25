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
    if phase == "list_fail_first":
        return "prepare"
    if phase == "first" and owner.get("list_retry"):
        return "list_fail_first"
    if phase == "list_fail":
        return phases[phases.index(before) - 1]
    if phase == before and owner.get("list_retry"):
        return "list_fail"
    if phase == "fail":
        return phases[phases.index(before) - 1]
    if phase == before and owner.get("retry"):
        return "fail"
    return phases[phases.index(phase) - 1]


def install_list_failure(job_env, owner, prefix, phase):
    """実queryを1件ずつ取得し、対象scanの2ページ目だけ503を注入する。"""
    import json
    from workers import Response

    calls = []
    scan = 0
    target_scan = 2 if prefix == "qa_normal" and phase == "list_fail" else 1

    async def query(url, options):
        nonlocal scan
        body = json.loads(options["body"])
        if not body.get("start_cursor"):
            scan += 1
        if scan == target_scan and body.get("start_cursor"):
            calls.append(503)
            owner["stages"][f"{prefix}_{phase}_injected"] = 503
            return Response("{}", status=503)
        body["page_size"] = 1
        response = await jobs.fetch(url, {**options, "body": json.dumps(body)})
        if scan == target_scan:
            data = json.loads(await response.text())
            if (response.status != 200 or len(data.get("results", [])) != 1
                    or data.get("has_more") is not True or not data.get("next_cursor")):
                raise RuntimeError("job_list_first_page_invalid")
            calls.append(200)
            owner["stages"][f"{prefix}_{phase}_first_page"] = 200
            return Response(json.dumps(data), status=200)
        return response

    job_env._job_notion_query_fetch = query
    owner["list_retry"] = True
    return calls


def check_list_failure(owner, prefix, phase, status, detail, calls):
    if (status != 500 or calls != [200, 503]
            or detail != {"mode": "native", "ok": False, "error": "notion_query_failed_503"}):
        raise RuntimeError("job_list_failure_not_observed")
    owner["stages"][f"{prefix}_{phase}_http"] = status
    owner["stages"][f"{prefix}_{phase}"] = 200
    owner["list_failure_detail"] = detail


def list_retry_verified(owner, prefix):
    phases = ("list_fail_first", "list_fail") if prefix == "qa_normal" else ("list_fail",)
    return not owner.get("list_retry") or all(
        owner["stages"].get(f"{prefix}_verify_{phase}") == 200 for phase in phases
    )
