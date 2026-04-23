#!/usr/bin/env python3
"""Multi-target deploy + smoke orchestrator for ESPHome Fleet.

Deploys the current dev build (ha-addon/VERSION) to three install paths in
parallel, runs the e2e-hass-4 Playwright suite against each, and prints a
collated pass/fail summary plus clickable URLs.

Replaces ``./push-to-hass-4.sh`` as the end-of-turn smoke command. Keeps
push-to-hass-4.sh around as a fast-path single-target loop for UI-only
iteration (no GHCR round-trip).

Targets (see dev-plans/HOME-LAB.md):
  hass-4          always-on HAOS box at 192.168.225.112
  haos-pve        throwaway HAOS VM on the `pve` Proxmox host
  standalone-pve  plain Docker host `docker-pve` running docker-compose

Image flow: one `docker buildx build --push` per Dockerfile (three total),
all tagged with dev-<VERSION>. All three targets pull that same tag.

Usage:
  scripts/test-matrix.py                    # all targets (default)
  scripts/test-matrix.py --targets hass-4   # single target
  scripts/test-matrix.py --no-build         # reuse last pushed tag
  scripts/test-matrix.py --seq-tests        # serialize Playwright runs
  scripts/test-matrix.py --list             # show targets and exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO_ROOT / "ha-addon" / "VERSION"
LOG_ROOT = REPO_ROOT / "build" / "test-matrix"
GHCR_OWNER = "weirded"

# Matches the upstream publish-addon.yml IMAGE_NAME pattern: one per arch
# with the arch prefixed. {arch} gets substituted to "amd64" here since
# all three targets are x86_64.
IMG_ADDON = f"ghcr.io/{GHCR_OWNER}/amd64-addon-esphome-dist-server"
# Standalone compose consumes these two unprefixed images
# (docker-compose.yml at repo root).
IMG_SERVER = f"ghcr.io/{GHCR_OWNER}/esphome-dist-server"
IMG_CLIENT = f"ghcr.io/{GHCR_OWNER}/esphome-dist-client"


# ANSI colors for per-target prefixes. Falls back to plain text when stdout
# isn't a TTY (pipes, CI logs). 38;5;N = 256-color.
COLORS = {
    "hass-4":         "\033[38;5;42m",   # green
    "haos-pve":       "\033[38;5;39m",   # blue
    "standalone-pve": "\033[38;5;170m",  # magenta
    "build":          "\033[38;5;214m",  # orange
}
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def color(name: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(name, '')}{text}{RESET}"


@dataclass
class BuildImage:
    """One entry in the buildx fan-out."""
    name: str           # short label ("addon", "server", "client")
    image: str          # full ghcr path
    dockerfile: Path
    context: Path


BUILD_IMAGES = [
    BuildImage(
        name="addon",
        image=IMG_ADDON,
        dockerfile=REPO_ROOT / "ha-addon" / "Dockerfile",
        context=REPO_ROOT / "ha-addon",
    ),
    BuildImage(
        name="server",
        image=IMG_SERVER,
        dockerfile=REPO_ROOT / "ha-addon" / "Dockerfile.standalone",
        context=REPO_ROOT / "ha-addon",
    ),
    BuildImage(
        name="client",
        image=IMG_CLIENT,
        dockerfile=REPO_ROOT / "ha-addon" / "client" / "Dockerfile",
        context=REPO_ROOT / "ha-addon" / "client",
    ),
]


@dataclass
class Target:
    name: str
    base_url: str
    # Command to deploy (with --skip-smoke appended). Run from REPO_ROOT.
    deploy_cmd: list[str]
    deploy_env: dict[str, str] = field(default_factory=dict)
    # Path the deploy script writes the add-on token to on success. Read
    # after deploy completes and passed to Playwright as FLEET_TOKEN.
    token_cache: Path = Path()
    # Extra env to pass to Playwright (FLEET_TARGET, HASS_URL/HASS_TOKEN
    # for ha-services.spec.ts, etc.).
    playwright_env: dict[str, str] = field(default_factory=dict)
    # Extra args to `npm run test:e2e:hass-4 --`, e.g.
    # ["--grep-invert=@requires-ha"].
    playwright_args: list[str] = field(default_factory=list)


def make_targets(version: str) -> dict[str, Target]:
    home = Path.home()
    tag = version  # 1:1 with VERSION; no separate --from-ghcr TAG argument

    return {
        "hass-4": Target(
            name="hass-4",
            base_url="http://192.168.225.112:8765",
            deploy_cmd=["./push-to-hass-4.sh", "--from-ghcr", "--skip-smoke"],
            token_cache=home / ".config" / "distributed-esphome" / "hass4-token",
            playwright_env={
                "HASS_URL": "http://hass-4.local:8123",
                "HASS_TOKEN": os.environ.get("HASS_TOKEN", ""),
                "FLEET_TARGET": "cyd-office-info.yaml",
            },
        ),
        "haos-pve": Target(
            name="haos-pve",
            base_url="http://192.168.226.135:8765",
            deploy_cmd=["./push-to-haos.sh", "--from-ghcr", "--skip-smoke"],
            deploy_env={"HAOS_URL": "http://192.168.226.135:8123"},
            token_cache=home / ".config" / "distributed-esphome" / "haos-addon-token",
            playwright_env={
                # The throwaway VM doesn't have the esphome_fleet HA service
                # set up, so @requires-ha specs would skip themselves on the
                # HASS_TOKEN guard anyway. Filter them out explicitly so the
                # summary row shows pass/5 not pass/6-with-skip.
                "FLEET_TARGET": "cyd-world-clock.yaml",
            },
            playwright_args=["--grep-invert=@requires-ha"],
        ),
        "standalone-pve": Target(
            name="standalone-pve",
            base_url="http://docker-pve:8765",
            deploy_cmd=["bash", "scripts/standalone/deploy.sh"],
            deploy_env={"TAG": tag, "STANDALONE_HOST": "docker-pve"},
            token_cache=home / ".config" / "distributed-esphome" / "standalone-token",
            playwright_env={
                "FLEET_TARGET": "cyd-world-clock.yaml",
            },
            playwright_args=["--grep-invert=@requires-ha"],
        ),
    }


@dataclass
class TargetResult:
    target: str
    deploy_ok: bool = False
    deploy_elapsed: float = 0.0
    tests_passed: int = 0
    tests_total: int = 0
    tests_ok: bool = False
    total_elapsed: float = 0.0
    report_dir: Path = Path()
    error: str = ""


# ---------------------------------------------------------------------------
# Subprocess plumbing: run a command, tee stdout+stderr to prefixed terminal
# output AND a per-target log file, return the exit code.
# ---------------------------------------------------------------------------

async def run_streaming(
    cmd: list[str],
    *,
    prefix: str,
    log_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> int:
    """Run ``cmd``, streaming output prefixed with ``[prefix]`` AND appending
    to ``log_path``. Returns the exit code.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Mark the start of this command in the log so post-mortems can tell
    # where each phase begins.
    with log_path.open("a") as log:
        log.write(f"\n===== $ {' '.join(cmd)} =====\n")

    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Line-buffered stdout for Python children. Bash children generally flush
    # on newline when stdio is a pipe, but this makes the live view snappier
    # when any step is a Python script.
    full_env.setdefault("PYTHONUNBUFFERED", "1")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=full_env,
        cwd=str(cwd) if cwd else None,
    )

    tag = color(prefix, f"[{prefix:<14}]")
    assert proc.stdout is not None
    with log_path.open("a") as log:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            log.write(line + "\n")
            log.flush()
            print(f"{tag} {line}", flush=True)

    return await proc.wait()


