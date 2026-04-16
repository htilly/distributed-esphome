# Release Checklist

Use when shipping `develop` → `main`. Copy into a GitHub issue and check items off.

The goal here is **what isn't automated**. Anything covered by CI, the pre-push hook, or `./push-to-hass-4.sh` is referenced with a one-liner — don't re-run it by hand.

---

## Pre-release (on `develop`)

### Claude does

- [ ] **Refresh pinned deps**: `bash scripts/refresh-deps.sh`. Review the diff and commit as `chore: refresh pinned deps for X.Y.Z`.
- [ ] **Dependabot**: confirm no open high/critical alerts. `gh api repos/:owner/:repo/dependabot/alerts --jq '.[] | select(.state=="open" and (.security_advisory.severity=="high" or .security_advisory.severity=="critical"))'` — must be empty. If any are open, upgrade the dep or explicitly accept the risk in WORKITEMS. (`pip-audit` + `npm audit` + ruff + mypy + pytest + invariants + frontend build already gate CI.)
- [ ] **Ensure CI is green on `develop`**: `gh run list --branch develop --limit 3`.
- [ ] **Bump version**: `bash scripts/bump-version.sh X.Y.Z`.
- [ ] **Write changelog entry** in `ha-addon/CHANGELOG.md`. Add a `## X.Y.Z` section. Source material is `dev-plans/WORKITEMS-X.Y.md` (has both completed work items and bug fixes). Group by category (features / improvements / bug fixes) and consolidate dev-iteration noise into clean user-facing descriptions.
- [ ] **Sync user-visible docs** if anything changed:
  - `README.md` — feature list, config tables, architecture.
  - `ha-addon/DOCS.md` — HA add-on panel docs.
  - `ha-addon/config.yaml` — `description`, `map`, `ports`, `options`, `schema`.
  Remove stale content (outdated diagrams, references to removed features, duplication of what the code already says).
- [ ] **Refresh `SECURITY.md`** — bump the Supported Versions table (e.g., `1.5.x → ✅ Current release`, demote the prior line to `✅ Previous stable — security fixes only if trivially backportable`, drop anything older than the previous stable). Re-read the "Security Measures" sections (Supply chain / Web surface / Protocol & validation / Auth & observability) and add bullets for hardening that landed this release; remove any bullets that no longer match shipped code. The "What is *not* in scope" list should stay aligned with `dev-plans/SECURITY_AUDIT.md`'s WONTFIX findings.
- [ ] **Refresh `dev-plans/SECURITY_AUDIT.md`** — bump the `**Last refreshed:**` date and version stamp at the top; add a refresh-note paragraph summarizing what flipped this cycle (OPEN → FIXED, new findings, status downgrades). Walk every F-* entry: each one whose status changed gets its **Status:** line rewritten with the release tag (e.g., `FIXED in 1.6.0 via SC.3 — worker pip install now hash-pinned`). New code that opens a finding gets a new F-N entry. Update the OWASP Top 10 table and the Summary Table at the bottom to match. Cross-check against the `WORKITEMS-X.Y.md` SC.* / SA.* / AU.* sections — anything checked off there must be reflected here.
- [ ] **Refresh `dev-plans/UX_REVIEW.md`** — re-do the UI walkthrough against the new release. The goal is "what an experienced UX reviewer would say *today*", not patching the prior version. Use Playwright against `http://hass-4.local:8765/` after deploy: each primary tab, every modal, the per-row hamburger, bulk-action dropdowns, mobile viewport, light + streamer mode. Update screenshots in `.playwright-mcp/ux-*.png`. For each finding from the previous review: mark as resolved (and remove) if shipped, keep + restate if still present. Add new findings the release introduced. The Prioritized Recommendations table should be re-numbered with **UX.N** entries the next release file can pick from. Update the version stamp + dev-build tag in the H1.
- [ ] **Check `docs/screenshot.png` is still representative** — compare the current Devices tab on hass-4 (`http://192.168.225.112:8765`) against the image in `docs/screenshot.png`. If columns, toolbar buttons, badges, or layout have changed meaningfully, take a fresh screenshot at ~1280px wide showing the Devices tab with a realistic device list, and replace the file. The screenshot is the GitHub README's primary hook — stale is worse than missing.
- [ ] **Close out `dev-plans/WORKITEMS-X.Y.md`**: mark all completed and carry forward anything that didn't ship. Concrete steps:
  1. `grep -nE '^- \[ \]' dev-plans/WORKITEMS-X.Y.md` — every unchecked box must be either (a) checked, (b) struck-through with `~~**ID**~~ WONTFIX —` + reason, or (c) **moved verbatim** to `dev-plans/WORKITEMS-X.Y+1.md` (or a later release file) under a `## Carried forward from X.Y` heading. Don't just delete — losing context across releases is what this checklist exists to prevent.
  2. `grep -niE 'defer|TODO|follow.?up|nice to have|tracked for later|future iteration' dev-plans/WORKITEMS-X.Y.md` — for each hit, decide: shipped + obsolete (delete the note), or still pending (move to the successor file). The phrase "deferred to <Y>" only counts as resolved if `WORKITEMS-Y.md` actually lists it.
  3. After both grep passes are clean, `git mv dev-plans/WORKITEMS-X.Y.md dev-plans/archive/`.
