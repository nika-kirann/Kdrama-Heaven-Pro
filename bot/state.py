"""In-memory wizard state for admin conversations."""

from __future__ import annotations

PENDING: dict[int, dict] = {}


def set_state(user_id: int, **data) -> None:
    PENDING[int(user_id)] = data


def get_state(user_id: int) -> dict | None:
    return PENDING.get(int(user_id))


def pop_state(user_id: int) -> dict | None:
    return PENDING.pop(int(user_id), None)
