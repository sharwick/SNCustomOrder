# ServiceNow local sync tool — design

**Status:** approved, slice 1 (`pull`) ready for implementation planning
**Date:** 2026-07-13
**Original brief:** [`docs/ai-prompts/2026-07-13-sync-tool-brief.md`](../../ai-prompts/2026-07-13-sync-tool-brief.md)

## Problem

Editing scripts (business rules, script includes, UI actions, client
scripts) inside the ServiceNow web IDE is slow and provides no local
tooling: no editor of choice, no git diff, no `rg`, no lints. The
scoped app under `25a488c5c30a8b10d7191c65e4013146/` is already linked
to Studio's git integration, but Studio's export/import round-trip is
heavy and enforces a checksum on the whole folder — it isn't designed
for a fast local-edit loop.

Goal: a Python CLI (`sync.py`) that mirrors the `script` column of
select tables to local `.js` files, PATCHes changes back via the REST
Table API, and detects conflicts when someone edits the same record
in the UI. The tool is orthogonal to Studio's git integration and does
not touch its files.

## Non-goals

- Full record export/import (that is Studio's job).
- Fields other than `script` for the four target tables (slice 1).
- OAuth, MFA, or multi-instance token management.
- Diffing against the server on `status` (network-free by design).
- Any automation of update-set promotion, XML export, or Studio
  operations.

## Scope of this document

This design covers **slice 1 only: repository layout, configuration,
manifest schema, auth/session strategy, and the `pull` command.**

Slices 2–4 (`push`, `set-update-set`, `status`) will each get an
addendum design after the previous slice is verified against the PDI.
This staged approach mirrors the user's request to test each piece
before moving on.

## Repository layout

```
SNCustomOrder/
├── 25a488c5c30a8b10d7191c65e4013146/   # Studio-managed — off limits
├── docs/
│   ├── ai-prompts/                     # prompts driving this work
│   └── superpowers/specs/              # design docs (this file)
├── src/                                # Table-API-managed script files
│   ├── sys_script_include/
│   ├── sys_script/
│   ├── sys_ui_action/
│   └── sys_script_client/
├── tests/
│   └── test_*.py
├── .manifest.json                      # sys_id + sys_updated_on per file
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── sync.py
```

Two disjoint working trees, on purpose:

- `25a488c5c30a8b10d7191c65e4013146/` is Studio's git-linked export.
  It contains full-record XML and a `checksum.txt` that Studio verifies
  when re-importing. Any edit here that doesn't come from Studio breaks
  the checksum and blocks import.
- `src/` is our REST-driven working copy holding only the `script`
  column of each record. Studio never reads this tree.

Both trees can live in the same git repo. The README will describe
when to use each mechanism.

## Configuration

Loaded via `python-dotenv` from `.env` at repo root (not committed):

| Variable            | Purpose                                                     |
| ------------------- | ----------------------------------------------------------- |
| `SN_INSTANCE`       | Host, e.g. `dev183527.service-now.com` (no scheme)          |
| `SN_USER`           | Basic Auth username                                         |
| `SN_PASS`           | Basic Auth password                                         |
| `SN_SCOPE_SYS_ID`   | Default scope filter for pulls; e.g. the SNCustomOrder scope |

CLI flags override env vars:

- `--instance <host>` — overrides `SN_INSTANCE`
- `--scope <sys_id>` — overrides `SN_SCOPE_SYS_ID`

Missing required env vars cause an immediate hard error naming the
missing variable, per the project CLAUDE.md secrets policy.

`.env.example` is committed with placeholder values so a new clone
knows what to fill in.

## Auth and HTTP session

Basic Auth via a single `requests.Session()` reused across the entire
CLI invocation. Rationale:

- Reused sessions keep the TCP connection warm across many requests.
- More importantly, ServiceNow's login path is expensive — session
  bootstrap warms ACL caches and evaluates login-time business rules.
  Doing that per-request would multiply latency on every pull.
- Basic Auth is fine for a PDI. If this tool ever runs against a shared
  instance, we upgrade to OAuth in a later slice.

No retries in slice 1. If the PDI returns non-2xx, we surface the
status and body to the user and exit non-zero. Retries with backoff
are a slice-2 concern (push is idempotent-ish and benefits more).

## Target tables and columns pulled

| Table               | Purpose               | Columns fetched                                                                |
| ------------------- | --------------------- | ------------------------------------------------------------------------------ |
| `sys_script_include` | Server-side utility libs | `sys_id, name, script, sys_updated_on, active, api_name, access`               |
| `sys_script`        | Business rules        | `sys_id, name, script, sys_updated_on, active, collection, when, order, condition` |
| `sys_ui_action`     | Form buttons / links  | `sys_id, name, script, sys_updated_on, active, table, action_name`             |
| `sys_script_client` | Client scripts        | `sys_id, name, script, sys_updated_on, active, table, type`                    |

Only `script` is written to the `.js` file. The other columns are
stored in the manifest so future slices (and `status`) can display
context without another round-trip. This list is the seed; users can
expand it by editing a constant in `sync.py` — no CLI flag until real
demand appears.

## Table API request shape

For each configured table, `pull` issues:

```
GET https://<SN_INSTANCE>/api/now/table/<table>
    ?sysparm_query=sys_scope=<SN_SCOPE_SYS_ID>
    &sysparm_fields=<comma-separated columns for this table>
    &sysparm_display_value=false
    &sysparm_exclude_reference_link=true
    &sysparm_limit=1000
Accept: application/json
Authorization: Basic <base64(user:pass)>
```

Each query parameter is deliberate:

- **`sysparm_fields`** — only the columns above. Prevents the API from
  serializing all ~90 columns of `sys_script`. Every column has an
  ACL evaluated per row, so trimming the field list cuts ACL work
  proportionally.
- **`sysparm_display_value=false`** — raw sys_ids for reference fields
  (`collection`, `sys_scope`, `table`) instead of resolved display
  strings. Skips a join per reference field per row.
- **`sysparm_exclude_reference_link=true`** — omit the
  `{link: "https://..."}` sidecar for each reference. Never useful for
  local editing; always dead weight.
- **`sysparm_limit=1000`** — one page is enough for a scoped app.
  Pagination is not implemented in slice 1; if a request returns
  exactly the limit, we log a warning and exit non-zero rather than
  silently truncating.

The URL is built with `urllib.parse.urlencode` — never string
concatenation — so scope sys_ids and other values are properly encoded.

## Filename strategy

Two-pass rename during `pull` per target table:

1. Compute `safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', name)` for every
   record.
2. For any `safe_name` that appears more than once within the table,
   rename all colliding records to `<safe_name>__<first 6 chars of sys_id>.js`.
   Non-colliding records keep the clean `<safe_name>.js`.

Result: unique records get readable filenames, and a collision only
uglifies the specific names that conflict. The `__` separator is
deliberate — a bare `_` would be ambiguous with sanitization output.

Empty or whitespace-only `name` values fall back to `<sys_id>.js`.

## Manifest

Single JSON file at repo root, `.manifest.json`, committed to git so
the checkout state is reproducible.

```json
{
  "instance": "dev183527.service-now.com",
  "scope_sys_id": "25a488c5c30a8b10d7191c65e4013146",
  "pulled_at": "2026-07-13T21:04:11Z",
  "records": {
    "sys_script_include/OrderUtils.js": {
      "table": "sys_script_include",
      "sys_id": "a3f2c9...",
      "name": "OrderUtils",
      "sys_updated_on": "2026-07-10 19:14:57",
      "sha256_at_pull": "<hex>",
      "extras": {
        "active": "true",
        "api_name": "x_1836523_sncust_0.OrderUtils",
        "access": "package_private"
      }
    }
  }
}
```

Keys are file paths relative to `src/`. This makes lookup from a file
path (needed by `push` in slice 2 and `status` in slice 4) trivial.

Because filenames can shift when a collision is introduced or resolved
(record renamed on the server, or a new record starts colliding with
an existing one), `pull` reconciles by `sys_id`, not by path:

1. Index the existing manifest by `sys_id`.
2. For each server record, look up by `sys_id`. If the previously
   written path differs from the new safe/de-collided path, delete
   the old `.js` file and log the rename before writing the new one.
3. Rewrite the manifest with paths as keys.

Reconciling by `sys_id` is the only stable identifier — names are not
unique across time either.

- `sys_updated_on` is the conflict token compared on `push`.
- `sha256_at_pull` is the pristine hash; `status` compares it against
  the current file to detect local edits with zero network calls.
- `extras` holds the per-table extra columns without giving them
  top-level manifest keys (keeps the schema shape stable as extras
  grow).

`pulled_at` and top-level `instance` / `scope_sys_id` guard against
accidentally running `push` against a manifest built from a different
instance or scope.

## `pull` command semantics

```
python sync.py pull                              # all four tables
python sync.py pull --table sys_script_include   # one table
python sync.py pull --table sys_script_include --name OrderUtils
```

Behavior:

1. Load `.env` and validate required vars.
2. Build the target-table list from `--table` (or all four).
3. For each table:
   1. Issue the Table API request above; add `name=<value>` to
      `sysparm_query` if `--name` is passed.
   2. Sanitize + de-collide filenames as described.
   3. Write each `.js` file (create directories as needed).
   4. Update the manifest entry for each file with the fresh
      `sys_updated_on`, `sha256_at_pull`, and `extras`.
4. Reconcile deletions against the scope of *this* pull:
   - Full pull (no `--table`, no `--name`): drop manifest entries for
     any of the four target tables whose `sys_id` was not seen in the
     server response. These are records that were deleted or moved
     out of scope.
   - `--table` only: drop manifest entries under that table that were
     not seen. Manifest entries for other tables are untouched.
   - `--name` filter: never drop anything. A `--name` pull is a
     targeted refresh, not a full inventory.
   Log every removal so the user can spot unintended deletions.
5. Write the manifest atomically (write to `.manifest.json.tmp`,
   `os.replace`).
6. Print a summary: files written, collisions renamed, deletions,
   elapsed time.

Behavior on empty / null `script`: skip with a per-record warning.
These records still get manifest entries (with an empty file) so
`push` can decide what to do later.

Behavior on unexpected HTTP failure: print status, body, and exit
non-zero. No partial manifest write — the previous manifest stays
intact.

## Testing

Per project CLAUDE.md, tests only for non-trivial logic.

Unit tests (`pytest`, using `tmp_path`):

- Filename sanitization and collision two-pass.
- Manifest merge: writing over existing entries, dropping deleted
  records, preserving unrelated entries.
- Table API query builder: correct params, correct URL encoding,
  `--name` optionally appended to `sysparm_query`.

Not tested by unit tests (verified manually against the PDI):

- The actual HTTP round-trip.
- Auth failures, scope filter, real record shapes.

No mocks of the ServiceNow API. If we want more coverage later, we
add contract tests against a recorded PDI response fixture — but not
in slice 1.

## Explicit non-behaviors in slice 1

- No `push`. No dry-run flag.
- No update-set awareness — anything the tool eventually PATCHes will
  land in whatever update set the user is currently on.
- No handling of records outside the configured scope.
- No touching of `25a488c5c30a8b10d7191c65e4013146/` or Studio APIs.
- No pagination (a single 1000-row page covers the app; overflow is
  a loud error).

## Open questions

None blocking. Two things worth deciding *before* slice 2 (push):

- Whether we surface per-table "extras" as `--field` CLI flags or
  keep the seed list constant-only.
- Whether `push` should optimistically-lock on
  `sha256_at_pull + local_sha256` in addition to `sys_updated_on`,
  to catch the "someone else pulled but hasn't pushed yet" case on a
  shared repo.

These do not affect slice 1.
