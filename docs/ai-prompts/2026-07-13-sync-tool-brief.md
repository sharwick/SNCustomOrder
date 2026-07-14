# Original brief — ServiceNow local sync tool

Captured verbatim from the conversation on 2026-07-13. Preserved so the
design doc's requirements can be traced back to what was actually asked
for.

---

I'm building a local development loop against a ServiceNow Personal Developer
Instance (PDI) so I can edit platform scripts as local files and sync them
via the REST Table API. Explain platform concepts as you go, particularly as they related to performance issues.

## Environment
- PDI URL: https://dev183527.service-now.com
- Release: [e.g., Xanadu — check via "stats.do" or System Diagnostics]
- Auth: local admin user; credentials should be read from environment
  variables or a .env file, never hardcoded
- Local tooling: Python 3.11+, requests, macOS/Linux shell
- Create an appropriate subfolder in the repo for AI Prompts/Project documents like this one.

## What to build
A Python CLI tool (sync.py) that:

1. **pull**: Downloads records from these tables into local .js files,
   organized as ./src/<table_name>/<record_name>.js
   - sys_script_include (script includes)
   - sys_script (business rules)
   - sys_ui_action (UI actions)
   - sys_script_client (Client Scripts)
   Store each record's sys_id and sys_updated_on in a local manifest
   (JSON) so we can detect conflicts.

2. **push <file>**: Uploads a modified local file back to the correct
   record via PATCH, using the sys_id from the manifest.
   - Before pushing, GET the record and compare sys_updated_on against
     the manifest; abort with a warning if the server copy changed
     (someone edited in the UI).

3. **set-update-set <name>**: Sets my user's current update set by
   writing the sys_update_set user preference via the Table API, so
   pushed changes are captured in the right update set instead of
   Default. Create the update set first if it doesn't exist.

4. **status**: Diffs local files against the manifest to show what's
   modified locally.

## Constraints
- Use only the standard Table API (/api/now/table/...); no plugins or
  extensions I'd have to install.
- Handle the fact that business rule and UI action "names" aren't unique —
  disambiguate filenames with a short sys_id suffix.
- Query only records where sys_scope is [my scoped app's scope = 25a488c5c30a8b10d7191c65e4013146];
  make this configurable.
- Include a --dry-run flag on push.
- Add a brief README explaining the workflow and its limitations
  (e.g., what this approach misses vs. update sets / source control).

Start by showing me the project structure and the pull command; I'll test
each piece against my PDI before we move to the next.

## Clarifications resolved during brainstorming

- **Auth mechanism:** Basic Auth. Simplest for a PDI; upgrade path to
  OAuth is available if the tool graduates beyond local development.
- **Filename disambiguation:** `name.js` when unique;
  `name__<6-char sys_id>.js` only for records whose sanitized name
  collides with another in the same table. Two-pass rename during pull.
- **Prompt / design doc location:** `docs/ai-prompts/` for prompts like
  this one; `docs/superpowers/specs/` for brainstorming-produced design
  docs (per skill convention).
- **Coexistence with Studio git integration:** The existing folder
  `25a488c5c30a8b10d7191c65e4013146/` is Studio-managed (checksum.txt,
  full-record XML). The sync tool operates in a disjoint `./src/` tree
  and never touches Studio's tree. README will explain when to use
  which mechanism.
- **Iterative delivery:** `pull` is slice 1. `push`, `set-update-set`,
  and `status` each get their own design pass and implementation slice
  after `pull` is verified against the PDI.
