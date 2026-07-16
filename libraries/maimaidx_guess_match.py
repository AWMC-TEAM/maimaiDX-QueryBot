import re
from typing import List, Optional

_TAIL_PUNCT = '!！?？.。~～…·,，、:：;；-—'
_LATIN_GUESS_RE = re.compile(r'[a-z0-9]+')


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


def _is_relaxed_latin_match(guess: str, answer: str) -> bool:
    """Allow a Latin fragment; only a 3-char fragment may contain one typo."""
    if (
        len(guess) < 3
        or len(guess) > len(answer)
        or not _LATIN_GUESS_RE.fullmatch(guess)
        or not _LATIN_GUESS_RE.fullmatch(answer)
    ):
        return False
    if guess in answer:
        return True

    # Keep typo tolerance narrowly scoped to the requested short-fragment case
    # (for example, "man" matching the "men" in "goodmen"). Longer guesses
    # must be exact so misspelled full titles do not become valid answers.
    if len(guess) != 3:
        return False

    # Compare against same-length windows so a short guess can match part of a
    # longer title. Stop as soon as more than one character differs.
    for start in range(len(answer) - len(guess) + 1):
        differences = 0
        for left, right in zip(guess, answer[start:start + len(guess)]):
            if left != right:
                differences += 1
                if differences > 1:
                    break
        if differences <= 1:
            return True
    return False


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
        if not ans_s.isdigit() and _is_relaxed_latin_match(norm_guess, norm_ans):
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
