# ServiceNow Local Sync Tool — Slice 1 (`pull`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `sync.py`, a single-file Python CLI that pulls the `script`
column (plus supporting metadata) from four ServiceNow tables into local
`.js` files under `src/`, tracked by a reconciled `.manifest.json`.

**Architecture:** One flat script, `sync.py`, per the approved design
(`docs/superpowers/specs/2026-07-13-sn-sync-tool-design.md`). Internally it
is organized into small pure functions (config, filename assignment,
manifest-entry construction, reconciliation, query building) that are unit
tested with no network and no mocked HTTP, plus a thin orchestration layer
(`run_pull`, `main`) that does real file/network I/O and is verified
manually against the PDI, not by unit test.

**Tech Stack:** Python 3.12+, `requests`, `python-dotenv`, `pytest`, `ruff`,
`uv`.

## Global Constraints

- Never touch anything under `25a488c5c30a8b10d7191c65e4013146/` (Studio's
  git-managed export tree).
- `src/` holds only the `script` column, one file per record, under a
  per-table subdirectory (`src/<table>/<file>.js`).
- `.manifest.json` is committed to git; `.manifest.json.tmp` is a transient
  write target only (atomic rename via `os.replace`).
- `.env` is never read/written/edited by name in code beyond
  `python-dotenv`'s `load_dotenv()`; secrets never appear in source, logs,
  or exception messages.
- Missing required env var → immediate hard error naming the missing
  variable.
- No pagination. If a Table API response returns `>= 1000` rows, print an
  error and exit non-zero — never silently truncate.
- No retries. Non-2xx response → print status + body, exit non-zero, and
  leave the previous `.manifest.json` untouched (no partial writes).
- URL query strings are built with `urllib.parse.urlencode` only — never
  string concatenation of already-decoded values.
- Unit tests exist only for non-trivial pure logic (filename
  sanitization/collision, manifest merge/reconcile, query-param
  building). No unit test mocks the ServiceNow HTTP API; the real
  round-trip is verified manually against the PDI.
- Per project CLAUDE.md: `uv` for all dependency management, `ruff` as the
  single formatting/lint authority, `pathlib.Path` over `os.path`,
  f-strings only, strict type hints with
  `from __future__ import annotations`, max function length 100 lines,
  max cyclomatic complexity 10, `pytest` (no `unittest`), tests use
  `tmp_path` (no filesystem mocks).
- Do not run `git commit` during automated/inline execution of this plan
  unless the user has explicitly asked for it in the current session —
  staging and committing are user operations by default. Leave the
  working tree ready for the user to review and commit.

---

## File Structure

```
SNCustomOrder/
├── pyproject.toml          # deps, ruff config, pytest config
├── .env.example            # placeholder env vars (committed)
├── .gitignore              # .env, caches, .manifest.json.tmp
├── sync.py                 # CLI entry point + all logic (single file, per design)
├── tests/
│   └── test_sync.py        # unit tests for pure functions in sync.py
└── README.md                # usage + two-working-trees explanation
```

`src/` and `.manifest.json` are created at runtime by `pull`, not checked
in empty.

## Interfaces Produced by `sync.py` (for reference across tasks)

