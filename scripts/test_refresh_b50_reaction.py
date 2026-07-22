"""刷新 b50 开始拉取前应使用表情静默确认。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
source = (ROOT / "command" / "mai_score.py").read_text(encoding="utf-8")

handler_start = source.index("@refresh_b50.handle()")
handler_end = source.index("\n\n@best_all50.handle()", handler_start)
handler = source[handler_start:handler_end]

assert "from ..libraries.maimaidx_reaction import react_processing" in source
assert "bot: Bot" in handler
assert handler.count("await react_processing(bot, event)") == 1
resolve_pos = handler.index("qqid = resolve_score_qqid(")
reaction_pos = handler.index("await react_processing(bot, event)")
fetch_pos = handler.index("await get_user_records(")
assert resolve_pos < reaction_pos < fetch_pos

print("refresh b50 reaction tests: ok")
