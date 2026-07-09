---
name: DTL repo clone/pull safety
description: Rules for the Device Type Library clone-or-pull flow (repo.py) to avoid data loss and self-heal broken checkouts.
---

# Device Type Library clone/pull safety

Rules for `DTLRepo._clone_or_pull` / `_fresh_clone` in `devicetype_importer/repo.py`.

- A usable checkout is identified by a real `.git` directory ONLY. Never treat `.github` (or any other subfolder) as "a repo exists" — the cloned library contains `.github`, so that check routes a git-less directory into the pull path and fails.
- `_fresh_clone` must clear a pre-existing non-empty directory before `git clone` (clone refuses non-empty targets). Guard the `rmtree` against dangerous paths (root, home, cwd).
- Distinguish failure modes on pull:
  - `InvalidGitRepositoryError` → directory isn't a valid repo → safe to wipe and re-clone.
  - `GitCommandError` (transient: network/auth/diverged branch) → do NOT wipe. Proceed with the existing (possibly stale) local copy.

**Why:** An unconditional wipe-on-any-failure deletes a valid local library when a pull fails for a transient reason; if the follow-up clone also fails the user is left with nothing.
**How to apply:** Any change to the clone/pull fallback must keep the invalid-repo (wipe) vs transient-failure (keep) distinction, and must never delete a valid checkout.