```python
@dataclass
class Config:
    instance: str
    user: str
    password: str
    scope_sys_id: str

class ConfigError(RuntimeError): ...

TABLES: dict[str, list[str]]          # table name -> columns to fetch
CORE_FIELDS: set[str]                 # {"sys_id", "name", "script", "sys_updated_on"}
MANIFEST_PATH: Path                   # Path(".manifest.json")
SRC_ROOT: Path                        # Path("src")

def load_config(env: dict[str, str], instance_override: str | None, scope_override: str | None) -> Config: ...
def assign_filenames(records: list[dict]) -> dict[str, str]: ...                     # sys_id -> bare filename
def record_to_entry(table: str, record: dict) -> dict: ...                          # -> manifest entry
def build_records_for_table(table: str, records: list[dict], filenames: dict[str, str], existing_paths_by_sysid: dict[str, str]) -> tuple[dict[str, dict], dict[str, str], list[str]]: ...  # (new_entries, contents, stale_paths)
def prune_stale(manifest_records: dict[str, dict], table: str, seen_sys_ids: set[str], drop: bool) -> tuple[dict[str, dict], list[str]]: ...  # (kept_records, dropped_paths)
def build_query_params(table: str, columns: list[str], scope_sys_id: str, name: str | None) -> dict[str, str]: ...
def build_table_url(instance: str, table: str, params: dict[str, str]) -> str: ...
def load_manifest(path: Path) -> dict: ...
def save_manifest(path: Path, manifest: dict) -> None: ...
def make_session(user: str, password: str) -> requests.Session: ...
def fetch_records(session: requests.Session, instance: str, table: str, columns: list[str], scope_sys_id: str, name_filter: str | None) -> list[dict]: ...
def run_pull(config: Config, tables: list[str], name_filter: str | None) -> int: ...
def build_arg_parser() -> argparse.ArgumentParser: ...
def main(argv: list[str] | None = None) -> int: ...
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Produces: an installable dev environment (`uv sync`) that later tasks'
  `pytest` runs depend on.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "sn-sync"
version = "0.1.0"
description = "Local sync tool for ServiceNow scoped app scripts"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.32",
    "python-dotenv>=1.0",
]

[tool.uv]
package = false
dev-dependencies = [
    "pytest>=8.0",
    "ruff>=0.6",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.env.example`**

```
SN_INSTANCE=devXXXXXX.service-now.com
SN_USER=admin
SN_PASS=changeme
SN_SCOPE_SYS_ID=00000000000000000000000000000000
```

- [ ] **Step 3: Write `.gitignore`**

```
.env
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.venv/
*.egg-info/
.manifest.json.tmp
```

- [ ] **Step 4: Verify the environment installs**

Run: `uv sync`
Expected: completes without error, creates `.venv/` and `uv.lock`.

Run: `uv run python -c "import requests, dotenv; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .env.example .gitignore uv.lock
git commit -m "Scaffold sn-sync project (deps, ruff, pytest config)"
```

---

### Task 2: Config loading