# ---------------------------------------------------------------------------
# Preflight checks — fail fast with actionable errors.
# ---------------------------------------------------------------------------

def preflight(skip_build: bool) -> None:
    problems: list[str] = []

    if not VERSION_FILE.exists():
        problems.append(f"VERSION file missing at {VERSION_FILE}")

    if not skip_build:
        if shutil.which("docker") is None:
            problems.append("`docker` not found on PATH")
        # Confirm GHCR login exists. `docker info` surfaces registry auth
        # only when a registry is queried; easier is to check the config.
        cfg = Path.home() / ".docker" / "config.json"
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text())
                auths = data.get("auths", {})
                if "ghcr.io" not in auths:
                    problems.append(
                        "Not logged in to ghcr.io. Run: "
                        "`echo $GHCR_TOKEN | docker login ghcr.io -u <user> --password-stdin`"
                    )
            except json.JSONDecodeError:
                pass  # unreadable config — let docker surface the real error

    for script in ("push-to-hass-4.sh", "push-to-haos.sh"):
        if not (REPO_ROOT / script).exists():
            problems.append(f"missing {script} at repo root")

    if problems:
        sys.stderr.write("Preflight failed:\n")
        for p in problems:
            sys.stderr.write(f"  - {p}\n")
        sys.exit(2)


