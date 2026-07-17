"""B50 风险预警触发与猜曲绘别名回归测试。"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
score_source = (ROOT / "command" / "mai_score.py").read_text(encoding="utf-8")
guess_source = (ROOT / "command" / "mai_guess.py").read_text(encoding="utf-8")

risk_pattern = re.compile(
    r"^\s*(?:b50\s*(?:风险(?:预警)?|预警)|风险预警)\s*$",
    re.IGNORECASE,
)
for command in (
    "b50风险",
    "B50风险",
    "b50风险预警",
    "B50 风险预警",
    "b50预警",
    "风险预警",
):
    assert risk_pattern.fullmatch(command), command

risk_registration = score_source[
    score_source.index("b50_risk_warning ="):
    score_source.index("head_to_head =")
]
assert "priority=0" in risk_registration
assert "block=True" in risk_registration
for alias in ("猜封面", "猜歌封面", "猜曲图", "猜歌图", "猜曲绘图"):
    assert alias in guess_source

pic_pattern = re.compile(
    r"^(?:猜曲绘|猜封面|猜歌封面|猜曲图|猜歌图|猜曲绘图)\s*([1-4])?\s*$"
)
for command in (
    "猜曲绘",
    "猜曲绘4",
    "猜曲绘 3",
    "猜封面2",
    "猜歌图 1",
):
    assert pic_pattern.fullmatch(command), command
assert pic_pattern.fullmatch("猜曲绘5") is None
assert pic_pattern.fullmatch("猜曲绘0") is None

print("risk and guess alias tests: ok")
