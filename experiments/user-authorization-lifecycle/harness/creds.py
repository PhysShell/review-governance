"""Durable, generation-aware credential store for the A1c lifecycle.

The store is the single source of truth about which credential generation
is current. Refresh is serialized through an exclusive file lock, and a
write only lands if the generation on disk is still the one the caller
started from — a compare-and-swap, so a second worker that lost the race
cannot clobber the winner's generation.

Token values never leave this module: callers get a generation record with
fingerprints, and pass generation numbers around. `SHA-256(token)[:16]` is
the only representation allowed in evidence.
"""
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
STORE_PATH = CONFIG_DIR / "user-credentials.json"
LOCK_PATH = CONFIG_DIR / "user-credentials.lock"
LEGACY_TOKEN_PATH = CONFIG_DIR / "user-token.json"


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16] if token else None


def _public(record: dict) -> dict:
    """Everything about a generation that is safe to record as evidence."""
    return {
        "generation": record["generation"],
        "label": record.get("label"),
        "access_fingerprint": fingerprint(record.get("access_token")),
        "refresh_fingerprint": fingerprint(record.get("refresh_token")),
        "access_prefix_class": (record.get("access_token") or "")[:4] or None,
        "refresh_prefix_class": (record.get("refresh_token") or "")[:4] or None,
        "token_type": record.get("token_type"),
        "expires_in": record.get("expires_in"),
        "refresh_token_expires_in": record.get("refresh_token_expires_in"),
        "obtained_at": record.get("obtained_at"),
        "obtained_via": record.get("obtained_via"),
    }


@contextmanager
def exclusive():
    """Single-writer lock around the durable store."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load() -> dict:
    return json.loads(STORE_PATH.read_text())


def current() -> dict:
    return load()["current"]


def current_generation() -> int:
    return load()["current"]["generation"]


def public_state() -> dict:
    store = load()
    return {
        "current_generation": store["current"]["generation"],
        "generations": [_public(g) for g in store["history"]],
    }


def _write(store: dict) -> None:
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE_PATH)          # atomic durable commit


def initialize_from_legacy(label: str = "G0") -> dict:
    """Adopt the device-flow token already on disk as generation 0."""
    with exclusive():
        if STORE_PATH.exists():
            return public_state()
        legacy = json.loads(LEGACY_TOKEN_PATH.read_text())
        record = {
            "generation": 0, "label": label,
            "access_token": legacy["access_token"],
            "refresh_token": legacy.get("refresh_token"),
            "token_type": legacy.get("token_type"),
            "expires_in": legacy.get("expires_in"),
            "refresh_token_expires_in": legacy.get("refresh_token_expires_in"),
            "obtained_at": legacy.get("obtained_at"),
            "obtained_via": legacy.get("obtained_via"),
        }
        _write({"current": record, "history": [record]})
        return public_state()


def commit_new_generation(payload: dict, from_generation: int, *,
                          label: str, obtained_via: str, obtained_at: str) -> dict:
    """CAS: land a new generation only if the store is still at
    `from_generation`. Raises if another writer already rotated."""
    with exclusive():
        store = load()
        on_disk = store["current"]["generation"]
        if on_disk != from_generation:
            raise GenerationRaceLost(on_disk, from_generation)
        record = {
            "generation": on_disk + 1, "label": label,
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token"),
            "token_type": payload.get("token_type"),
            "expires_in": payload.get("expires_in"),
            "refresh_token_expires_in": payload.get("refresh_token_expires_in"),
            "obtained_at": obtained_at, "obtained_via": obtained_via,
        }
        store["current"] = record
        store["history"].append(record)
        _write(store)
        return _public(record)


def generation(number: int) -> dict:
    for record in load()["history"]:
        if record["generation"] == number:
            return record
    raise KeyError(f"no such generation: {number}")


class GenerationRaceLost(Exception):
    def __init__(self, on_disk, attempted):
        super().__init__(f"store already at generation {on_disk}, "
                         f"attempted from {attempted}")
        self.on_disk = on_disk
        self.attempted = attempted
