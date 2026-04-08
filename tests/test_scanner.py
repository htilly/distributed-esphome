"""Unit tests for the YAML scanner and bundle creator."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from scanner import (
    build_name_to_target_map,
    create_bundle,
    get_device_address,
    get_device_metadata,
    get_esphome_version,
    scan_configs,
)

FIXTURES = Path(__file__).parent / "fixtures" / "esphome_configs"


# ---------------------------------------------------------------------------
# scan_configs
# ---------------------------------------------------------------------------

def test_scan_finds_yaml_files():
    targets = scan_configs(str(FIXTURES))
    assert "device1.yaml" in targets
    assert "device2.yaml" in targets


def test_scan_excludes_secrets_yaml():
    targets = scan_configs(str(FIXTURES))
    assert "secrets.yaml" not in targets
    assert not any(t.lower() == "secrets.yaml" for t in targets)


def test_scan_excludes_subdirectory_yaml():
    """Only top-level YAMLs should be returned."""
    targets = scan_configs(str(FIXTURES))
    assert not any("packages" in t for t in targets)


def test_scan_nonexistent_dir():
    targets = scan_configs("/nonexistent/path/that/does/not/exist")
    assert targets == []


def test_scan_returns_sorted_list():
    targets = scan_configs(str(FIXTURES))
    assert targets == sorted(targets)


def test_scan_only_returns_filenames():
    """Results should be filenames only, not full paths."""
    targets = scan_configs(str(FIXTURES))
    for t in targets:
        assert "/" not in t
        assert t.endswith(".yaml")


def test_scan_empty_dir(tmp_path):
    targets = scan_configs(str(tmp_path))
    assert targets == []


def test_scan_dir_with_only_secrets(tmp_path):
    (tmp_path / "secrets.yaml").write_text("key: val")
    targets = scan_configs(str(tmp_path))
    assert targets == []


# ---------------------------------------------------------------------------
# create_bundle
# ---------------------------------------------------------------------------

def test_bundle_is_tar_gz():
    raw = create_bundle(str(FIXTURES))
    assert isinstance(raw, bytes)
    assert len(raw) > 0
    # gzip magic bytes
    assert raw[:2] == b"\x1f\x8b"


def test_bundle_includes_secrets_yaml():
    raw = create_bundle(str(FIXTURES))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        names = tar.getnames()
    assert "secrets.yaml" in names


def test_bundle_includes_device_yamls():
    raw = create_bundle(str(FIXTURES))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        names = tar.getnames()
    assert "device1.yaml" in names
    assert "device2.yaml" in names


def test_bundle_includes_subdirectory():
    raw = create_bundle(str(FIXTURES))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        names = tar.getnames()
    assert any("packages" in n for n in names), f"packages/ not found in bundle: {names}"
    assert any("common.yaml" in n for n in names)


def test_bundle_preserves_content():
    raw = create_bundle(str(FIXTURES))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        f = tar.extractfile("secrets.yaml")
        content = f.read().decode()
    assert "wifi_ssid" in content
    assert "wifi_password" in content


def test_bundle_paths_are_relative():
    """Archive paths should not start with '/' (absolute) or include the base dir prefix."""
    raw = create_bundle(str(FIXTURES))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for name in tar.getnames():
            assert not name.startswith("/"), f"Absolute path in bundle: {name}"


def test_bundle_empty_dir(tmp_path):
    """Bundle of empty directory should be a valid but empty tar.gz."""
    raw = create_bundle(str(tmp_path))
    assert raw[:2] == b"\x1f\x8b"
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        assert tar.getnames() == []


# ---------------------------------------------------------------------------
# get_esphome_version
# ---------------------------------------------------------------------------

def test_get_esphome_version_returns_string():
    ver = get_esphome_version()
    assert isinstance(ver, str)
    assert len(ver) > 0


def test_get_esphome_version_returns_unknown_when_not_installed():
    """If esphome is not installed, should return 'unknown' without crashing."""
    import importlib.metadata as meta
    original = meta.version

    def mock_version(pkg):
        if pkg == "esphome":
            raise meta.PackageNotFoundError(pkg)
        return original(pkg)

    meta.version = mock_version
    try:
        ver = get_esphome_version()
        assert ver == "unknown"
    finally:
        meta.version = original


# ---------------------------------------------------------------------------
# get_device_metadata — extracting name/friendly_name/area/comment/project
# ---------------------------------------------------------------------------

def _write_yaml(config_dir: Path, name: str, content: str) -> None:
    (config_dir / name).write_text(content)


# Minimum-required tail every test config needs so that newer ESPHome versions
# (which validate harder) accept it through _resolve_esphome_config. Without
# this, the resolver returns None and metadata extraction silently no-ops.
_MIN_BOARD = """\
esp8266:
  board: d1_mini

wifi:
  ssid: test
  password: test
