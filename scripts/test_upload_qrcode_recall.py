"""maiu 二维码撤回不得阻塞上传流程的回归检查。"""

from pathlib import Path


source = (Path(__file__).parents[1] / "command" / "mai_account.py").read_text()

helper_start = source.index("async def _recall_qrcode_message")
helper_end = source.index("\n\ndef ", helper_start)
helper_source = source[helper_start:helper_end]

assert "asyncio.wait_for(" in helper_source
assert "bot.delete_msg(message_id=event.message_id)" in helper_source
assert "return _RECALL_FAILED_NOTICE" in helper_source

upload_start = source.index("@upload_fish.handle()")
upload_end = source.index("\n\n@account_ping.handle()", upload_start)
upload_source = source[upload_start:upload_end]

assert upload_source.count("await _recall_qrcode_message(bot, event)") == 2
assert "await bot.delete_msg(message_id=event.message_id)" not in upload_source
assert upload_source.count('recall_notice = ""') == 2

print("upload qrcode recall timeout tests: ok")