# ---------------------------------------------------------------------------
# Step 1: build + push images.
# ---------------------------------------------------------------------------

async def build_and_push_one(img: BuildImage, version: str) -> bool:
    """Build one image for linux/amd64 and push both the version tag and
    the buildcache tag. Returns True on success.
    """
    tag_version = f"{img.image}:{version}"
    tag_cache = f"{img.image}:buildcache"

    cmd = [
        "docker", "buildx", "build",
        "--platform", "linux/amd64",
        "--file", str(img.dockerfile),
        "--tag", tag_version,
        "--push",
        # Registry-based layer cache — makes iterations fast even on a
        # QEMU-emulated build from an arm64 Mac. --cache-to writes the
        # cache manifest; --cache-from reads it on the next run.
        "--cache-from", f"type=registry,ref={tag_cache}",
        "--cache-to", f"type=registry,ref={tag_cache},mode=max",
    ]
    # The HA add-on Dockerfile expects BUILD_ARCH + BUILD_VERSION build
    # args (mirrors publish-addon.yml).
    if img.name == "addon":
        cmd += ["--build-arg", "BUILD_ARCH=amd64",
                "--build-arg", f"BUILD_VERSION={version}"]
    cmd.append(str(img.context))

    log_path = LOG_ROOT / "build" / f"{img.name}.log"
    code = await run_streaming(
        cmd,
        prefix=f"build:{img.name}",
        log_path=log_path,
        cwd=REPO_ROOT,
    )
    if code != 0:
        print(color("build", f"[build:{img.name}   ] ✖ buildx exit {code} (log: {log_path})"), flush=True)
    return code == 0


async def build_all(version: str) -> bool:
    """Build all images concurrently. Returns True iff all succeeded."""
    print(color("build", f"==> Building + pushing dev images with tag {version} ..."), flush=True)
    start = time.monotonic()
    results = await asyncio.gather(
        *[build_and_push_one(img, version) for img in BUILD_IMAGES],
    )
    elapsed = time.monotonic() - start
    ok = all(results)
    marker = "✔" if ok else "✖"
    print(color("build", f"==> Build/push {marker} in {elapsed:.0f}s"), flush=True)
    return ok


# ---------------------------------------------------------------------------
# Step 2: per-target chain — deploy → read token → playwright.
# ---------------------------------------------------------------------------