"""


def test_metadata_extracts_name_and_friendly_name(tmp_path):
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: living-room-sensor
  friendly_name: Living Room Sensor

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["device_name_raw"] == "living-room-sensor"
    assert meta["device_name"] == "Living Room Sensor"
    assert meta["friendly_name"] == "Living Room Sensor"


def test_metadata_extracts_area_and_comment(tmp_path):
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev
  area: Kitchen
  comment: Over the sink

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["area"] == "Kitchen"
    assert meta["comment"] == "Over the sink"


def test_metadata_extracts_project(tmp_path):
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev
  project:
    name: example.device
    version: "1.2.3"

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["project_name"] == "example.device"
    assert meta["project_version"] == "1.2.3"


def test_metadata_detects_web_server(tmp_path):
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev

{_MIN_BOARD}
web_server:
  port: 80
""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["has_web_server"] is True


def test_metadata_missing_web_server(tmp_path):
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["has_web_server"] is False


def test_metadata_substitutions_resolved(tmp_path):
    """${substitutions} in area/comment should be resolved from the substitutions block."""
    _write_yaml(tmp_path, "dev.yaml", f"""\
substitutions:
  device_name: living_room
  room_area: Living Room

esphome:
  name: ${{device_name}}
  area: ${{room_area}}

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["area"] == "Living Room"


def test_metadata_all_fields_none_for_empty_config(tmp_path):
    """A minimal config with no metadata still returns a well-formed dict."""
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev

{_MIN_BOARD}""")
    meta = get_device_metadata(str(tmp_path), "dev.yaml")
    assert meta["device_name_raw"] == "dev"
    assert meta["friendly_name"] is None
    assert meta["area"] is None
    assert meta["comment"] is None
    assert meta["project_name"] is None
    assert meta["project_version"] is None
    assert meta["has_web_server"] is False


# ---------------------------------------------------------------------------
# build_name_to_target_map
# ---------------------------------------------------------------------------

def test_name_map_uses_filename_stem_fallback(tmp_path):
    """Filename stem is always in the map as a fallback."""
    _write_yaml(tmp_path, "bedroom.yaml", f"""\
esphome:
  name: bedroom

{_MIN_BOARD}""")
    name_map, _, _ = build_name_to_target_map(str(tmp_path), ["bedroom.yaml"])
    assert name_map["bedroom"] == "bedroom.yaml"


def test_name_map_maps_esphome_name_to_target(tmp_path):
    """esphome.name (may differ from filename) is mapped to the filename."""
    _write_yaml(tmp_path, "kitchen.yaml", f"""\
esphome:
  name: kitchen-under-cabinet

{_MIN_BOARD}""")
    name_map, _, _ = build_name_to_target_map(str(tmp_path), ["kitchen.yaml"])
    assert name_map["kitchen-under-cabinet"] == "kitchen.yaml"
    # And the underscore-normalized variant for mDNS (bug #159)
    assert name_map["kitchen_under_cabinet"] == "kitchen.yaml"


def test_name_map_extracts_encryption_key(tmp_path):
    """API encryption keys are extracted and keyed by device name."""
    _write_yaml(tmp_path, "dev.yaml", f"""\
esphome:
  name: dev

{_MIN_BOARD}
api:
  encryption:
    key: "SGVsbG9Xb3JsZEhlbGxvV29ybGRIZWxsb1dvcmxkRWVFRQ=="
""")
    _, keys, _ = build_name_to_target_map(str(tmp_path), ["dev.yaml"])
    assert keys["dev"] == "SGVsbG9Xb3JsZEhlbGxvV29ybGRIZWxsb1dvcmxkRWVFRQ=="


def test_name_map_extracts_use_address(tmp_path):
    """wifi.use_address overrides are captured."""
    _write_yaml(tmp_path, "dev.yaml", """\
esphome:
  name: dev

esp8266:
  board: d1_mini

wifi:
  ssid: test
  password: test
  use_address: 192.168.1.42
