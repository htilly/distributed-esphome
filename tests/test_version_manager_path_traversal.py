"""Regression tests for finding O-002 (2026-08-27 security review).

Attacker chain this closes: ``VersionManager._venv_path()`` used to join a
caller-supplied ESPHome version string directly onto ``VERSIONS_BASE`` with
no validation, and ``_install()`` unconditionally ``shutil.rmtree()``s
whatever that resolves to before reinstalling. A version string reachable
via ``POST /ui/api/esphome-version`` or the per-target pin endpoint (both of
which previously rejected only the empty string) could walk outside
``VERSIONS_BASE`` with a value like ``../../../data`` — and because the
add-on container runs as root with ``/config`` mounted read-write, a single
crafted request could recursively delete the user's Home Assistant
configuration tree.

These tests exercise the fix at the actual sink (``_venv_path``, the single
choke point every install/lookup/eviction path routes through) rather than
mocking it out, so a regression that removes or narrows the check fails
here even if a caller upstream (e.g. an ingress handler) forgets its own
validation.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from version_manager import VersionManager, _validate_version


# ---------------------------------------------------------------------------
# _validate_version — the standalone check, also reused by ui_api.py's
# ingress handlers (see set_esphome_version_handler / pin_target_version).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "version",
    [
        "2026.3.1",
        "2026.3.0b3",
        "2026.3.0.dev20260521",
        "2026.7",
        "dev",
    ],
)
def test_validate_version_accepts_real_esphome_release_shapes(version):
    """Every format actually seen from PyPI/the version picker must still work."""
    assert _validate_version(version) == version


@pytest.mark.parametrize(
    "version",
    [
        "../../../data",
        "../../etc/passwd",
        "..",
        "a/b",
        "a\\b",
        "",
        "/etc/passwd",
        "2026.3.1/../../../data",
    ],
)
def test_validate_version_rejects_traversal_and_separators(version):
    with pytest.raises(ValueError):
        _validate_version(version)


# ---------------------------------------------------------------------------
# VersionManager._venv_path — the actual sink. Confirm the malicious value
# never resolves to a path outside the configured base, and that no
# directory operation (create/evict/rmtree) is reachable for it.
# ---------------------------------------------------------------------------

@pytest.fixture
def vm(tmp_path: Path) -> VersionManager:
    base = tmp_path / "esphome-versions"
    return VersionManager(versions_base=base, max_versions=3)


def test_venv_path_rejects_traversal_before_any_filesystem_access(vm, tmp_path):
    """The historical attack: a version string that walks outside the base
    and lands on a sibling directory an attacker wants deleted."""
    victim = tmp_path / "config"
    victim.mkdir()
    (victim / "secrets.yaml").write_text("wifi_password: hunter2\n")

    with pytest.raises(ValueError):
        vm._venv_path("../config")

    # The sibling directory must be completely untouched.
    assert victim.exists()
    assert (victim / "secrets.yaml").exists()


def test_ensure_version_rejects_traversal_without_touching_disk(vm, monkeypatch):
    """ensure_version() is the real public entrypoint workers/handlers call
    — confirm the reject happens before _install (and its rmtree) ever runs."""
    install_calls = []
    monkeypatch.setattr(VersionManager, "_install", lambda self, v: install_calls.append(v))

    with pytest.raises(ValueError):
        vm.ensure_version("../../etc")

    assert install_calls == []


def test_install_never_rmtrees_outside_base(vm, tmp_path, monkeypatch):
    """Even if a caller reached _install directly (bypassing ensure_version),
    the rmtree target must still be validated — belt and suspenders."""
    victim = tmp_path / "config"
    victim.mkdir()

    rmtree_calls = []
    monkeypatch.setattr(shutil, "rmtree", lambda path, *a, **kw: rmtree_calls.append(path))

    with pytest.raises(ValueError):
        vm._install("../config")

    assert rmtree_calls == []
    assert victim.exists()


def test_legitimate_version_still_installs_under_base(vm):
    """The fix must not regress the happy path — a real version string
    resolves inside the configured base, same as before."""
    path = vm._venv_path("2026.3.1")
    assert path == vm._base / "2026.3.1"
    assert path.resolve().parent == vm._base.resolve()
