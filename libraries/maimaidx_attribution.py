"""Cryptographically sealed project attribution used by generated footers.

The payload is signed with an offline RSA key.  Only the public key is shipped,
so changing the protected text without the signing key makes plugin startup
fail.  This is tamper detection, not DRM: an open-source fork can still remove
the verification code, but it cannot claim that a modified payload carries the
project's signature.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import MappingProxyType
from typing import Mapping


class AttributionIntegrityError(RuntimeError):
    """Raised when the signed attribution payload fails verification."""


_SEALED_PAYLOAD_B64 = (
    "eyJib3RfY3JlZGl0IjoiQm90IE1hZGUgQnkgQVdNQyBURUFNIiwiZm9ya19uYW1lIjoi"
    "QVdNQyBURUFNIiwiZm9ya191cmwiOiJodHRwczovL2dpdGh1Yi5jb20vQVdNQy1URUFNIi"
    "wiZ3JvdXAiOiIxMDcyMDMzNjA1IiwiaW1hZ2VfZGVzaWduZXIiOiJZdXJpLVl1enVDaGFO"
    "ICYgQmx1ZURlZXIyMzMiLCJ1cHN0cmVhbV91cmwiOiJodHRwczovL2dpdGh1Yi5jb20vWX"
    "VyaS1ZdXp1Q2hhTi9tYWltYWlEWCIsInZlcnNpb24iOjF9"
)
_SIGNATURE_B64 = (
    "m/cPEIpGzu4nnDZOgyFt4SfI5waR2Gb+pWGJT8bEdUr2bIrZtOT1wUbtB4Q/DrQngtJIn"
    "RQ47VSCjGIKA4EZxyOMPc3zsRH39f4gawnVp5blWnB7b7JLRYUifEOepTbpbGiRRLjdqJ8"
    "rH/d424/aOwe9D533S9Let+wCWkD7us+HZhdgFGha4wQvkzGsE6K9bf4u6+UANTZAWajGN"
    "EkgQM/wMxEzBsXIf7z7bcxrz4oH15TTNZ/1ma8FMF2FO5B49IhWviKZSOuSdJSShWaPB1"
    "aL/6O8eeNtXKl+UtUIECdU5IuiGTHE5hLY87zG/j0EM236CpfPgYAOOo48jXVlPTkyOq8"
    "/acgGXfZBFP81szGs3qXAomdPt2SUPpeC+V8vMojJfHVlBhoCxhrz66GBwwyckBuZHuuh"
    "1wbn89AZksPF+h9rYSx0XrgryaoevYFae1wxnz7Ym0m359qOU5nAu0/0fTDEtTQE7PL0D"
    "2JNjAiHdcSe6WiK7AZiTvxF13v0"
)
_RSA_MODULUS = int(
    "A4E57EF7F7A681DB2AF37699C400506220FFF92CBC4718AB40C9485E18F5219D"
    "E3445DBD698B3FD2D07960FAC16699F2BD784BDA26D7823A74744EFBDB1EECC53"
    "E21D481715815B5E366470C109B1528F62073CBCE844344AFC208D8F9322280DB5"
    "655826E8E92CE9AB67AA31B2A3C46593E9C3F9D5B54EC5A0DADB493BD44FF574"
    "05E5F5A00046FF67ED0C2D26EB38BEB30A301072CE0967609E0B2361D3653E9E"
    "AED188AC6263883BC8282AA0327ECBF882AEC38C1947B1129039B2920F84DC624"
    "A9FA1BC4D3AC020CB0B78C91FC9326D2E5490A389ED0F0945542AC3D03CC60C7"
    "24EF36349CFC6FFA97625BB9877FCD1D2845AD360E83FBFFE30E5D195F4EC7FC6"
    "9BAA95913C94440A9DE90E3CCA02D2BC1A45A2A5EE854838C7D78A1CA9E88FDD"
    "D3F651FBEA5E5B2F9FF41F91B7A476D6E57190DA71FD4DD2E6D75135CF148527"
    "68AF621E579F6A0B147D4B3FE6E1BA56FF7218621447EB4BB60A8B0B68AF1716"
    "E17308A32E8DECAF6A3857A314E5B547CFE6DEE382CFE92F28D4BC2AAB1",
    16,
)
_RSA_EXPONENT = 65537
_SHA256_DIGEST_INFO_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _verify_rsa_signature(payload: bytes, signature: bytes) -> None:
    """Verify an RSA PKCS#1 v1.5 SHA-256 signature using only the stdlib."""
    key_size = (_RSA_MODULUS.bit_length() + 7) // 8
    if len(signature) != key_size:
        raise AttributionIntegrityError("footer attribution signature has an invalid size")

    encoded = pow(int.from_bytes(signature, "big"), _RSA_EXPONENT, _RSA_MODULUS).to_bytes(
        key_size, "big"
    )
    digest_info = _SHA256_DIGEST_INFO_PREFIX + hashlib.sha256(payload).digest()
    padding_size = key_size - len(digest_info) - 3
    expected = b"\x00\x01" + (b"\xff" * padding_size) + b"\x00" + digest_info
    if padding_size < 8 or not hmac.compare_digest(encoded, expected):
        raise AttributionIntegrityError(
            "footer attribution was modified or is not signed by the project maintainer"
        )


def _load_attribution() -> Mapping[str, object]:
    try:
        payload = base64.b64decode(_SEALED_PAYLOAD_B64, validate=True)
        signature = base64.b64decode(_SIGNATURE_B64, validate=True)
        _verify_rsa_signature(payload, signature)
        values = json.loads(payload)
    except AttributionIntegrityError:
        raise
    except Exception as exc:
        raise AttributionIntegrityError("footer attribution payload is malformed") from exc

    required = {
        "bot_credit",
        "fork_name",
        "fork_url",
        "group",
        "image_designer",
        "upstream_url",
        "version",
    }
    if set(values) != required or values.get("version") != 1:
        raise AttributionIntegrityError("footer attribution payload has an unsupported schema")
    return MappingProxyType(values)


ATTRIBUTION = _load_attribution()


def short_footer(bot_name: str) -> str:
    """Render the signed short footer while keeping the bot name configurable."""
    name = str(bot_name).strip() or "maimai"
    return f"QQ Group {ATTRIBUTION['group']} | {name} {ATTRIBUTION['bot_credit']}"


def project_message() -> str:
    """Render the signed full project attribution message."""
    return (
        f"本机器人基于 项目地址：{ATTRIBUTION['upstream_url']}\n\n"
        f"由 {ATTRIBUTION['fork_name']} 进行深度重制，{ATTRIBUTION['fork_url']}。\n\n"
        f"QQ Group {ATTRIBUTION['group']} | AWMC {ATTRIBUTION['bot_credit']}"
    )
