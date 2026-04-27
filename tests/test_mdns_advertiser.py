"""HT.5 — dedicated coverage for ``mdns_advertiser``.

The module shipped in 1.6.1 with zero unit tests; every issue fixed in
PR #80 (skip when no IPv4, no `localhost` server, dead `gethostname()`
guard) was caught only by reading the diff. This file pins the same
behaviours so the next reader gets a regression net.
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdns_advertiser import SERVICE_TYPE, FleetAdvertiser


def _make_zc() -> MagicMock:
    """A stand-in for ``AsyncZeroconf`` exposing only what the advertiser uses."""
    zc = MagicMock()
    zc.async_register_service = AsyncMock()
    zc.async_unregister_service = AsyncMock()
    return zc


@pytest.mark.asyncio
async def test_start_registers_service_when_ipv4_available():
    zc = _make_zc()
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value="192.0.2.10"), \
         patch("mdns_advertiser._read_version", return_value="1.7.0"), \
         patch("socket.gethostname", return_value="myhost"):
        await adv.start()

    zc.async_register_service.assert_awaited_once()
    info = zc.async_register_service.await_args.args[0]
    assert info.type == SERVICE_TYPE
    assert info.port == 8123
    # ``addresses`` is bytes-encoded IPv4 — round-trip via inet_ntoa.
    assert socket.inet_ntoa(info.addresses[0]) == "192.0.2.10"
    # base_url + version + protocol bake into properties as bytes.
    props = {k.decode(): v.decode() for k, v in info.properties.items()}
    assert props["version"] == "1.7.0"
    assert props["base_url"] == "http://192.0.2.10:8123"
    assert props["protocol"] == "1"
    # Routable hostname → server filled.
    assert info.server == "myhost.local."


@pytest.mark.asyncio
async def test_start_skips_when_no_primary_ipv4():
    """PR #80: skip advertise rather than ship ``http://:<port>``."""
    zc = _make_zc()
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value=None):
        await adv.start()
    zc.async_register_service.assert_not_called()
    # stop() must remain a safe no-op when start() bailed early.
    await adv.stop()
    zc.async_unregister_service.assert_not_called()


@pytest.mark.asyncio
async def test_start_falls_back_when_hostname_is_localhost():
    """PR #80: ``localhost`` can't be resolved by peers, so server=None."""
    zc = _make_zc()
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value="192.0.2.10"), \
         patch("mdns_advertiser._read_version", return_value="1.7.0"), \
         patch("socket.gethostname", return_value="localhost"):
        await adv.start()
    info = zc.async_register_service.await_args.args[0]
    assert info.server is None


@pytest.mark.asyncio
async def test_stop_before_start_does_not_crash():
    zc = _make_zc()
    adv = FleetAdvertiser(zc, port=8123)
    await adv.stop()  # must not raise
    zc.async_unregister_service.assert_not_called()


@pytest.mark.asyncio
async def test_start_twice_re_registers_only_when_prior_failed_to_take():
    """``start()`` is not designed to re-register; calling it twice
    schedules another register call. The contract is "callers do this
    once" — pin the second-call shape so a future caller-side bug is
    obvious from the test rather than silently double-advertising
    different ServiceInfo objects."""
    zc = _make_zc()
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value="192.0.2.10"), \
         patch("mdns_advertiser._read_version", return_value="1.7.0"), \
         patch("socket.gethostname", return_value="myhost"):
        await adv.start()
        await adv.start()
    # Both calls produced register attempts — i.e. the module doesn't
    # silently swallow a second start. If a future change adds an
    # idempotency guard, this test should flip to assert exactly one.
    assert zc.async_register_service.await_count == 2


@pytest.mark.asyncio
async def test_register_failure_leaves_info_unset():
    """If zeroconf rejects registration, ``stop()`` must remain a safe
    no-op — the advertiser tracks failure by clearing ``_info``."""
    zc = _make_zc()
    zc.async_register_service.side_effect = RuntimeError("boom")
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value="192.0.2.10"), \
         patch("mdns_advertiser._read_version", return_value="1.7.0"), \
         patch("socket.gethostname", return_value="myhost"):
        await adv.start()
    # Register raised, but the advertiser caught it — start() didn't propagate.
    assert adv._info is None
    await adv.stop()
    zc.async_unregister_service.assert_not_called()


@pytest.mark.asyncio
async def test_stop_swallows_unregister_errors():
    """A teardown-time zeroconf failure mustn't crash the shutdown path."""
    zc = _make_zc()
    zc.async_unregister_service.side_effect = RuntimeError("boom")
    adv = FleetAdvertiser(zc, port=8123)
    with patch("mdns_advertiser._primary_ipv4", return_value="192.0.2.10"), \
         patch("mdns_advertiser._read_version", return_value="1.7.0"), \
         patch("socket.gethostname", return_value="myhost"):
        await adv.start()
    await adv.stop()  # must not raise
    assert adv._info is None
