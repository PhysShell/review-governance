"""Tests for the durable credential store's single-writer discipline.

The store runs against a temporary GOVERNOR_CONFIG_DIR — never the real
credential directory — and no real tokens appear anywhere in this file.
"""
import importlib
import json
import os
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVERNOR_CONFIG_DIR", str(tmp_path))
    import creds
    importlib.reload(creds)
    (tmp_path / "user-token.json").write_text(json.dumps({
        "access_token": "ghu_fake_access_gen0",
        "refresh_token": "ghr_fake_refresh_gen0",
        "token_type": "bearer", "expires_in": 28800,
        "refresh_token_expires_in": 15897600,
        "obtained_at": "2026-01-01T00:00:00Z",
        "obtained_via": "github_app_device_flow",
    }))
    creds.initialize_from_legacy("G0")
    yield creds
    importlib.reload(creds)


def test_public_state_never_exposes_token_values(store):
    state = store.public_state()
    serialized = json.dumps(state)
    assert "ghu_fake_access_gen0" not in serialized
    assert "ghr_fake_refresh_gen0" not in serialized
    assert state["generations"][0]["access_prefix_class"] == "ghu_"
    assert len(state["generations"][0]["access_fingerprint"]) == 16


def test_fingerprint_is_sha256_prefix_and_not_reversible(store):
    fp = store.fingerprint("ghu_fake_access_gen0")
    assert fp == store.fingerprint("ghu_fake_access_gen0")
    assert fp != store.fingerprint("ghu_fake_access_gen1")
    assert "fake" not in fp and len(fp) == 16


def test_commit_advances_the_generation(store):
    public = store.commit_new_generation(
        {"access_token": "ghu_fake_access_gen1",
         "refresh_token": "ghr_fake_refresh_gen1",
         "token_type": "bearer", "expires_in": 28800,
         "refresh_token_expires_in": 15897600},
        from_generation=0, label="G1",
        obtained_via="github_app_refresh_grant",
        obtained_at="2026-01-01T01:00:00Z")
    assert public["generation"] == 1
    assert store.current_generation() == 1
    assert len(store.public_state()["generations"]) == 2


def test_race_loser_cannot_clobber_the_winner(store):
    """Both workers start from generation 0; the first commit wins and the
    second is refused, so the durable chain never regresses."""
    store.commit_new_generation(
        {"access_token": "ghu_winner", "refresh_token": "ghr_winner"},
        from_generation=0, label="G1-winner",
        obtained_via="github_app_refresh_grant", obtained_at="t1")

    with pytest.raises(store.GenerationRaceLost) as excinfo:
        store.commit_new_generation(
            {"access_token": "ghu_loser", "refresh_token": "ghr_loser"},
            from_generation=0, label="G1-loser",
            obtained_via="github_app_refresh_grant", obtained_at="t2")

    assert excinfo.value.on_disk == 1
    assert excinfo.value.attempted == 0
    assert store.current_generation() == 1
    assert store.fingerprint("ghu_winner") == \
        store.public_state()["generations"][1]["access_fingerprint"]


def test_store_file_is_owner_only(store):
    store.commit_new_generation(
        {"access_token": "ghu_x", "refresh_token": "ghr_x"},
        from_generation=0, label="G1",
        obtained_via="github_app_refresh_grant", obtained_at="t")
    mode = os.stat(store.STORE_PATH).st_mode & 0o777
    assert mode == 0o600
