"""二维码动态预计耗时回归测试（无需启动 NoneBot）。"""

import importlib.util
import sys
import tempfile
from pathlib import Path


root = Path(__file__).resolve().parent.parent
path = root / "libraries" / "maimaidx_processing_time.py"
spec = importlib.util.spec_from_file_location("processing_time_test", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.auto_qrcode_workflow_key(pc=True, fish=True, lxns=False) == (
    "auto_qrcode:pc+fish"
)
assert module.auto_qrcode_fallback_seconds(pc=True, fish=True, lxns=True) == 71
assert "首次预计约 71 秒" in module.format_processing_estimate(71, 0)

with tempfile.TemporaryDirectory() as td:
    estimator = module.ProcessingTimeEstimator(
        Path(td) / "timing.db", sample_limit=3
    )
    assert estimator.estimate("flow", fallback_seconds=40) == (40, 0)
    estimator.record("flow", 10)
    estimator.record("flow", 20)
    estimator.record("flow", 30)
    estimator.record("flow", 40)
    # sample_limit=3，仅保留 20/30/40，平均 30。
    assert estimator.estimate("flow", fallback_seconds=40) == (30, 3)

playcount_source = (root / "command" / "mai_playcount.py").read_text(
    encoding="utf-8"
)
assert "processing_time_estimator.estimate(" in playcount_source
assert "processing_time_estimator.record(" in playcount_source
assert "Bot 无法撤回原凭据消息，请立即手动撤回" in playcount_source

print("processing time estimator tests: ok")
