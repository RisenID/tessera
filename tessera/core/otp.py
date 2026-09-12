"""Pull one-time passcodes out of notification text.

Notifications carrying a login code are formatted inconsistently by every
sender, so this is deliberately heuristic: find digit-ish runs that could be a
code, then score them on the words around them. Only candidates that clear a
threshold are offered, because a wrong code silently pasted into a login form
is worse than showing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Words that indicate the message is about a passcode at all. Without one of
#: these, no number in the text is treated as a code.
KEYWORDS = (
    "one-time", "one time", "onetime", "otp", "passcode", "pass code",
    "verification", "verification code", "verify", "confirmation code",
    "security code", "auth code", "authentication", "2fa", "two-factor",
    "login code", "log in code", "sign-in code", "sign in code",
    "access code", "pin", "code",
    # Auth context without the word "code" -- "Enter 8842 to continue signing in".
    "signing in", "sign in", "sign-in", "logging in", "log in", "authenticate",
)

#: Phrases that usually sit immediately before or after the code itself.
_STRONG_BEFORE = re.compile(
    r"(?:code|otp|pin|passcode|password)\W{0,4}(?:is|:|=)?\W{0,4}$",
    re.IGNORECASE,
)
_STRONG_AFTER = re.compile(
    r"^\W{0,4}(?:is\s+your|as\s+your)?\W{0,4}(?:is\s+the\s+)?"
    r"(?:code|otp|pin|passcode|verification)",
    re.IGNORECASE,
)

#: Imperative verbs that commonly introduce a code without naming it as one,
#: as in "Use 12345 to verify your account".
_ENTER_BEFORE = re.compile(r"\b(?:use|enter|type|input|submit)\s+$", re.IGNORECASE)

#: A run of digits, or an alphanumeric block that contains at least one digit.
_CANDIDATE = re.compile(r"\b(?=[A-Z0-9-]*\d)([0-9]{4,8}|[A-Z0-9]{5,8}|\d{3}-\d{3})\b")

#: Contexts that mean a number is definitely not a passcode.
_MONEY = re.compile(r"[$£€¥₹]\s*$")
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

    @property
    def confident(self) -> bool:
        return self.score >= THRESHOLD + 3


def _looks_like_year(token: str) -> bool:
    return bool(_ORDINAL_DATE.fullmatch(token))


def _score(token: str, text: str, start: int, end: int, app: str) -> int:
    before = text[max(0, start - 32) : start]
    after = text[end : end + 32]
    lowered = text.lower()
    score = 0

    if any(keyword in lowered for keyword in KEYWORDS):
        score += 2
    if any(marker in app.lower() for marker in _CODE_APPS):
        score += 3

    if _STRONG_BEFORE.search(before):
        score += 4
    if _STRONG_AFTER.search(after):
        score += 4
    if _ENTER_BEFORE.search(before):
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
    # Part of a longer number, e.g. an order id or phone number.
    if start > 0 and (text[start - 1].isdigit() or text[start - 1] in "+."):
        score -= 4
    if end < len(text) and (text[end].isdigit() or text[end] == "."):
        score -= 4
    # "Do not share" is a strong signal the number really is a secret.
    if "not share" in lowered or "never share" in lowered or "do not give" in lowered:
        score += 2
    return score


def find_codes(text: str, app: str = "") -> list[OtpMatch]:
    """Every plausible passcode in *text*, best first."""
    if not text:
        return []
    matches: list[OtpMatch] = []
    seen: set[str] = set()
    for match in _CANDIDATE.finditer(text):
        token = match.group(1)
        if token in seen:
            continue
        seen.add(token)
        score = _score(token, text, match.start(1), match.end(1), app)
        if score >= THRESHOLD:
            context = text[max(0, match.start(1) - 40) : match.end(1) + 40].strip()
            matches.append(OtpMatch(token, score, app, context))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def find_code(text: str, app: str = "") -> OtpMatch | None:
    """The single best passcode in *text*, or None."""
    found = find_codes(text, app)
    return found[0] if found else None
