"""猜歌文本匹配回归测试（不依赖 NoneBot）。"""

from libraries.maimaidx_guess_match import match_guess_answer


# 普通猜歌继续保留历史行为：三个拉丁字符允许错一个。
assert match_guess_answer("the", ["SHE IS HERE"])

# 开字母关闭错字容忍后，只能命中真实存在的连续内容。
assert not match_guess_answer("the", ["SHE IS HERE", "tie"], allow_latin_typo=False)
assert match_guess_answer("the", ["THE BRIGHT SIDE"], allow_latin_typo=False)
assert match_guess_answer("the", ["In The End"], allow_latin_typo=False)
assert match_guess_answer("THE", ["the"], allow_latin_typo=False)
assert not match_guess_answer("th", ["In The End"], allow_latin_typo=False)

print("guess match tests passed")
