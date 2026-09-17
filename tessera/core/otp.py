"""Pull one-time passcodes out of notification text."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass

#: Words that indicate the message is about a passcode at all. Without one of
#: these, no number in the text is treated as a code.
KEYWORDS = (
    "one-time", "one time", "onetime", "otp", "passcode", "pass code",
    "verification", "verification code", "verify", "confirmation code",
    "security code", "auth code", "authentication", "2fa", "two-factor",
    "login code", "log in code", "sign-in code", "sign in code",
    "access code", "pin", "code", "tap to copy", "copy code",
    # Auth context without the word "code" -- "Enter 8842 to continue signing in".
    "signing in", "sign in", "sign-in", "logging in", "log in", "authenticate",
)

#: Whole words only: "pin" is not in "shopping", nor "code" in "barcode".
_KEYWORD = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in KEYWORDS) + r")\b", re.IGNORECASE
)

#: Marketing that carries codes which are not passcodes.
_PROMO = re.compile(r"\b(?:promo|coupon|discount|voucher|referral|cashback|\d+\s*% off)", re.IGNORECASE)

#: Phrases that usually sit immediately before or after the code itself.
_STRONG_BEFORE = re.compile(
    r"(?:code|otp|pin|passcode|password)\W{0,4}(?:is|:|=)?\W{0,4}$",
    re.IGNORECASE,
)
#: Up to two words may sit between: "is your Instagram code", "is the OTP".
_STRONG_AFTER = re.compile(
    r"^\W{0,4}(?:is\s+your|as\s+your|is\s+the)?\W{0,4}(?:[A-Za-z][\w&']*\s+){0,2}"
    r"(?:code|otp|pin|passcode|verification)",
    re.IGNORECASE,
)

#: Imperative verbs that commonly introduce a code without naming it as one,
#: as in "Use 12345 to verify your account".
_ENTER_BEFORE = re.compile(r"\b(?:use|enter|type|input|submit)\s+$", re.IGNORECASE)

#: "The OTP for Reference No 9b59684b69ac62 is 482913": the keyword sits well
#: before the code, but "is" right before it still says which number is meant.
_IS_BEFORE = re.compile(r"\bis\W{0,3}$", re.IGNORECASE)

#: A run of digits, an alphanumeric block that contains at least one digit, or
#: two groups of three ("123-456", "123 456").
_CANDIDATE = re.compile(r"\b(?=[A-Z0-9-]*\d)([0-9]{4,8}|[A-Z0-9]{5,8}|\d{3}[ -]\d{3})\b")

#: Contexts that mean a number is definitely not a passcode.
_MONEY = re.compile(r"(?:[$£€¥₹]|\b(?:rs|inr|usd|eur|gbp|aud|cad))\.?\s*$", re.IGNORECASE)
_TIME = re.compile(r"\d:\d")
_ORDINAL_DATE = re.compile(r"\b(?:19|20)\d{2}\b")

#: Apps whose notifications are essentially always codes.
_CODE_APPS = ("authenticator", "duo", "authy", "2fa", "okta")

#: Score a candidate must reach before it is shown.
THRESHOLD = 4


@dataclass(frozen=True)
class OtpMatch:
    """A passcode found in a notification."""

    code: str
    score: int
    app: str = ""
    context: str = ""


def _looks_like_year(token: str) -> bool:
    return bool(_ORDINAL_DATE.fullmatch(token))


def _text_score(text: str, app: str) -> int:
    """The part of the score every candidate in *text* shares."""
    score = 0
    if _KEYWORD.search(text):
        score += 2
    if _PROMO.search(text):
        score -= 5
    if any(marker in app.lower() for marker in _CODE_APPS):
        score += 3
    # "Do not share" is a strong signal the number really is a secret.
    lowered = text.lower()
    if "not share" in lowered or "never share" in lowered or "do not give" in lowered:
        score += 2
    return score


def _score(token: str, text: str, start: int, end: int, base: int) -> int:
    before = text[max(0, start - 32) : start]
    after = text[end : end + 32]
    score = base

    if _STRONG_BEFORE.search(before):
        score += 4
    if _STRONG_AFTER.search(after):
        score += 4
    if _ENTER_BEFORE.search(before):
        score += 2
    elif base > 0 and _IS_BEFORE.search(before) and not _STRONG_BEFORE.search(before):
        score += 2

    digits = sum(character.isdigit() for character in token)
    if digits == 6:
        score += 2          # by far the most common length
    elif digits in (4, 5, 7, 8):
        score += 1

    # -- disqualifiers ------------------------------------------------------
    if _looks_like_year(token):
        score -= 4
    if _MONEY.search(before):
        score -= 5
    if _TIME.search(text[max(0, start - 2) : end + 2]):
        score -= 4
    # Part of a longer number, e.g. an order id, phone number or decimal. A
    # full stop only counts with a digit on its other side: "is 123456." is
    # the end of a sentence, not a fraction.
    if start > 0 and (
        text[start - 1].isdigit() or text[start - 1] == "+"
        or (text[start - 1] == "." and start > 1 and text[start - 2].isdigit())
    ):
        score -= 4
    if end < len(text) and (
        text[end].isdigit()
        or (text[end] == "." and end + 1 < len(text) and text[end + 1].isdigit())
    ):
        score -= 4
    return score


@functools.lru_cache(maxsize=256)
def find_codes(text: str, app: str = "") -> tuple[OtpMatch, ...]:
    """Every plausible passcode in *text*, best first."""
    if not text:
        return ()
    matches: list[OtpMatch] = []
    seen: set[str] = set()
    base = _text_score(text, app)
    for match in _CANDIDATE.finditer(text):
        token = match.group(1)
        if token in seen:
            continue
        seen.add(token)
        score = _score(token, text, match.start(1), match.end(1), base)
        if score >= THRESHOLD:
            context = text[max(0, match.start(1) - 40) : match.end(1) + 40].strip()
            # "123 456" is typed as 123456.
            code = token.replace(" ", "").replace("-", "")
            matches.append(OtpMatch(code, score, app, context))
    matches.sort(key=lambda m: m.score, reverse=True)
    # A tuple, so the cached result cannot be changed by a caller.
    return tuple(matches)


def find_code(text: str, app: str = "") -> OtpMatch | None:
    """The single best passcode in *text*, or None."""
    found = find_codes(text, app)
    return found[0] if found else None
