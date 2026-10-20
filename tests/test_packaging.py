"""Check build metadata without building or loading real OAuth credentials."""
import json
from pathlib import Path
from types import SimpleNamespace


def test_downloaded_client_filename_is_normalized_in_bundle(tmp_path, monkeypatch):
    client = tmp_path / "client_secret_download.json"
    client.write_text(json.dumps({"installed": {"client_id": "synthetic-client"}}), encoding="utf-8")
    monkeypatch.setenv("XCAL_PUBLIC_OAUTH_CLIENT_FILE", str(client))

    def analysis(_scripts, **kwargs):
        return SimpleNamespace(
            datas=[(str(Path(destination) / Path(source).name), source, "DATA")
                   for source, destination in kwargs["datas"]],
            pure=[], scripts=[], binaries=[])

    root = Path(__file__).resolve().parents[1]
    namespace = {"SPECPATH": str(root), "Analysis": analysis,
                 "PYZ": lambda *args, **kwargs: None,
                 "EXE": lambda *args, **kwargs: None,
                 "COLLECT": lambda *args, **kwargs: None}
    spec = root / "xCAL.spec"
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), namespace)
    bundled = [entry for entry in namespace["analysis"].datas if entry[1] == str(client)]
    assert bundled == [("resources/oauth-client.json", str(client), "DATA")]
