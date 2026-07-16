"""高负载滚动窗口计数回归测试（无需启动 NoneBot）。"""

import importlib.util
from pathlib import Path


path = Path(__file__).resolve().parent.parent / "libraries" / "maimaidx_request_rate.py"
spec = importlib.util.spec_from_file_location("maimaidx_request_rate_test", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

meter = module.RollingRequestMeter()
assert meter.record("same", now=0, window_seconds=60) == 1
assert meter.record("same", now=1, window_seconds=60) is None
for index in range(2, 31):
    assert meter.record(str(index), now=float(index), window_seconds=60) == index
assert meter.record("31", now=31, window_seconds=60) == 31
# t=1 的请求仍在窗口内；t=0 已过期，因此窗口内仍是 31 个请求。
assert meter.record("later", now=61, window_seconds=60) == 31

print("busy surcharge meter tests: ok")
