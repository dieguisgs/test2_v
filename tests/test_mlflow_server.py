"""Verify local service ownership without needing the optional MLflow package."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import textwrap

import pytest

from vwaps import mlflow_server as servers


HTTP_CHILD = textwrap.dedent("""
    import http.server
    import sys
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        def log_message(self, *args):
            pass
    http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()
""")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def owned_servers():
    before = set(servers._SERVERS)
    yield
    for key, server in list(servers._SERVERS.items()):
        if key not in before:
            server.stop()


@pytest.fixture
def local_launcher(monkeypatch, owned_servers):
    psutil = pytest.importorskip("psutil")
    monkeypatch.setattr(servers, "_dependencies", lambda: psutil)
    real_launch = servers._launch
    calls = []

    def launch(command, storage, log, library):
        port = command[command.index("--port") + 1]
        parent = "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); child.wait()"
        process, owner, job = real_launch([sys.executable, "-c", parent, HTTP_CHILD, port], storage, log, library)
        calls.append((command, process, owner, job))
        return process, owner, job

    monkeypatch.setattr(servers, "_launch", launch)
    return calls, psutil, real_launch


@pytest.mark.parametrize("port", [True, 0, -1, 65536, "5000", 1.5])
def test_invalid_ports_do_not_start_processes(tmp_path, port):
    with pytest.raises(ValueError, match="port"):
        servers.start_mlflow_server(tmp_path / "data", port=port)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("timeout", [False, 0, -1, float("inf"), float("nan"), "45"])
def test_invalid_timeouts_do_not_start_processes(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        servers.start_mlflow_server(tmp_path / "data", timeout=timeout)
    assert not (tmp_path / "data").exists()


def test_missing_optional_dependency_has_actionable_install_command(tmp_path, monkeypatch):
    monkeypatch.setattr(servers.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(servers, "_check_port", lambda port: None)
    with pytest.raises(ImportError, match="uv sync --group notebook --group experiment"):
        servers.start_mlflow_server(tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_occupied_unknown_port_is_not_adopted_or_killed(tmp_path, monkeypatch):
    monkeypatch.setattr(servers, "_dependencies", lambda: pytest.fail("Do not launch or adopt an unknown listener"))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="does not own.*another port"):
            servers.start_mlflow_server(tmp_path / "data", port=port)
        assert listener.fileno() >= 0
        assert not (tmp_path / "data").exists()


def test_service_uses_current_python_local_storage_and_hidden_windows_process(
    tmp_path, local_launcher, monkeypatch,
):
    calls, _, _ = local_launcher
    popen_calls = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        popen_calls.append(kwargs.copy())
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(servers.subprocess, "Popen", popen)
    monkeypatch.setenv("MLFLOW_BACKEND_STORE_URI", "https://unrelated.example/backend")
    storage = tmp_path / "experiment data"
    server = servers.start_mlflow_server(storage, port=free_port(), timeout=8)
    command = calls[0][0]
    assert command[:4] == [sys.executable, "-m", "mlflow", "server"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--workers") + 1] == "1"
    assert command[command.index("--backend-store-uri") + 1] == f"sqlite:///{(storage / 'mlflow.db').as_posix()}"
    assert command[command.index("--artifacts-destination") + 1] == (storage / "artifacts").as_uri()
    assert "--serve-artifacts" in command and "--default-artifact-root" not in command
    assert "MLFLOW_BACKEND_STORE_URI" not in popen_calls[0]["env"]
    if os.name == "nt":
        assert popen_calls[0]["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert popen_calls[0]["creationflags"] & 0x00000004
        assert server._job is not None
    else:
        assert popen_calls[0]["start_new_session"] is True
    assert server.storage_dir == storage.resolve()
    assert server.log_path == storage / "server.log"
    assert server._owns_listener(int(server.url.rsplit(":", 1)[1]))


def test_repeated_cells_reuse_owned_server_stop_children_and_keep_experiments(tmp_path, local_launcher):
    calls, psutil, _ = local_launcher
    storage = tmp_path / "data"
    storage.mkdir()
    database = storage / "mlflow.db"
    database.write_bytes(b"Existing experiment metadata")
    port = free_port()
    first = servers.start_mlflow_server(storage, port=port, timeout=8)
    again = servers.start_mlflow_server(str(storage), port=port, timeout=8)
    assert again is first and len(calls) == 1
    children = first._owner.children(recursive=True)
    assert children
    artifact = storage / "artifacts" / "saved.txt"
    artifact.write_text("Keep this artifact", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already occupied"):
        servers.start_mlflow_server(tmp_path / "different", port=port, timeout=1)
    first.stop()
    first.stop()
    assert first.process.poll() is not None
    assert not any(item.is_running() for item in children)
    assert database.read_bytes() == b"Existing experiment metadata"
    assert artifact.read_text(encoding="utf-8") == "Keep this artifact"
    restarted = servers.start_mlflow_server(storage, port=port, timeout=8)
    assert restarted is not first and len(calls) == 2


def test_rerunning_an_unhealthy_owned_service_reports_log_and_stops_only_it(
    tmp_path, local_launcher, monkeypatch,
):
    calls, _, _ = local_launcher
    port = free_port()
    server = servers.start_mlflow_server(tmp_path / "data", port=port, timeout=8)
    monkeypatch.setattr(server, "_owns_listener", lambda checked_port: False)
    with pytest.raises(RuntimeError, match="did not become ready.*\nLog:.*server.log"):
        servers.start_mlflow_server(tmp_path / "data", port=port, timeout=0.2)
    assert len(calls) == 1
    assert server.process.poll() is not None


def test_startup_timeout_exposes_log_and_stops_owned_process(tmp_path, local_launcher, monkeypatch):
    _, psutil, real_launch = local_launcher
    launched = []

    def launch(command, storage, log, library):
        result = real_launch([sys.executable, "-u", "-c", "import time; print('Waiting forever'); time.sleep(60)"],
                             storage, log, library)
        launched.append(result[0])
        return result

    monkeypatch.setattr(servers, "_launch", launch)
    with pytest.raises(RuntimeError, match="did not become ready.*\nLog:.*server.log\nWaiting forever"):
        servers.start_mlflow_server(tmp_path / "data", port=free_port(), timeout=0.6)
    assert launched[0].poll() is not None
    assert not any(item.storage_dir == (tmp_path / "data").resolve() for item in servers._SERVERS.values())


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership is the process-tree regression under test")
def test_early_parent_exit_still_stops_its_windows_child(tmp_path, local_launcher, monkeypatch):
    _, psutil, real_launch = local_launcher

    def launch(command, storage, log, library):
        script = ("import pathlib,subprocess,sys; "
                  "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
                  "print('Injected startup crash',flush=True); sys.exit(9)")
        return real_launch([sys.executable, "-c", script, str(storage / "child.pid")], storage, log, library)

    monkeypatch.setattr(servers, "_launch", launch)
    with pytest.raises(RuntimeError, match="exit code 9.*\nLog:.*server.log\nInjected startup crash"):
        servers.start_mlflow_server(tmp_path / "data", port=free_port(), timeout=8)
    pid = int((tmp_path / "data" / "child.pid").read_text())
    assert not psutil.pid_exists(pid)


def test_port_race_never_treats_unrelated_listener_as_ready(tmp_path, local_launcher, monkeypatch):
    _, _, real_launch = local_launcher
    monkeypatch.setattr(servers, "_check_port", lambda port: None)

    def launch(command, storage, log, library):
        return real_launch([sys.executable, "-c", "import time; time.sleep(60)"], storage, log, library)

    monkeypatch.setattr(servers, "_launch", launch)
    with socket.socket() as unrelated:
        unrelated.bind(("127.0.0.1", 0))
        unrelated.listen(4)
        with pytest.raises(RuntimeError, match="did not become ready"):
            servers.start_mlflow_server(tmp_path / "data", port=unrelated.getsockname()[1], timeout=0.4)
        assert unrelated.fileno() >= 0
