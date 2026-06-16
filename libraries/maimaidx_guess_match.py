import re
from typing import List, Optional

_TAIL_PUNCT = '!！?？.。~～…·,，、:：;；-—'


def normalize_guess_text(text: str, strictness: int = 1) -> str:
    text = str(text).strip().lower()
    if not text:
        return ''
    if strictness >= 1:
        text = text.strip(_TAIL_PUNCT)
    if strictness >= 2:
        text = re.sub(
            r'[\s!！?？.。~～…·,，、:：;；—\'"“”‘’「」【】()（）\[\]-]+',
            '',
            text,
        )
    return text


def match_guess_answer(
    guess_text: str,
    answers: List[str],
    *,
    pic_difficulty: Optional[int] = None,
) -> bool:
    raw = guess_text.strip()
    if not raw:
        return False
    if raw.lower() in [str(a).lower() for a in answers]:
        return True

    strictness = pic_difficulty if pic_difficulty is not None else 2
    norm_guess = normalize_guess_text(raw, strictness)
    if not norm_guess:
        return False

    for ans in answers:
        ans_s = str(ans)
        if ans_s.isdigit() and norm_guess == ans_s.lower():
            return True
        norm_ans = normalize_guess_text(ans_s, strictness)
        if not norm_ans:
            continue
        if norm_guess == norm_ans:
            return True
        if (
            pic_difficulty is not None
            and pic_difficulty >= 3
            and len(norm_guess) >= 2
            and len(norm_ans) >= 2
            and (norm_guess in norm_ans or norm_ans in norm_guess)
        ):
            return True
    return False