""")
    _, _, overrides = build_name_to_target_map(str(tmp_path), ["dev.yaml"])
    assert overrides["dev"] == "192.168.1.42"


def test_name_map_empty_targets(tmp_path):
    name_map, keys, overrides = build_name_to_target_map(str(tmp_path), [])
    assert name_map == {}
    assert keys == {}
    assert overrides == {}


# ---------------------------------------------------------------------------
# get_device_address — bug #179
# Mirrors ESPHome CORE.address: wifi → ethernet → openthread, each honoring
# use_address → manual_ip.static_ip → {name}.local fallback.
# ---------------------------------------------------------------------------

def test_get_device_address_wifi_use_address():
    config = {"wifi": {"use_address": "192.168.1.42"}}
    assert get_device_address(config, "dev") == "192.168.1.42"


def test_get_device_address_wifi_static_ip():
    config = {"wifi": {"manual_ip": {"static_ip": "10.0.0.5"}}}
    assert get_device_address(config, "dev") == "10.0.0.5"


def test_get_device_address_wifi_default_to_mdns():
    config = {"wifi": {"ssid": "test"}}
    assert get_device_address(config, "dev") == "dev.local"


def test_get_device_address_ethernet_use_address():
    config = {"ethernet": {"use_address": "10.0.0.10"}}
    assert get_device_address(config, "dev") == "10.0.0.10"


def test_get_device_address_ethernet_static_ip():
    config = {"ethernet": {"manual_ip": {"static_ip": "10.0.0.11"}}}
    assert get_device_address(config, "dev") == "10.0.0.11"


def test_get_device_address_ethernet_default_to_mdns():
    config = {"ethernet": {"type": "LAN8720"}}
    assert get_device_address(config, "dev") == "dev.local"


def test_get_device_address_openthread_use_address():
    """Thread-only devices: openthread.use_address overrides everything."""
    config = {"openthread": {"use_address": "fd00::1"}}
    assert get_device_address(config, "thread-dev") == "fd00::1"


def test_get_device_address_openthread_default_to_mdns():
    """Thread-only device with no explicit address falls back to mDNS hostname."""
    config = {"openthread": {"network_key": "deadbeef"}}
    assert get_device_address(config, "thread-dev") == "thread-dev.local"


def test_get_device_address_nothing_configured():
    """Empty config (no network block at all) falls back to {name}.local."""
    config = {"esphome": {"name": "minimal"}}
    assert get_device_address(config, "minimal") == "minimal.local"


# Bonus: wifi takes precedence over ethernet/openthread when multiple are present
def test_get_device_address_wifi_wins_over_ethernet():
    config = {
        "wifi": {"use_address": "192.168.1.42"},
        "ethernet": {"use_address": "10.0.0.10"},
    }
    assert get_device_address(config, "dev") == "192.168.1.42"


# ---------------------------------------------------------------------------
# build_name_to_target_map populates address_overrides for ALL targets (#179)
# ---------------------------------------------------------------------------

def test_name_map_overrides_static_ip_target(tmp_path):
    _write_yaml(tmp_path, "static.yaml", """\
esphome:
  name: static-dev

esp8266:
  board: d1_mini

wifi:
  ssid: test
  password: test
  manual_ip:
    static_ip: 192.168.1.99
    gateway: 192.168.1.1
    subnet: 255.255.255.0
""")
    _, _, overrides = build_name_to_target_map(str(tmp_path), ["static.yaml"])
    assert overrides["static-dev"] == "192.168.1.99"


def test_name_map_overrides_default_mdns_for_dhcp_target(tmp_path):
    """A plain DHCP wifi target now also gets an override (the mDNS name)."""
    _write_yaml(tmp_path, "dhcp.yaml", """\
esphome:
  name: dhcp-dev

esp8266:
  board: d1_mini

wifi:
  ssid: test
  password: test
""")
    _, _, overrides = build_name_to_target_map(str(tmp_path), ["dhcp.yaml"])
    assert overrides["dhcp-dev"] == "dhcp-dev.local"


# The Thread-only case is exercised by the FIXTURE-based test
# (test_thread_only_fixture_resolves_to_mdns) below — it uses the real
# tests/fixtures/esphome_configs/thread_only_device.yaml which is a known
# good ESP32-C6 + openthread config.


# ---------------------------------------------------------------------------
# Fixture-based integration tests for #186 — verify the real fixture YAMLs
# (which include !secret + manual_ip / openthread blocks) actually parse
# through ESPHome's full resolution pipeline and yield the right metadata.
# These exercise the same code path the production code uses, not isolated
# helper functions.
# ---------------------------------------------------------------------------

def test_static_ip_fixture_resolves_address():
    """Fixture: tests/fixtures/esphome_configs/static_ip_device.yaml"""
    _, _, overrides = build_name_to_target_map(
        str(FIXTURES), ["static_ip_device.yaml"],
    )
    assert overrides.get("static-ip-device") == "192.168.1.99"


def test_thread_only_fixture_resolves_to_mdns():
    """Fixture: tests/fixtures/esphome_configs/thread_only_device.yaml

    A Thread-only device with no wifi/ethernet block should still get an
    address override (falling back to {name}.local). Without this, the YAML
    row never exists and any later mDNS discovery duplicates it (#179).
    """
    _, _, overrides = build_name_to_target_map(
        str(FIXTURES), ["thread_only_device.yaml"],
    )
    assert "thread-only-device" in overrides
    assert overrides["thread-only-device"] == "thread-only-device.local"


def test_static_ip_fixture_metadata():
    """Static-IP device's friendly_name still resolves correctly."""
    meta = get_device_metadata(str(FIXTURES), "static_ip_device.yaml")
    assert meta["friendly_name"] == "Static IP Device"
    assert meta["device_name_raw"] == "static-ip-device"
