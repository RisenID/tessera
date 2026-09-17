#!/usr/bin/env python3
"""Passcode detection against the messages people actually get."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tessera.core import otp  # noqa: E402

#: (text, expected code); None when nothing in the text is a passcode.
CASES = [
    ("123456 is your OTP for login. Do not share it with anyone.", "123456"),
    ("Your OTP is 123456. Valid for 10 mins.", "123456"),
    ("Your OTP is 123456.", "123456"),
    ("OTP: 123456", "123456"),
    ("Use 123456 as OTP to verify your mobile number", "123456"),
    ("<#> 123456 is your verification code. abcDEFghij", "123456"),
    ("G-123456 is your Google verification code.", "123456"),
    ("123456 is your WhatsApp code", "123456"),
    ("12345678 is your Instagram code", "12345678"),
    ("Your verification code is 123 456", "123456"),
    ("Your code: 1234", "1234"),
    ("Your Uber code is 1234. Never share this code.", "1234"),
    ("Enter 8842 to continue signing in", "8842"),
    ("Aapka OTP 123456 hai. Kisi ke saath share na karein.", "123456"),
    ("OTP for Rs.500.00 at Swiggy is 123456. Valid till 10:35 PM", "123456"),
    ("Dear Customer, 123456 is your one time password (OTP) for txn of INR 2,499.00 "
     "on your card ending 1234.", "123456"),
    ("Your one-time passcode is 123456 (expires in 5 min) - Ref 987654", "123456"),
    ("Tap to copy 123456", "123456"),
    # The keyword far ahead of the code, and a full stop after it.
    ("Dear Customer, The OTP for Reference No 1x2y3z is 123456. Please complete your "
     "Funds Transfer Transaction to Beneficiary Test for amount 1000.00 INR with "
     "the OTP.-Bank of Testing", "123456"),
    # Not passcodes.
    ("Order 12345678 shipped. Track at 10:30", None),
    ("Your bill of Rs. 2345 is due on 2026-09-20", None),
    ("Meeting at 1400 in room 2231", None),
    ("Use code SAVE20 for 20% off until 2026", None),
    ("Sensitive notification content hidden", None),
]


def main() -> int:
    failed = 0
    for text, expected in CASES:
        found = otp.find_code(text, "Messages")
        code = found.code if found else None
        if code != expected:
            failed += 1
            print(f"FAIL expected {expected!r}, got {code!r} (score {found.score if found else 0}): {text}")
    if failed:
        print(f"{failed} of {len(CASES)} passcode checks failed")
        return 1
    print(f"all {len(CASES)} passcode checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