- [ ] **Grep TODO/FIXME/HACK** in source — `grep -rnE 'TODO|FIXME|HACK' ha-addon/ scripts/ tests/` (excluding `node_modules`, `dist`, lockfiles). Resolve, document as known issues, or move to a successor WORKITEMS file. Same forwarding rule as the dev-plans grep above — don't lose context.

### You do

- [ ] **Deploy + smoke test**: `./push-to-hass-4.sh`. Runs the full `e2e-hass-4` Playwright suite (device load, schedule upgrade, compile + OTA with live log streaming, editor edit + validate, live device logs, parallel-compile pinned to local-worker).
- [ ] **Read the changelog draft** — does it represent what users care about?
- [ ] **Sanity-check editor autocomplete on a real config** — the only thing Playwright can't verify end-to-end.
- [ ] Note any config changes that need migration notes for users upgrading.
- [ ] Decide: merge all `develop` commits, or cherry-pick?

---

## Release (merge to `main`)

### Claude does

- [ ] Create release branch if needed: `git checkout -b release/X.Y.Z develop`.
- [ ] Final commit with version + changelog + docs on `develop` (or release branch).
- [ ] Merge to main: `git checkout main && git merge develop` (or merge the release branch).
- [ ] Push: `git push origin main`. Pre-push hook runs tests + mypy + changelog check. GHCR publish workflows fire automatically.
- [ ] Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
- [ ] Verify: `gh run list --branch main --limit 3` and `gh api /orgs/{owner}/packages/container/{name}/versions --jq '.[0]'`.

---

## Post-release

### Claude does

- [ ] **Create GitHub release**: `gh release create vX.Y.Z --title "X.Y.Z — <short theme>" --notes-file <(awk '/^## X\.Y\.Z/{f=1;next} /^## /{f=0} f' ha-addon/CHANGELOG.md)`. Uses the changelog section as the release body so the GitHub Releases page stays in sync with CHANGELOG.md. Mark as latest unless it's a point release behind an active major line.
- [ ] Start next dev cycle: `git checkout develop && bash scripts/bump-dev.sh`.
- [ ] Create `dev-plans/WORKITEMS-X.Y+1.md` — copy structure from the previous file, leave items unchecked.

### You do

- [ ] Update the HA add-on repo (if using a separate repo for distribution).
- [ ] Verify the add-on updates cleanly on hass-4 from the published image.
- [ ] Post release notes if desired (GitHub release, Reddit, Discord).

---

## Reference

**`scripts/bump-version.sh X.Y.Z`** keeps these in sync:

| File | Field |
|------|-------|
| `ha-addon/VERSION` | entire content |
| `ha-addon/config.yaml` | `version:` field |
| `ha-addon/client/client.py` | `CLIENT_VERSION` constant |

**`.githooks/pre-push`** runs `pytest` + `mypy` on every push, plus a `CHANGELOG.md` entry check when pushing to `main`. Install with `bash scripts/install-hooks.sh`.
