"""Per-account operation guard for account and upload workflows."""

from __future__ import annotations


_active_account_operations: set[str] = set()


def try_begin_account_operation(user_id: object) -> bool:
    """Atomically claim an account operation in the current event loop."""
    key = str(user_id)
    if key in _active_account_operations:
        return False
    _active_account_operations.add(key)
    return True


def finish_account_operation(user_id: object) -> None:
    _active_account_operations.discard(str(user_id))