async def run_target_chain(target: Target, sem: asyncio.Semaphore | None) -> TargetResult:
    result = TargetResult(target=target.name)
    target_dir = LOG_ROOT / target.name
    target_dir.mkdir(parents=True, exist_ok=True)
    result.report_dir = target_dir / "playwright-report"
    deploy_log = target_dir / "deploy.log"
    test_log = target_dir / "playwright.log"

    target_start = time.monotonic()

    # -- Deploy ------------------------------------------------------------
    deploy_start = time.monotonic()
    code = await run_streaming(
        target.deploy_cmd,
        prefix=target.name,
        log_path=deploy_log,
        env=target.deploy_env,
        cwd=REPO_ROOT,
    )
    result.deploy_elapsed = time.monotonic() - deploy_start
    if code != 0:
        result.error = f"deploy exited {code} (see {deploy_log})"
        result.total_elapsed = time.monotonic() - target_start
        return result
    result.deploy_ok = True

    # -- Resolve the add-on token the deploy script just cached ------------
    token = ""
    if target.token_cache.exists():
        token = target.token_cache.read_text().strip()
    if not token:
        # Warn but don't abort — the suite can still exercise unauthed
        # paths. Any Bearer-gated test will surface its own failure.
        print(color(
            target.name,
            f"[{target.name:<14}] ⚠ no token at {target.token_cache} — Bearer-gated tests will 401",
        ), flush=True)

    # -- Playwright --------------------------------------------------------
    npm_env = {
        "FLEET_URL": target.base_url,
        "FLEET_TOKEN": token,
        # Per-target report dir so parallel runs don't collide.
        "PLAYWRIGHT_HTML_REPORT": str(result.report_dir),
        # JSON reporter output path for post-run collation.
        "PLAYWRIGHT_JSON_OUTPUT_NAME": str(target_dir / "results.json"),
    }
    npm_env.update(target.playwright_env)

    # npm run test:e2e:hass-4 -- <extra args>. The config auto-adds the
    # JSON reporter when PLAYWRIGHT_JSON_OUTPUT_NAME is set (which it is,
    # below), so we don't need a --reporter override.
    npm_cmd = ["npm", "run", "test:e2e:hass-4", "--"]
    npm_cmd += target.playwright_args

    if sem is None:
        code = await run_streaming(
            npm_cmd,
            prefix=target.name,
            log_path=test_log,
            env=npm_env,
            cwd=REPO_ROOT / "ha-addon" / "ui",
        )
    else:
        async with sem:
            code = await run_streaming(
                npm_cmd,
                prefix=target.name,
                log_path=test_log,
                env=npm_env,
                cwd=REPO_ROOT / "ha-addon" / "ui",
            )

    # -- Parse Playwright JSON --------------------------------------------
    results_json = target_dir / "results.json"
    if results_json.exists():
        try:
            data = json.loads(results_json.read_text())
            stats = data.get("stats", {})
            # Playwright's JSON reporter surfaces expected/unexpected counts.
            # tests_total = expected + unexpected + flaky + skipped.
            expected = stats.get("expected", 0)
            unexpected = stats.get("unexpected", 0)
            flaky = stats.get("flaky", 0)
            skipped = stats.get("skipped", 0)
            result.tests_passed = expected + flaky  # flaky still count as passed
            result.tests_total = expected + unexpected + flaky + skipped
            result.tests_ok = (code == 0)
        except (json.JSONDecodeError, OSError) as e:
            result.error = f"couldn't parse playwright results.json: {e}"
            result.tests_ok = False
    else:
        result.tests_ok = (code == 0)
        if not result.tests_ok:
            result.error = f"playwright exited {code} with no results.json (see {test_log})"

    result.total_elapsed = time.monotonic() - target_start
    return result


# ---------------------------------------------------------------------------
# Summary rendering.
# ---------------------------------------------------------------------------

