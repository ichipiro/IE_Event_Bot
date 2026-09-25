"""期間限定Workerで実Cron配信を記録する。HTTPからは起動できない。"""

import json
import re
import time

from workers import Response, WorkerEntrypoint

from entry import Application


CRON_FLAGS = (
    "CRON_ENABLE_SYNC", "CRON_ENABLE_DISCORD_NOTION_SYNC",
    "CRON_ENABLE_GCAL_WATCH_ENSURE", "CRON_ENABLE_QA",
    "CRON_ENABLE_REMINDER", "CRON_ENABLE_AUTO_CLEAN",
)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("not_found", status=404)

    async def scheduled(self, controller, env, ctx):
        run_id = str(getattr(self.env, "E2E_CRON_RUN_ID", ""))
        if not re.fullmatch(r"E2E-\d{8}T\d{6}Z-[a-f0-9]{8}", run_id):
            return
        start = int(self.env.E2E_CRON_START_MS)
        deadline = int(self.env.E2E_CRON_DEADLINE_MS)
        scheduled = int(controller.scheduledTime)
        now = int(time.time() * 1000)
        if (not 0 < deadline - start <= 1_200_000
                or not start <= scheduled <= now < deadline
                or scheduled % 60_000 != 0
                or str(controller.cron) != "* * * * *"):
            return
        metadata = self.env.CF_VERSION_METADATA
        if str(metadata.tag) != run_id:
            return
        # 実Cronの配信経路だけを検査し、通常ジョブへの外部書込みは許可しない。
        if any(str(getattr(self.env, flag, "")).lower() != "false" for flag in CRON_FLAGS):
            return
        results = await Application(self.env).scheduled(controller, env, ctx)
        if results != []:
            raise RuntimeError("cron_unexpected_job_execution")
        receipt = {
            "run_id": run_id, "version_id": str(metadata.id), "version_tag": str(metadata.tag),
            "commit": str(self.env.E2E_CRON_COMMIT), "cron": str(controller.cron),
            "scheduled_time_ms": scheduled, "observed_time_ms": now,
            "source": "scheduled", "normal_dispatch_completed": True, "job_count": len(results),
        }
        await self.env.STATE_KV.put(
            f"e2e:real-cron:{run_id}:{scheduled}", json.dumps(receipt),
        )
