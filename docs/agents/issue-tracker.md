# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Closing a spec (implement-spec, code-review, retro)

This repo closes work with a commit, not a PR. Four edits in one commit, in this order:

1. Each ticket: `**Status:** ready-for-agent` → `**Status:** resolved`, and every `- [ ]` → `- [x]`.
2. `spec.md`: top `Status:` line → `Status: resolved`.
3. `spec.md`: append a final `## 实现结果` section — branch name + HEAD, what shipped, test counts,
   how it was verified, what review rounds found and fixed, and the known boundaries.
4. The commit message says which spec it closes.

Precedent: `63dd039` (P0), `267185b` + `a72797e` (v0.1.1).

A ticked box claims the bullet is **satisfied**, not merged: the integration branch counts.

Every bullet names its **observable** — the command, exit code, error code, or file state a
skeptic can check. A bullet that only says "must not miss a Skill" cannot be ticked honestly,
because the same two designs (hard-coded list vs derived floor) both claim it; the observable
(`删掉一个 Skill 目录 → install-sim 退出非零`) picks one. `.scratch/*/issues/*.md` bullets are
checked mechanically: no backticked path, error code, exit code, or `→` ⇒ rewrite the bullet.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.
