# Contributing to ESPHome Fleet

Thanks for taking the time — a quick tour of how this repo works so you can get a PR landing without surprises.

## Quick orientation

- **`develop`** is the trunk. Every change lands here first; `-dev.N` versions live on this branch. Open your PR against `develop`, not `main`.
- **`main`** holds tagged stable releases (`vX.Y.Z`). Don't push to it directly — releases flow through a PR from `develop`.
- **`CLAUDE.md`** at the repo root is the deep reference for conventions, enforced invariants, and the design philosophy. If something in here is terse and you want the "why," that's where the longer version lives.
- **`dev-plans/README.md`** indexes the release work-item files. The current release is named at the top; closed releases are under `dev-plans/archive/`.

## Running the tests

Three suites, all should pass before you push:

```bash
# 1. Python unit + integration tests (server + worker + integration logic).
pytest tests/

# 2. Frontend build + typecheck.
cd ha-addon/ui && npm run build

# 3. Mocked Playwright end-to-end tests. Runs against a production
#    build of the UI with every API route stubbed — no backend required.
cd ha-addon/ui && npx playwright test
```

A handful of extras that CI runs — worth having locally if you're editing the touched areas:

- `ruff check ha-addon/server/ ha-addon/client/` — Python lint (zero warnings bar).
- `mypy ha-addon/server/ --ignore-missing-imports` / `mypy ha-addon/client/ --ignore-missing-imports` — type check.
- `bash scripts/check-invariants.sh` — grep-based enforcement of the architectural rules documented in `CLAUDE.md` → Enforced Invariants.

## End-of-turn / end-of-PR loop

The project uses a dev-rev versioning scheme to keep every push identifiable:

1. Make your code changes.
2. `bash scripts/bump-dev.sh` — increments `-dev.N` across `ha-addon/VERSION`, `ha-addon/config.yaml`, and `ha-addon/client/client.py` in one shot.
3. `./push-to-hass-4.sh` — optional for external contributors; maintainers use it to deploy to a local Home Assistant test instance and run the `e2e-hass-4` Playwright smoke suite.
4. Commit + push to your branch.

## Bug numbering

Bugs land in the current `dev-plans/WORKITEMS-X.Y.md` file under **Open Bugs & Tweaks**, numbered globally and monotonically across releases. `- [x] **#NNN** *(X.Y.Z-dev.N)* — description` is the standard entry shape (keep the exact dev version, don't write a generic `dev`). Work items (features) use workstream codes like `AV.1`, `SS.2`, `QS.3` instead of numeric IDs.

When a release ships, its WORKITEMS file moves to `dev-plans/archive/` and a new one (either the pre-planned next release or `WORKITEMS-X.Y+1.md`) becomes the active file. See `dev-plans/RELEASE_CHECKLIST.md` for the full release flow.

## Enforced invariants

Some rules are enforced mechanically by `scripts/check-invariants.sh` (runs in CI and blocks the merge). The full list lives in `CLAUDE.md` → Enforced Invariants; highlights:

- **UI-1** — No `fetch()` outside `ha-addon/ui/src/api/`. All HTTP goes through the api layer.
- **UI-2** — No Tailwind `@apply`. Utility classes in JSX; CSS files only for things Tailwind can't express.
- **UI-3** — No `any` in new TypeScript. Use `unknown` or a real type.
- **PY-6** — `ha-addon/server/protocol.py` and `ha-addon/client/protocol.py` must stay byte-identical.
- **PY-8** — Every direct dep in `requirements.txt` must appear in `requirements.lock`. Run `bash scripts/refresh-deps.sh` after any `requirements.txt` edit.
- **PY-10** — `tests/test_integration_*.py` (without a `_logic` suffix) must import `pytest_homeassistant_custom_component`.

If you trip one of these, CI will tell you which. The fix is always to comply with the invariant; don't add `# noqa` / `// eslint-disable` without a reason comment per **PY-5**.

## PR process

1. Open the PR against `develop`.
2. CI runs lint + tests + the mocked Playwright suite + the real-ESPHome compile-test matrix.
3. Address every review comment (Copilot bot and/or human reviewer) in the same push. Resolve each review thread after the fix lands — an unresolved thread looks like an open concern even after the code is fixed. See `CLAUDE.md` → PR Review Loop for the `gh api graphql` incantations.
4. For items you want to defer: file a work-item in the relevant `dev-plans/WORKITEMS-*.md`, reply to the review comment with a pointer, then resolve the thread.

## Where to ask

- Questions about **how** to do something → `CLAUDE.md` is the best single-file reference.
- Questions about **what** to do → `dev-plans/WORKITEMS-X.Y.md` for the current release, `dev-plans/USER_PERSONA.md` for scope/UX decisions.
- Anything else → open a GitHub issue.
