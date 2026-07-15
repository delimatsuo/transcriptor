# Repository Preservation Inventory

**Captured:** 2026-07-15

**Worktree:** `/Volumes/Extreme Pro/myprojects/Transcriptor`

**Branch:** `staging`, aligned with `origin/staging` before local changes

**Purpose:** Prevent existing user work, generated files, ignored files, and planning artifacts from being mixed or lost before a clean native-companion baseline is established.

This inventory did not initially authorize staging, committing, branching, pushing, deleting, or moving files. The user authorized the preservation and clean-baseline sequence on 2026-07-15; the executed boundary is recorded below.

## Preservation executed

- Base commit: `69729ca127a10551c122b4b44ca1d35479343a24` (`origin/staging` at capture time).
- Local preservation branch: `codex/preserve-speaker-correlation-wip-20260715`.
- Preservation commit: `f5fc9f61cfddfc67de6bb2cf7af7c23f402c9840`.
- Scope: the seven tracked speaker-correlation changes plus the untracked backend utilities, speaker correlator, and Meet extension source.
- The ignored `extension/manifest.json` was force-added intentionally.
- `AGENTS.md`, `frontend/tsconfig.tsbuildinfo`, local `.env`, virtual environments, documentation, and containment configuration were excluded.
- The preservation branch is local and was not pushed.

The original worktree remains unchanged and dirty. The preservation commit is a recoverable snapshot, not a claim that the unfinished feature is reviewed, approved, tested, or suitable for the Phase 1 baseline.

## Pre-existing tracked source files with local modifications

- `backend/main.py`
- `backend/schemas/models.py`
- `backend/sessions/manager.py`
- `backend/storage/firestore.py`
- `frontend/src/components/TranscriptPanel.tsx`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/types/ws.ts`

These changes appear related to speaker correlation and must be treated as pre-existing user work.

## Tracked documentation/configuration reconciliation changes

- `.claude/deploy-config.yaml`
- `.env.example`
- `.github/workflows/deploy.yml`
- `.github/workflows/rollback.yml`
- `DEPLOY-SETUP.md`
- `Dockerfile`
- `firestore.rules`

These changes include the documentation reconciliation and Phase 0B defensive configuration: the checked-in workflow is manual build-only with no deployment job, release targets are safety sentinels, and Firestore client rules fail closed. At the time of the inventory they remained uncommitted and unpushed. Live out-of-band containment is recorded separately in `docs/current-state/phase-0b-containment-evidence.md`.

## Untracked implementation work

- `backend/speaker_correlation.py`
- `backend/utils/__init__.py`
- `backend/utils/sanitize.py`
- `extension/background.js`
- `extension/content/meet-observer.js`
- `extension/content/meet-selectors.js`
- `extension/icons/icon16.png`
- `extension/icons/icon48.png`
- `extension/icons/icon128.png`
- `extension/popup/popup.css`
- `extension/popup/popup.html`
- `extension/popup/popup.js`

## Ignored implementation work

- `extension/manifest.json`

The manifest is ignored by `.gitignore`'s broad `*.json` rule. A normal untracked-file inventory or preservation commit can omit it silently.

## Untracked planning and generated context

- `README.md` and the files under `docs/` contain the documentation reconciliation package.
- `AGENTS.md` is generated agent-memory context and is non-normative.
- `frontend/tsconfig.tsbuildinfo` is generated TypeScript build state and should not be treated as source or intentionally preserved without a specific reason.

## Required refresh before any worktree or branch action

1. Run `git status --short --branch --untracked-files=all`.
2. Run `git ls-files --others --ignored --exclude-standard` for the extension and other in-scope paths.
3. Run `git check-ignore -v extension/manifest.json`.
4. Review the complete diff of every tracked modified file.
5. Decide which files are user-authored source, generated output, or documentation.
6. Verify that any preservation method includes the ignored manifest and excludes generated build output.
7. Record the chosen branch/worktree name and base commit.
8. Re-run the inventory after preservation and before creating the native-companion baseline.

## Preservation acceptance gate

Preservation is complete only when:

- Every intended tracked, untracked, and ignored source file is recoverable.
- The extension manifest is included intentionally.
- Generated `tsbuildinfo` is excluded intentionally.
- No existing source change is overwritten by documentation work.
- The selected clean baseline and preserved-work location are named explicitly.
- The user separately authorizes the branch/worktree operation.

Current result: the WIP source is recoverable at the named local commit, the ignored manifest is included, generated build state is excluded, and no source change was overwritten. The containment commit and clean implementation worktree are recorded separately after they are created.
