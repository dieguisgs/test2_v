"""Start and stop an owned local MLflow service from a notebook.

Only the current Python process's registry is trusted for reuse. A service
started elsewhere, including an earlier kernel, is never adopted or stopped.
SQLite metadata, artifacts and logs remain on disk after ``stop()``.
MLflow and psutil are optional and are imported only when starting a service.
"""

from __future__ import annotations

import atexit
from dataclasses import dataclass, field
import importlib
import importlib.util
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


_INSTALL = "uv sync --group notebook --group experiment"
_SERVERS: dict[tuple[str, int], "LocalMlflowServer"] = {}
_LOCK = threading.RLock()


class _WindowsJob:
    """Keep a suspended child and all its descendants in one owned Windows job."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        self._ctypes = ctypes
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self._api.CreateJobObjectW.restype = wintypes.HANDLE
        self._api.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self._api.SetInformationJobObject.restype = wintypes.BOOL
        self._api.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self._api.AssignProcessToJobObject.restype = wintypes.BOOL
        self._api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self._api.OpenProcess.restype = wintypes.HANDLE
        self._api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._api.CloseHandle.restype = wintypes.BOOL
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
        if not self._api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, pid: int) -> None:
        handle = self._api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE.
        if not handle:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            if not self._api.AssignProcessToJobObject(self._handle, handle):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._api.CloseHandle(handle)

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not self._api.CloseHandle(handle):
                raise self._ctypes.WinError(self._ctypes.get_last_error())


def _dependencies():
    if importlib.util.find_spec("mlflow") is None:
        raise ImportError(f"MLflow is optional. Install it with `{_INSTALL}`, then restart the notebook kernel. "
                          f"Current interpreter: {sys.executable}")
    try:
        return importlib.import_module("psutil")
    except ImportError as exc:
        raise ImportError(f"Local MLflow process management needs psutil. Run `{_INSTALL}` and restart the kernel.") from exc


def _log_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 8000))
            return stream.read().decode("utf-8", errors="replace").strip() or "(log is empty)"
    except OSError as exc:
        return f"(cannot read log: {exc})"


def _check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Port {port} is already occupied by a service this kernel does not own. "
                               "Choose another port or stop that service yourself; it will not be adopted or killed.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Cannot bind local MLflow port {port}. Choose another port; "
                               "no existing service was stopped.") from exc


def _launch(command: list[str], storage: Path, log_path: Path, psutil):
    options = {"cwd": storage, "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
    # Inherited MLflow routing/storage settings must not redirect this local service.
    options["env"] = {key: value for key, value in os.environ.items() if not key.startswith("MLFLOW_")}
    options["env"]["PYTHONUNBUFFERED"] = "1"
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | 0x00000004  # CREATE_SUSPENDED.
    else:
        options["start_new_session"] = True
    job = None
    with log_path.open("ab") as log:
        process = subprocess.Popen(command, stdout=log, **options)
    try:
        owner = psutil.Process(process.pid)
        if os.name == "nt":
            job = _WindowsJob()
            job.assign(process.pid)
            # No MLflow child can start before job membership is established.
            owner.resume()
        return process, owner, job
    except BaseException as exc:
        try:
            if job is not None:
                job.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        except Exception as cleanup_error:
            exc.add_note(f"Could not fully clean up owned startup process {process.pid}: {cleanup_error}")
        raise


@dataclass
class LocalMlflowServer:
    """An owned local server; ``stop`` preserves every experiment and artifact."""

    url: str
    storage_dir: Path
    log_path: Path
    process: subprocess.Popen
    _psutil: object = field(repr=False)
    _owner: object = field(repr=False)
    _job: object = field(default=None, repr=False)
    _known: dict = field(default_factory=dict, repr=False)
    _stopped: bool = field(default=False, repr=False)

    def _owned_processes(self) -> list:
        psutil = self._psutil
        try:
            if self._owner.is_running():
                for item in [self._owner, *self._owner.children(recursive=True)]:
                    self._known[(item.pid, item.create_time())] = item
        except psutil.NoSuchProcess:
            pass
        return [item for item in self._known.values() if item.is_running()]

    def _owns_listener(self, port: int) -> bool:
        for item in self._owned_processes():
            try:
                for connection in item.net_connections(kind="tcp"):
                    if (connection.status == self._psutil.CONN_LISTEN and connection.laddr.port == port
                            and connection.laddr.ip == "127.0.0.1"):
                        return True
            except self._psutil.NoSuchProcess:
                continue
        return False

    def stop(self) -> None:
        """Stop only owned processes, including Windows workers; keep all files."""
        with _LOCK:
            if self._stopped:
                return
            processes = self._owned_processes()
            if self._job is not None:
                self._job.close()
                self._job = None
            else:
                # A live, creation-time-checked owned process must still belong
                # to this group before its ID can be used for a tree signal.
                for item in processes:
                    try:
                        if os.getpgid(item.pid) == self.process.pid:
                            os.killpg(self.process.pid, signal.SIGTERM)
                            break
                    except (ProcessLookupError, self._psutil.NoSuchProcess):
                        continue
            _, alive = self._psutil.wait_procs(processes, timeout=3)
            for item in alive:
                try:
                    item.kill()  # psutil protects against PID reuse.
                except self._psutil.NoSuchProcess:
                    pass
            self._psutil.wait_procs(alive, timeout=3)
            self.process.wait(timeout=3)
            self._stopped = True
            for key, value in list(_SERVERS.items()):
                if value is self:
                    _SERVERS.pop(key)


def _wait_ready(server: LocalMlflowServer, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))  # Never send localhost checks to an HTTP proxy.
    while time.monotonic() < deadline:
        code = server.process.poll()
        if code is not None:
            raise RuntimeError(f"MLflow exited during startup (exit code {code}).")
        if server._owns_listener(port):
            try:
                with opener.open(f"{server.url}/health", timeout=min(1.0, max(0.05, deadline - time.monotonic()))) as response:
                    if response.status == 200 and server.process.poll() is None and server._owns_listener(port):
                        return
            except (OSError, URLError):
                pass
        time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    raise TimeoutError(f"MLflow did not become ready at {server.url} within {timeout:g} seconds.")


def _startup_failed(exc: BaseException, server: LocalMlflowServer | None, log_path: Path):
    cleanup = ""
    if server is not None:
        try:
            server.stop()
        except Exception as cleanup_error:
            cleanup = f"\nCould not fully stop owned MLflow processes: {cleanup_error}"
            exc.add_note(cleanup.strip())
    detail = f"MLflow startup failed: {exc}{cleanup}\nLog: {log_path}\n{_log_tail(log_path)}"
    if not isinstance(exc, Exception):
        exc.add_note(detail)
        raise exc
    raise RuntimeError(detail) from exc


def start_mlflow_server(storage_dir: Path | str, *, port: int = 5000,
                        timeout: float = 45.0) -> LocalMlflowServer:
    """Return a ready owned service, reusing repeated calls from this kernel.

    Metadata uses ``storage_dir/mlflow.db`` and proxied artifacts use
    ``storage_dir/artifacts``. Logs are appended to ``storage_dir/server.log``.
    Only IPv4 loopback is bound. An occupied port is never adopted, including
    a server from a different kernel. Startup failures stop owned children and
    report the log path/tail. A normal kernel shutdown also stops owned servers.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number of seconds")
    storage = Path(storage_dir).expanduser().resolve()
    key = (os.path.normcase(str(storage)), port)
    with _LOCK:
        existing = _SERVERS.get(key)
        if existing is not None:
            if not existing._stopped and existing.process.poll() is None:
                try:
                    _wait_ready(existing, port, timeout)
                    return existing
                except BaseException as exc:
                    _startup_failed(exc, existing, existing.log_path)
            existing.stop()
        _check_port(port)
        psutil = _dependencies()
        storage.mkdir(parents=True, exist_ok=True)
        artifacts = storage / "artifacts"
        artifacts.mkdir(exist_ok=True)
        log_path = storage / "server.log"
        sqlite = f"sqlite:///{(storage / 'mlflow.db').as_posix()}"
        command = [sys.executable, "-m", "mlflow", "server", "--host", "127.0.0.1", "--port", str(port),
                   "--workers", "1", "--backend-store-uri", sqlite, "--registry-store-uri", sqlite,
                   "--serve-artifacts", "--artifacts-destination", artifacts.as_uri()]
        server = None
        try:
            process, owner, job = _launch(command, storage, log_path, psutil)
            server = LocalMlflowServer(f"http://127.0.0.1:{port}", storage, log_path,
                                       process, psutil, owner, job)
            _SERVERS[key] = server
            _wait_ready(server, port, timeout)
            return server
        except BaseException as exc:
            _startup_failed(exc, server, log_path)


def _stop_all() -> None:
    for server in list(_SERVERS.values()):
        try:
            server.stop()
        except Exception:
            pass  # Interpreter shutdown must not affect persisted experiments.


atexit.register(_stop_all)
