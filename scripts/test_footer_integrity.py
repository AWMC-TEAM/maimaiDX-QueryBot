#!/usr/bin/env python3
"""Self-contained regression test for the signed footer manifest."""

from __future__ import annotations

import base64
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "libraries" / "maimaidx_attribution.py"
SPEC = importlib.util.spec_from_file_location("maimaidx_attribution_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> None:
    assert MODULE.ATTRIBUTION["upstream_url"] == "https://github.com/Yuri-YuzuChaN/maimaiDX"
    assert MODULE.short_footer("Milk MAXXXX") == (
        "QQ Group 1072033605 | Milk MAXXXX Bot Made By AWMC TEAM"
    )
    payload = bytearray(base64.b64decode(MODULE._SEALED_PAYLOAD_B64))
    payload[-1] ^= 1
    try:
        MODULE._verify_rsa_signature(bytes(payload), base64.b64decode(MODULE._SIGNATURE_B64))
    except MODULE.AttributionIntegrityError:
        pass
    else:
        raise AssertionError("tampered attribution unexpectedly passed verification")
    print("footer attribution signature: OK")


if __name__ == "__main__":
    main()
