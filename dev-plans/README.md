# Development Plans

Roadmap and bug tracking for distributed-esphome, organized by release.

## Files

- **[PRD.md](PRD.md)** — Product requirements document for the full ESPHome dashboard replacement
- **[SECURITY_AUDIT.md](SECURITY_AUDIT.md)** — Security audit findings (2026-03-29)
- **[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md)** — Step-by-step release process
- **[WORKITEMS-1.0.md](WORKITEMS-1.0.md)** — First stable release: distributed compile, vanilla JS UI, mDNS discovery
- **[WORKITEMS-1.1.md](WORKITEMS-1.1.md)** — React UI rewrite, Monaco editor, HA integration, device lifecycle (89 bug fixes)
- **[WORKITEMS-1.2.md](WORKITEMS-1.2.md)** — shadcn/ui design system, TanStack Table, SWR, local worker (69 bug fixes)
- **[WORKITEMS-1.3.md](WORKITEMS-1.3.md)** — **Current release.** Quality + Testing: CI, Playwright, ruff, coverage, security hardening
- **[WORKITEMS-1.4.md](WORKITEMS-1.4.md)** — Planned: ESPHome Dashboard parity (create device, firmware download, web serial)
- **[WORKITEMS-1.5.md](WORKITEMS-1.5.md)** — Planned: Power-user features (file tree editor, AI/LLM, config diff)
- **[WORKITEMS-future.md](WORKITEMS-future.md)** — Backlog without committed scope

## How this works

- Each release file mixes **work items** (planned features, marked `[x]` when done) and **bug fixes** (numbered, with status FIXED/WONTFIX/etc. and `*(X.Y.Z-dev.N)*` version tags)
- Bug numbers are global and monotonic across releases — they were extracted from the original BUGS.md
- The current release file contains **open bugs** at the bottom under "Open Bugs" — these get folded into the bug fixes list as they land
