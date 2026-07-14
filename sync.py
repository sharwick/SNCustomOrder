from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import requests


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


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"instance": "", "scope_sys_id": "", "pulled_at": "", "records": {}}
    return json.loads(path.read_text())


def save_manifest(path: Path, manifest: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    os.replace(tmp_path, path)


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
