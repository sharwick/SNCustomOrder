# SNCustomOrder — local sync tool

This repo has two independent, disjoint working trees for the same
ServiceNow scoped app:

- `25a488c5c30a8b10d7191c65e4013146/` — Studio's git-linked export
  (full-record XML + `checksum.txt`). Managed entirely by Studio's git
  integration. **Never hand-edit files here** — any change that doesn't
  come from a Studio export/checksum cycle will break re-import.
- `src/` — a REST-driven working copy holding only the `script` column
  of four tables (`sys_script_include`, `sys_script`, `sys_ui_action`,
  `sys_script_client`), managed by `sync.py`. Edit these files freely
  with your editor of choice; `git diff`, `rg`, and lints all work
  normally here.

Use Studio's git integration for full record export/import, update-set
promotion, and anything outside the four tables above. Use `sync.py` for
fast local edit/PATCH loops on scripts in those four tables.

## Setup

```bash
uv sync
cp .env.example .env   # fill in SN_INSTANCE, SN_USER, SN_PASS, SN_SCOPE_SYS_ID
```

## Usage

```bash
uv run python sync.py pull                              # all four tables
uv run python sync.py pull --table sys_script_include    # one table
uv run python sync.py pull --table sys_script_include --name OrderUtils
```

`pull` writes `src/<table>/<name>.js` per record and tracks state in
`.manifest.json` (committed to git). Re-running `pull` reconciles
renames and deletions against the manifest — see
`docs/superpowers/specs/2026-07-13-sn-sync-tool-design.md` for full
semantics.

`push`, `set-update-set`, and `status` are not implemented yet (planned
in later slices).