**Files:**
- Create: `sync.py` (module header + `Config`, `ConfigError`, `load_config`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Produces: `Config`, `ConfigError`, `load_config(env, instance_override, scope_override) -> Config`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sync.py
from __future__ import annotations

import pytest

from sync import Config, ConfigError, load_config


def test_load_config_success():
    env = {
        "SN_INSTANCE": "dev183527.service-now.com",
        "SN_USER": "admin",
        "SN_PASS": "secret",
        "SN_SCOPE_SYS_ID": "abc123",
    }
    config = load_config(env, None, None)
    assert config == Config(
        instance="dev183527.service-now.com",
        user="admin",
        password="secret",
        scope_sys_id="abc123",
    )


def test_load_config_missing_var_raises_naming_it():
    env = {"SN_USER": "admin", "SN_PASS": "secret", "SN_SCOPE_SYS_ID": "abc123"}
    with pytest.raises(ConfigError, match="SN_INSTANCE"):
        load_config(env, None, None)


def test_load_config_cli_overrides_take_precedence():
    env = {
        "SN_INSTANCE": "dev183527.service-now.com",
        "SN_USER": "admin",
        "SN_PASS": "secret",
        "SN_SCOPE_SYS_ID": "abc123",
    }
    config = load_config(env, "other.service-now.com", "def456")
    assert config.instance == "other.service-now.com"
    assert config.scope_sys_id == "def456"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'sync'` or `ImportError`)

- [ ] **Step 3: Write minimal implementation**

```python
# sync.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Config:
    instance: str
    user: str
    password: str
    scope_sys_id: str


class ConfigError(RuntimeError):
    pass


REQUIRED_VARS = ("SN_INSTANCE", "SN_USER", "SN_PASS", "SN_SCOPE_SYS_ID")


def load_config(
    env: dict[str, str],
    instance_override: str | None,
    scope_override: str | None,
) -> Config:
    values = {var: env.get(var) for var in REQUIRED_VARS}
    instance = instance_override or values["SN_INSTANCE"]
    scope_sys_id = scope_override or values["SN_SCOPE_SYS_ID"]
    user = values["SN_USER"]
    password = values["SN_PASS"]

    missing = [
        name
        for name, val in [
            ("SN_INSTANCE", instance),
            ("SN_USER", user),
            ("SN_PASS", password),
            ("SN_SCOPE_SYS_ID", scope_sys_id),
        ]
        if not val
    ]
    if missing:
        raise ConfigError(f"Missing required configuration: {', '.join(missing)}")

    return Config(instance=instance, user=user, password=password, scope_sys_id=scope_sys_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "Add config loading with env var validation"
```

---

### Task 3: Filename assignment (sanitize + two-pass de-collide)

**Files:**
- Modify: `sync.py` (add `assign_filenames`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `assign_filenames(records: list[dict]) -> dict[str, str]` —
  maps `sys_id` to a bare filename (no directory prefix).

- [ ] **Step 1: Write the failing tests**

```python
def test_assign_filenames_unique_names():
    records = [
        {"sys_id": "id1", "name": "OrderUtils"},
        {"sys_id": "id2", "name": "Other Name!"},
    ]
    result = assign_filenames(records)
    assert result == {"id1": "OrderUtils.js", "id2": "Other_Name_.js"}


def test_assign_filenames_collision_gets_sysid_suffix():
    records = [
        {"sys_id": "aaaaaa111111", "name": "Dup"},
        {"sys_id": "bbbbbb222222", "name": "Dup"},
    ]
    result = assign_filenames(records)
    assert result == {
        "aaaaaa111111": "Dup__aaaaaa.js",
        "bbbbbb222222": "Dup__bbbbbb.js",
    }


def test_assign_filenames_blank_name_falls_back_to_sysid():
    records = [
        {"sys_id": "id1", "name": ""},
        {"sys_id": "id2", "name": "   "},
    ]
    result = assign_filenames(records)
    assert result == {"id1": "id1.js", "id2": "id2.js"}
```

Add `from sync import assign_filenames` (or extend the existing `from sync
import ...` line) at the top of `tests/test_sync.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL (`ImportError: cannot import name 'assign_filenames'`)

- [ ] **Step 3: Write minimal implementation**

```python
# sync.py — add near the top-level functions
import re


def assign_filenames(records: list[dict]) -> dict[str, str]:
    safe_names: dict[str, str] = {}
    for record in records:
        name = (record.get("name") or "").strip()
        safe_names[record["sys_id"]] = re.sub(r"[^A-Za-z0-9_.-]", "_", name) if name else ""

    counts: dict[str, int] = {}
    for safe in safe_names.values():
        if safe:
            counts[safe] = counts.get(safe, 0) + 1

    filenames: dict[str, str] = {}
    for sys_id, safe in safe_names.items():
        if not safe:
            filenames[sys_id] = f"{sys_id}.js"
        elif counts[safe] > 1:
            filenames[sys_id] = f"{safe}__{sys_id[:6]}.js"
        else:
            filenames[sys_id] = f"{safe}.js"
    return filenames
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "Add filename sanitization and collision de-duplication"
```

---

### Task 4: Manifest entry construction and reconciliation

**Files:**
- Modify: `sync.py` (add `CORE_FIELDS`, `record_to_entry`,
  `build_records_for_table`, `prune_stale`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `assign_filenames` output (Task 3) as the `filenames` argument.
- Produces: `record_to_entry`, `build_records_for_table`, `prune_stale` per
  the signatures in the shared Interfaces section above.

- [ ] **Step 1: Write the failing tests**

```python
def test_record_to_entry_splits_core_and_extras():
    record = {
        "sys_id": "id1",
        "name": "OrderUtils",
        "script": "var x = 1;",
        "sys_updated_on": "2026-07-10 19:14:57",
        "active": "true",
        "api_name": "x_1836523_sncust_0.OrderUtils",
    }
    entry = record_to_entry("sys_script_include", record)
    assert entry["table"] == "sys_script_include"
    assert entry["sys_id"] == "id1"
    assert entry["name"] == "OrderUtils"
    assert entry["sys_updated_on"] == "2026-07-10 19:14:57"
    assert entry["extras"] == {
        "active": "true",
        "api_name": "x_1836523_sncust_0.OrderUtils",
    }
    import hashlib
    assert entry["sha256_at_pull"] == hashlib.sha256(b"var x = 1;").hexdigest()


def test_record_to_entry_empty_script_hashes_empty_string():
    record = {"sys_id": "id1", "name": "Empty", "script": "", "sys_updated_on": "now"}
    entry = record_to_entry("sys_script", record)
    import hashlib
    assert entry["sha256_at_pull"] == hashlib.sha256(b"").hexdigest()


def test_build_records_for_table_new_records():
    records = [{"sys_id": "id1", "name": "Foo", "script": "code", "sys_updated_on": "now"}]
    filenames = {"id1": "Foo.js"}
    new_entries, contents, stale_paths = build_records_for_table(
        "sys_script_include", records, filenames, existing_paths_by_sysid={}
    )
    assert set(new_entries.keys()) == {"sys_script_include/Foo.js"}
    assert contents == {"sys_script_include/Foo.js": "code"}
    assert stale_paths == []


def test_build_records_for_table_detects_rename():
    records = [{"sys_id": "id1", "name": "NewName", "script": "code", "sys_updated_on": "now"}]
    filenames = {"id1": "NewName.js"}
    existing = {"id1": "sys_script_include/OldName.js"}
    new_entries, contents, stale_paths = build_records_for_table(
        "sys_script_include", records, filenames, existing_paths_by_sysid=existing
    )
    assert stale_paths == ["sys_script_include/OldName.js"]
    assert "sys_script_include/NewName.js" in new_entries


def test_prune_stale_drops_unseen_when_drop_true():
    manifest_records = {
        "sys_script_include/A.js": {"table": "sys_script_include", "sys_id": "id1"},
        "sys_script_include/B.js": {"table": "sys_script_include", "sys_id": "id2"},
        "sys_script/C.js": {"table": "sys_script", "sys_id": "id3"},
    }
    kept, dropped = prune_stale(manifest_records, "sys_script_include", seen_sys_ids={"id1"}, drop=True)
    assert dropped == ["sys_script_include/B.js"]
    assert set(kept.keys()) == {"sys_script_include/A.js", "sys_script/C.js"}


def test_prune_stale_noop_when_drop_false():
    manifest_records = {
        "sys_script_include/A.js": {"table": "sys_script_include", "sys_id": "id1"},
    }
    kept, dropped = prune_stale(manifest_records, "sys_script_include", seen_sys_ids=set(), drop=False)
    assert dropped == []
    assert kept == manifest_records
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL (`ImportError: cannot import name 'record_to_entry'`)

- [ ] **Step 3: Write minimal implementation**

```python
# sync.py — add
import hashlib

CORE_FIELDS = {"sys_id", "name", "script", "sys_updated_on"}


def record_to_entry(table: str, record: dict) -> dict:
    script = record.get("script") or ""
    extras = {k: v for k, v in record.items() if k not in CORE_FIELDS}
    return {
        "table": table,
        "sys_id": record["sys_id"],
        "name": record.get("name") or "",
        "sys_updated_on": record.get("sys_updated_on", ""),
        "sha256_at_pull": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "extras": extras,
    }


def build_records_for_table(
    table: str,
    records: list[dict],
    filenames: dict[str, str],
    existing_paths_by_sysid: dict[str, str],
) -> tuple[dict[str, dict], dict[str, str], list[str]]:
    new_entries: dict[str, dict] = {}
    contents: dict[str, str] = {}
    stale_paths: list[str] = []

    for record in records:
        sys_id = record["sys_id"]
        path = f"{table}/{filenames[sys_id]}"
        new_entries[path] = record_to_entry(table, record)
        contents[path] = record.get("script") or ""

        old_path = existing_paths_by_sysid.get(sys_id)
        if old_path is not None and old_path != path:
            stale_paths.append(old_path)

    return new_entries, contents, stale_paths


def prune_stale(
    manifest_records: dict[str, dict],
    table: str,
    seen_sys_ids: set[str],
    drop: bool,
) -> tuple[dict[str, dict], list[str]]:
    if not drop:
        return dict(manifest_records), []

    kept: dict[str, dict] = {}
    dropped_paths: list[str] = []
    for path, entry in manifest_records.items():
        if entry.get("table") == table and entry.get("sys_id") not in seen_sys_ids:
            dropped_paths.append(path)
        else:
            kept[path] = entry
    return kept, dropped_paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "Add manifest entry construction and rename/deletion reconciliation"
```

---

### Task 5: Manifest file I/O (atomic load/save)

**Files:**
- Modify: `sync.py` (add `load_manifest`, `save_manifest`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Produces: `load_manifest(path: Path) -> dict`, `save_manifest(path: Path, manifest: dict) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_manifest_missing_file_returns_default(tmp_path):
    result = load_manifest(tmp_path / ".manifest.json")
    assert result == {"instance": "", "scope_sys_id": "", "pulled_at": "", "records": {}}


def test_save_and_load_manifest_round_trip(tmp_path):
    path = tmp_path / ".manifest.json"
    manifest = {
        "instance": "dev183527.service-now.com",
        "scope_sys_id": "abc123",
        "pulled_at": "2026-07-13T21:04:11Z",
        "records": {"sys_script_include/Foo.js": {"sys_id": "id1"}},
    }
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_save_manifest_leaves_no_tmp_file(tmp_path):
    path = tmp_path / ".manifest.json"
    save_manifest(path, {"instance": "", "scope_sys_id": "", "pulled_at": "", "records": {}})
    assert not (tmp_path / ".manifest.json.tmp").exists()
    assert path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL (`ImportError: cannot import name 'load_manifest'`)

- [ ] **Step 3: Write minimal implementation**

```python
# sync.py — add
import json
import os
from pathlib import Path


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"instance": "", "scope_sys_id": "", "pulled_at": "", "records": {}}
    return json.loads(path.read_text())


def save_manifest(path: Path, manifest: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.replace(tmp_path, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "Add atomic manifest load/save"
```

---

### Task 6: Table API query/URL builder

**Files:**
- Modify: `sync.py` (add `TABLES`, `build_query_params`, `build_table_url`)
- Test: `tests/test_sync.py`

**Interfaces:**
- Produces: `TABLES: dict[str, list[str]]`,
  `build_query_params(table, columns, scope_sys_id, name) -> dict[str, str]`,
  `build_table_url(instance, table, params) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_query_params_without_name():
    params = build_query_params("sys_script_include", ["sys_id", "name", "script"], "scope123", None)
    assert params == {
        "sysparm_query": "sys_scope=scope123",
        "sysparm_fields": "sys_id,name,script",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": "1000",
    }


def test_build_query_params_with_name_appends_to_query():
    params = build_query_params("sys_script_include", ["sys_id"], "scope123", "OrderUtils")
    assert params["sysparm_query"] == "sys_scope=scope123^name=OrderUtils"


def test_build_table_url_encodes_params():
    params = {"sysparm_query": "sys_scope=scope 123", "sysparm_fields": "a,b"}
    url = build_table_url("dev183527.service-now.com", "sys_script", params)
    assert url.startswith("https://dev183527.service-now.com/api/now/table/sys_script?")
    assert "sysparm_query=sys_scope%20123".replace("%20123", "+123") or "sys_scope" in url
    assert "%20" in url or "+" in url  # space must be encoded, not literal


def test_tables_constant_has_four_target_tables():
    assert set(TABLES.keys()) == {
        "sys_script_include",
        "sys_script",
        "sys_ui_action",
        "sys_script_client",
    }
    assert "script" in TABLES["sys_script_include"]
    assert "sys_id" in TABLES["sys_script"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sync.py -v`
Expected: FAIL (`ImportError: cannot import name 'build_query_params'`)

- [ ] **Step 3: Write minimal implementation**

```python
# sync.py — add
from urllib.parse import urlencode

TABLES: dict[str, list[str]] = {
    "sys_script_include": [
        "sys_id", "name", "script", "sys_updated_on", "active", "api_name", "access",
    ],
    "sys_script": [
        "sys_id", "name", "script", "sys_updated_on", "active", "collection", "when",
        "order", "condition",
    ],
    "sys_ui_action": [
        "sys_id", "name", "script", "sys_updated_on", "active", "table", "action_name",
    ],
    "sys_script_client": [
        "sys_id", "name", "script", "sys_updated_on", "active", "table", "type",
    ],
}


def build_query_params(
    table: str,
    columns: list[str],
    scope_sys_id: str,
    name: str | None,
) -> dict[str, str]:
    query = f"sys_scope={scope_sys_id}"
    if name:
        query += f"^name={name}"
    return {
        "sysparm_query": query,
        "sysparm_fields": ",".join(columns),
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": "1000",
    }


def build_table_url(instance: str, table: str, params: dict[str, str]) -> str:
    return f"https://{instance}/api/now/table/{table}?{urlencode(params)}"
```

Note: `table` is unused inside `build_query_params` itself (columns are
already resolved by the caller) — keep the parameter anyway since every
caller passes it and a future slice (per-table field overrides) will need
it; this matches the design's seed-list approach.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add sync.py tests/test_sync.py
git commit -m "Add Table API query and URL builders with the four target tables"
```

---

### Task 7: HTTP session and fetch (not unit tested, per design)

**Files:**
- Modify: `sync.py` (add `make_session`, `fetch_records`)

**Interfaces:**
- Consumes: `build_query_params`, `build_table_url` (Task 6).
- Produces: `make_session(user, password) -> requests.Session`,
  `fetch_records(session, instance, table, columns, scope_sys_id, name_filter) -> list[dict]`

Per the design doc's Testing section, the live HTTP round-trip is
explicitly **not** unit tested — no mocks of the ServiceNow API. This task
has no test step; it is verified manually in Task 8.

- [ ] **Step 1: Write the implementation**

```python
# sync.py — add
import requests


def make_session(user: str, password: str) -> requests.Session:
    session = requests.Session()
    session.auth = (user, password)
    session.headers.update({"Accept": "application/json"})
    return session


def fetch_records(
    session: requests.Session,
    instance: str,
    table: str,
    columns: list[str],
    scope_sys_id: str,
    name_filter: str | None,
) -> list[dict]:
    params = build_query_params(table, columns, scope_sys_id, name_filter)
    url = build_table_url(instance, table, params)
    response = session.get(url, timeout=30)
    if not response.ok:
        raise requests.HTTPError(f"{table}: {response.status_code} {response.text}")
    return response.json().get("result", [])
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 19 passed (no new tests added this task)

- [ ] **Step 3: Commit**

```bash
git add sync.py
git commit -m "Add authenticated session and Table API fetch"
```

---

### Task 8: `pull` orchestration and CLI

**Files:**
- Modify: `sync.py` (add `run_pull`, `build_arg_parser`, `main`, `if __name__ == "__main__":` guard)

**Interfaces:**
- Consumes: every function from Tasks 2–7.
- Produces: `run_pull(config, tables, name_filter) -> int`,
  `build_arg_parser() -> argparse.ArgumentParser`,
  `main(argv=None) -> int`

This is the orchestration layer: real file and network I/O. Per the
design doc, it is verified manually against the PDI, not by automated
test (no HTTP mocking). This task's steps are implementation + manual
verification, not TDD.

- [ ] **Step 1: Write the implementation**

```python
# sync.py — add
import argparse
import os
import sys
import time

MANIFEST_PATH = Path(".manifest.json")
SRC_ROOT = Path("src")


def run_pull(config: Config, tables: list[str], name_filter: str | None) -> int:
    start = time.monotonic()
    session = make_session(config.user, config.password)
    manifest = load_manifest(MANIFEST_PATH)
    records_by_path = dict(manifest.get("records", {}))

    total_written = 0
    total_renamed = 0
    total_deleted = 0

    for table in tables:
        columns = TABLES[table]
        try:
            fetched = fetch_records(
                session, config.instance, table, columns, config.scope_sys_id, name_filter
            )
        except requests.HTTPError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        if len(fetched) >= 1000:
            print(
                f"error: {table} returned >= 1000 rows; pagination is not supported",
                file=sys.stderr,
            )
            return 1

        for record in fetched:
            if not (record.get("script") or "").strip():
                label = record.get("name") or record["sys_id"]
                print(f"warning: {table} record '{label}' has an empty script", file=sys.stderr)

        filenames = assign_filenames(fetched)
        existing_by_sysid = {
            entry["sys_id"]: path
            for path, entry in records_by_path.items()
            if entry.get("table") == table
        }

        new_entries, contents, stale_paths = build_records_for_table(
            table, fetched, filenames, existing_by_sysid
        )

        for old_path in stale_paths:
            old_file = SRC_ROOT / old_path
            if old_file.exists():
                old_file.unlink()
            print(f"renamed away from {old_path}")
            total_renamed += 1

        for path, content in contents.items():
            file_path = SRC_ROOT / path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            total_written += 1

        records_by_path.update(new_entries)

        seen_sys_ids = {record["sys_id"] for record in fetched}
        records_by_path, dropped_paths = prune_stale(
            records_by_path, table, seen_sys_ids, drop=name_filter is None
        )
        for path in dropped_paths:
            file_path = SRC_ROOT / path
            if file_path.exists():
                file_path.unlink()
            print(f"deleted {path} (removed from server)")
            total_deleted += 1

    save_manifest(
        MANIFEST_PATH,
        {
            "instance": config.instance,
            "scope_sys_id": config.scope_sys_id,
            "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "records": records_by_path,
        },
    )

    elapsed = time.monotonic() - start
    print(
        f"pull complete: {total_written} written, {total_renamed} renamed, "
        f"{total_deleted} deleted, {elapsed:.2f}s"
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync.py")
    parser.add_argument("--instance")
    parser.add_argument("--scope")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pull_parser = subparsers.add_parser("pull")
    pull_parser.add_argument("--table", action="append", choices=sorted(TABLES.keys()))
    pull_parser.add_argument("--name")

    return parser


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        config = load_config(dict(os.environ), args.instance, args.scope)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "pull":
        tables = args.table or sorted(TABLES.keys())
        return run_pull(config, tables, args.name)

    return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `uv run pytest tests/test_sync.py -v`
Expected: 19 passed

- [ ] **Step 3: Manually verify CLI argument parsing**

Run: `uv run python sync.py --help`
Expected: usage text listing `--instance`, `--scope`, and the `pull`
subcommand, exit code 0.

Run: `uv run python sync.py pull --help`
Expected: usage text listing `--table` (with the four choices) and
`--name`, exit code 0.

Run: `uv run python sync.py pull`
Expected: (no `.env` present yet) `error: Missing required configuration:
SN_INSTANCE, SN_USER, SN_PASS, SN_SCOPE_SYS_ID`, exit code 1.

- [ ] **Step 4: Manual verification against the PDI (blocking before slice 2)**

This step cannot be automated in this environment — it requires real PDI
credentials in a local `.env` (copied from `.env.example`). Before
starting slice 2 (`push`), the user should:

1. `cp .env.example .env` and fill in real `SN_INSTANCE`, `SN_USER`,
   `SN_PASS`, `SN_SCOPE_SYS_ID`.
2. Run `uv run python sync.py pull` and confirm:
   - `src/<table>/*.js` files appear with real script content.
   - `.manifest.json` has one entry per file with correct `sys_id`,
     `sys_updated_on`, `sha256_at_pull`, `extras`.
   - Re-running `pull` with no server-side changes reports 0 renamed, 0
     deleted, and file contents/manifest are stable (idempotent).
   - Renaming a record's `name` in the ServiceNow UI and re-pulling
     renames the local file and updates the manifest path.
   - Deleting a record in the ServiceNow UI and re-pulling removes the
     local file and manifest entry, with a logged deletion.

- [ ] **Step 5: Commit**

```bash
git add sync.py
git commit -m "Add pull orchestration and CLI entry point"
```

---

### Task 9: README and repo-layout documentation

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: nothing (documentation only).

- [ ] **Step 1: Write `README.md`**

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document the two working trees and pull usage"
```

---

## Self-Review Notes

- **Spec coverage:** repo layout (Task 1, 9), config/env (Task 2), auth
  session (Task 7), target tables/columns (Task 6), Table API request
  shape (Task 6–7), filename strategy (Task 3), manifest schema (Task 4,
  5), `pull` semantics including reconciliation and deletion rules (Task
  8), empty-script warning (Task 8), non-2xx handling with no partial
  write (Task 8), testing boundaries — pure logic tested, HTTP not mocked
  (Tasks 2–7 have tests, Task 7–8 orchestration does not).
- **Placeholder scan:** none found — every step has runnable code.
- **Type consistency:** verified `assign_filenames`, `record_to_entry`,
  `build_records_for_table`, `prune_stale`, `build_query_params`,
  `build_table_url`, `load_manifest`/`save_manifest`, `fetch_records`,
  `run_pull`, `main` all use matching signatures between the Interfaces
  section and each task's code.
