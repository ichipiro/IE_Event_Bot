"""所有確認済みの要求でのみ、KV保存前後の固定例外を注入する。"""


class FaultKV:
    def __init__(self, kv, owner, prefix, keys):
        self.kv, self.owner, self.prefix = kv, owner, prefix
        self.keys = keys
        self.calls = {}
        self.values = {}

    async def get(self, key):
        return await self.kv.get(key)

    async def put(self, key, value):
        if key not in self.keys:
            raise RuntimeError("job_kv_retry_key_forbidden")
        count = self.calls.get(key, 0) + 1
        self.calls[key] = count
        label = "result" if key.startswith("result:") else "cache"
        stage = f"{self.prefix}_kv_{label}"
        if count == 1:
            # 値も所有権も検査してから、実KVへ送らず失敗させる。
            await self.kv.get(key)
            self.values[key] = value
            self.owner["stages"][f"{stage}_before"] = 200
            raise RuntimeError("e2e_kv_before_write")
        if value != self.values[key] or count > 3:
            raise RuntimeError("job_kv_retry_value_changed")
        await self.kv.put(key, value)
        if count == 2:
            self.owner["stages"][f"{stage}_after"] = 200
            raise RuntimeError("e2e_kv_after_write")
        self.owner["stages"][f"{stage}_recovered"] = 200

    def check(self):
        if self.calls != dict.fromkeys(self.keys, 3):
            raise RuntimeError("job_kv_retry_not_observed")


def verified(owner, prefix):
    return not owner.get("kv_retry") or all(
        owner["stages"].get(f"{prefix}_kv_{key}_{step}") == 200
        for key in ("cache", "result") for step in ("before", "after", "recovered")
    )
