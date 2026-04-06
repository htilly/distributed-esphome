"""System information gathering — stdlib only, no psutil dependency.

Importable standalone; no dependency on other client modules.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Captured at process start so uptime can be computed on each heartbeat.
_PROCESS_START_TIME: float = time.monotonic()


def _benchmark_cpu() -> int:
    """Run a quick CPU benchmark. Returns a relative performance score (SHA256 ops/sec / 1000)."""
    import hashlib  # noqa: PLC0415
    data = b"benchmark" * 1000
    count = 0
    deadline = time.monotonic() + 1.0  # run for 1 second
    while time.monotonic() < deadline:
        hashlib.sha256(data).digest()
        count += 1
    return count


# Computed once at startup; included in every heartbeat as a relative CPU score.
_CPU_PERF_SCORE: int = _benchmark_cpu()


# ---------------------------------------------------------------------------
# OS / hardware detection
# ---------------------------------------------------------------------------

def _get_os_version() -> str:
    """Return a human-readable OS version string using only stdlib."""
    system = platform.system()

    if system == "Darwin":
        # e.g. "macOS 15.3"
        mac_ver = platform.mac_ver()[0]
        return f"macOS {mac_ver}" if mac_ver else "macOS"

    if system == "Linux":
        # Parse /etc/os-release for NAME and VERSION_ID (most distros)
        os_release: dict[str, str] = {}
        try:
            with open("/etc/os-release", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, _, val = line.partition("=")
                        os_release[key.strip()] = val.strip().strip('"')
        except OSError:
            pass

        name = os_release.get("NAME") or os_release.get("ID", "")
        version = os_release.get("VERSION_ID", "")
        if name and version:
            return f"{name} {version}"
        if name:
            return name

        # Fallback for minimal containers without /etc/os-release
        kernel = platform.release()
        return f"Linux {kernel}" if kernel else "Linux"

    # Windows or other
    return platform.platform()


def _get_cpu_model() -> str:
    """Return CPU model string using stdlib and /proc/cpuinfo or sysctl."""
    system = platform.system()

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=3,
            )
            model = result.stdout.strip()
            if model:
                return model
        except Exception:
            pass
        # Apple Silicon reports via hw.model (e.g. "Apple M1 Pro")
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.model"],
                capture_output=True, text=True, timeout=3,
            )
            model = result.stdout.strip()
            if model:
                return model
        except Exception:
            pass

    if system == "Linux":
        # Try /proc/cpuinfo — "model name" on x86, "Model name" or "Hardware" on ARM
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if ":" in line:
                        key, _, val = line.partition(":")
                        key = key.strip().lower()
                        if key in ("model name", "hardware", "cpu model"):
                            val = val.strip()
                            if val:
                                return val
        except OSError:
            pass

    # Generic fallback
    machine = platform.machine()
    processor = platform.processor()
    return processor or machine or "Unknown"


def _get_total_memory_bytes() -> Optional[int]:
    """Return total physical memory in bytes using stdlib only."""
    system = platform.system()

    if system == "Linux":
        # Parse /proc/meminfo
        try:
            with open("/proc/meminfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        # Format: "MemTotal:     16384000 kB"
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = int(parts[1])
                            return kb * 1024
        except (OSError, ValueError):
            pass

    if system == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3,
            )
            return int(result.stdout.strip())
        except Exception:
            pass

    return None


def _format_memory(bytes_: int) -> str:
    """Return a human-readable memory string, e.g. '16 GB' or '512 MB'."""
    gb = bytes_ / (1024 ** 3)
    if gb >= 1:
        # Round to nearest whole GB for clean display
        return f"{round(gb)} GB"
    mb = bytes_ / (1024 ** 2)
    return f"{round(mb)} MB"


def _format_uptime(seconds: float) -> str:
    """Return uptime as a compact human-readable string, e.g. '2d 3h' or '45m'."""
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _get_cpu_usage() -> Optional[float]:
    """Return CPU usage as a percentage (0-100) using load average.

    Uses os.getloadavg() (1-minute average) divided by core count.
    Works in Docker on both Linux and macOS without /proc/stat sampling.
    """
    try:
        load1, _, _ = os.getloadavg()
        cores = os.cpu_count() or 1
        return round(min(load1 / cores * 100, 100.0), 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_system_info(versions_dir: str = "/esphome-versions") -> dict:
    """Gather hardware/OS details using stdlib only. All fields are best-effort.

    *versions_dir* is used to report disk space on the build volume.  Pass the
    ``ESPHOME_VERSIONS_DIR`` env value from the caller so this module stays
    dependency-free.

    When running in Docker on a non-Linux host, the container sees the VM's
    Linux.  Set ``HOST_PLATFORM`` to override ``os_version`` with the actual
    host OS (e.g. ``macOS 15.3 (Apple M1 Pro)``).
    """
    cpu_count = os.cpu_count()
    mem_bytes = _get_total_memory_bytes()

    os_version = os.environ.get("HOST_PLATFORM") or _get_os_version()

    # Disk space on the build volume
    disk_total: Optional[str] = None
    disk_free: Optional[str] = None
    disk_pct: Optional[int] = None
    try:
        st = os.statvfs(versions_dir)
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        used_pct = round((1 - free / total) * 100) if total > 0 else None
        disk_total = _format_memory(total)
        disk_free = _format_memory(free)
        disk_pct = used_pct
    except Exception:
        pass

    info: dict = {
        "cpu_arch": platform.machine(),
        "os_version": os_version,
        "cpu_cores": cpu_count,
        "cpu_model": _get_cpu_model(),
        "total_memory": _format_memory(mem_bytes) if mem_bytes is not None else None,
        "uptime": _format_uptime(time.monotonic() - _PROCESS_START_TIME),
        "perf_score": _CPU_PERF_SCORE,
        "cpu_usage": _get_cpu_usage(),
        "disk_total": disk_total,
        "disk_free": disk_free,
        "disk_used_pct": disk_pct,
    }
    return info