def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def print_summary(results: list[TargetResult], targets: dict[str, Target]) -> None:
    print()
    print(f"{BOLD}===== Test matrix summary ====={RESET}" if sys.stdout.isatty() else "===== Test matrix summary =====")
    print()

    # Compute column widths.
    rows = []
    for r in results:
        if not r.deploy_ok:
            deploy_cell = "✖"
            tests_cell = "—"
        else:
            deploy_cell = f"✔ {fmt_duration(r.deploy_elapsed)}"
            if r.tests_ok and r.tests_total > 0:
                tests_cell = f"✔ {r.tests_passed}/{r.tests_total}"
            elif r.tests_total > 0:
                tests_cell = f"✖ {r.tests_passed}/{r.tests_total}"
            else:
                tests_cell = "✖"
        rows.append((
            r.target,
            deploy_cell,
            tests_cell,
            fmt_duration(r.total_elapsed),
            str(r.report_dir.relative_to(REPO_ROOT)) if r.report_dir else "",
        ))

    headers = ("Target", "Deploy", "Tests", "Elapsed", "Report")
    widths = [max(len(str(row[i])) for row in (rows + [headers])) for i in range(5)]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))

    print(line(headers))
    print(line(tuple("─" * w for w in widths)))
    for row in rows:
        print(line(row))

    print()
    print("Open:")
    for r in results:
        url = targets[r.target].base_url
        status = "✔" if (r.deploy_ok and r.tests_ok) else "✖"
        print(f"  {status} {r.target:<14}  {url}")

    # Per-target error lines for anything that failed. One line each so
    # the summary stays compact; the full log path is right there.
    errors = [r for r in results if r.error]
    if errors:
        print()
        print(f"{BOLD}Failures:{RESET}" if sys.stdout.isatty() else "Failures:")
        for r in errors:
            print(f"  {r.target}: {r.error}")

    print()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def run(args: argparse.Namespace) -> int:
    version = VERSION_FILE.read_text().strip()
    all_targets = make_targets(version)

    if args.list:
        print("Available targets:")
        for name, t in all_targets.items():
            print(f"  {name:<16} {t.base_url}")
        return 0

    if args.targets:
        names = [n.strip() for n in args.targets.split(",")]
        unknown = [n for n in names if n not in all_targets]
        if unknown:
            sys.stderr.write(f"Unknown target(s): {', '.join(unknown)}\n")
            sys.stderr.write(f"Available: {', '.join(all_targets)}\n")
            return 2
    else:
        names = list(all_targets)

    selected = {n: all_targets[n] for n in names}

    print(color("build", f"==> test-matrix.py v{version}  targets: {', '.join(names)}"), flush=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    # -- Build + push images --------------------------------------------
    if not args.no_build:
        ok = await build_all(version)
        if not ok:
            sys.stderr.write("Build/push failed — skipping all target deploys.\n")
            return 1

    # -- Run target chains ----------------------------------------------
    # Deploy always parallel. Playwright parallel by default; --seq-tests
    # serializes Playwright to one target at a time (escape hatch for
    # memory pressure). Implemented via a shared semaphore that only the
    # Playwright step inside each chain acquires.
    test_sem = asyncio.Semaphore(1) if args.seq_tests else None

    start = time.monotonic()
    results = await asyncio.gather(
        *[run_target_chain(t, test_sem) for t in selected.values()],
    )
    print(color("build", f"==> All targets done in {fmt_duration(time.monotonic() - start)}"), flush=True)

    # -- Summary --------------------------------------------------------
    print_summary(results, selected)

    # Exit code = non-zero if ANY target failed.
    return 0 if all(r.deploy_ok and r.tests_ok for r in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="See dev-plans/HOME-LAB.md for target infrastructure.",
    )
    parser.add_argument(
        "--targets",
        help="Comma-separated target names (default: all). See --list.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip the buildx+push step and reuse the last pushed dev-<VERSION> tag.",
    )
    parser.add_argument(
        "--seq-tests",
        action="store_true",
        help="Run Playwright one target at a time (deploys still parallel).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available targets and exit.",
    )
    args = parser.parse_args()

    preflight(skip_build=args.no_build or args.list)

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
