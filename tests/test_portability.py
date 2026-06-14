"""Phase 7 — history portability: native-dir migration + export/import."""
import asyncio
import base64
import zipfile

import pytest

from fiskeretta import storage


class _FakeWS:
    def __init__(self):
        self.closed = False
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


def _point_storage_at(monkeypatch, cfg):
    monkeypatch.setattr(storage, "CONFIG_DIR", cfg)
    monkeypatch.setattr(storage, "SCANS_DIR", cfg / "scans")
    monkeypatch.setattr(storage, "EVENTS_FILE", cfg / "events.jsonl")


def test_is_safe_member_guards_zip_slip():
    assert storage._is_safe_member("scans/scan-1.json")
    assert storage._is_safe_member("events.jsonl")
    assert storage._is_safe_member("bms_dids.json")
    assert not storage._is_safe_member("../etc/passwd")
    assert not storage._is_safe_member("scans/../../x.json")
    assert not storage._is_safe_member("/abs/path.json")
    assert not storage._is_safe_member("random.txt")
    assert not storage._is_safe_member("scans/")


def test_migrate_legacy_dir_copies_then_removes(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    (legacy / "scans").mkdir(parents=True)
    (legacy / "scans" / "scan-1.json").write_text('{"summary":{}}')
    (legacy / "events.jsonl").write_text('{"when":"t","type":"clear"}\n')
    native = tmp_path / "native" / "Fiskeretta"
    monkeypatch.setattr(storage, "CONFIG_DIR", native)
    monkeypatch.setattr(storage, "LEGACY_CONFIG_DIR", legacy)

    assert storage.migrate_legacy_dir() is True
    assert (native / "scans" / "scan-1.json").is_file()
    assert (native / "events.jsonl").is_file()
    assert not legacy.exists()
    # second run is a no-op (native already exists), legacy already gone
    assert storage.migrate_legacy_dir() is False


def test_migrate_noop_when_native_exists(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(storage, "CONFIG_DIR", native)
    monkeypatch.setattr(storage, "LEGACY_CONFIG_DIR", legacy)
    assert storage.migrate_legacy_dir() is False
    assert legacy.exists()  # left untouched


def test_export_import_roundtrip(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    _point_storage_at(monkeypatch, cfg)
    (cfg / "scans").mkdir(parents=True)
    (cfg / "scans" / "scan-A.json").write_text('{"summary":{"active":1}}')
    storage.record_clear({"active": 1, "historical": 2, "comm": 0})

    zpath = storage.export_history(tmp_path / "out.zip")
    assert zpath.is_file()

    cfg2 = tmp_path / "cfg2"
    _point_storage_at(monkeypatch, cfg2)
    res = storage.import_history(zpath)
    assert res == {"scans_added": 1, "events_added": 1}
    assert (cfg2 / "scans" / "scan-A.json").is_file()
    assert len(storage.list_events()) == 1

    # re-import is idempotent: scan already present, event deduped by (when, type)
    assert storage.import_history(zpath) == {"scans_added": 0, "events_added": 0}


def test_import_rejects_foreign_zip(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    _point_storage_at(monkeypatch, cfg)
    z = tmp_path / "foreign.zip"
    with zipfile.ZipFile(z, "w") as zz:
        zz.writestr("random.txt", "hi")
        zz.writestr("../escape.json", "{}")
    with pytest.raises(ValueError):
        storage.import_history(z)


def test_run_import_history_decodes_data_url(tmp_path, monkeypatch):
    from fiskeretta import server
    cfg = tmp_path / "cfg"
    _point_storage_at(monkeypatch, cfg)
    (cfg / "scans").mkdir(parents=True)
    (cfg / "scans" / "scan-A.json").write_text('{"summary":{}}')
    zpath = storage.export_history(tmp_path / "out.zip")
    data_url = "data:application/zip;base64," + base64.b64encode(zpath.read_bytes()).decode()

    cfg2 = tmp_path / "cfg2"
    _point_storage_at(monkeypatch, cfg2)
    ws = _FakeWS()
    asyncio.run(server.run_import_history(ws, data_url))

    result = next(m for m in ws.sent if m["type"] == "import_result")
    assert result["ok"] is True and result["scans_added"] == 1
    assert (cfg2 / "scans" / "scan-A.json").is_file()
    assert not (cfg2 / "_import.tmp.zip").exists()  # temp cleaned up
