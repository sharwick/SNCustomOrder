from __future__ import annotations

import hashlib

import pytest

from sync import (
    TABLES,
    Config,
    ConfigError,
    assign_filenames,
    build_query_params,
    build_records_for_table,
    build_table_url,
    load_config,
    load_manifest,
    prune_stale,
    record_to_entry,
    save_manifest,
)


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
    assert entry["sha256_at_pull"] == hashlib.sha256(b"var x = 1;").hexdigest()


def test_record_to_entry_empty_script_hashes_empty_string():
    record = {"sys_id": "id1", "name": "Empty", "script": "", "sys_updated_on": "now"}
    entry = record_to_entry("sys_script", record)
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
    assert url == (
        "https://dev183527.service-now.com/api/now/table/sys_script"
        "?sysparm_query=sys_scope%3Dscope+123&sysparm_fields=a%2Cb"
    )


def test_tables_constant_has_four_target_tables():
    assert set(TABLES.keys()) == {
        "sys_script_include",
        "sys_script",
        "sys_ui_action",
        "sys_script_client",
    }
    assert "script" in TABLES["sys_script_include"]
    assert "sys_id" in TABLES["sys_script"]
