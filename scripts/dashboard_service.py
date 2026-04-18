from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_DIR = ROOT / "runs" / "control"


def _windows_no_window_flags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_subprocess(command: list[str], *, timeout: int, check: bool = False) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "check": check,
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _windows_no_window_flags()
    return subprocess.run(command, **kwargs)


def _pid_file(port: int) -> Path:
    suffix = "" if port == 8765 else f"_{port}"
    return CONTROL_DIR / f"dashboard_server{suffix}.pid"


def _stdout_log(port: int) -> Path:
    suffix = "" if port == 8765 else f"_{port}"
    return CONTROL_DIR / f"dashboard_server{suffix}.stdout.log"


def _stderr_log(port: int) -> Path:
    suffix = "" if port == 8765 else f"_{port}"
    return CONTROL_DIR / f"dashboard_server{suffix}.stderr.log"


def _server_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/dashboard.html"


def _control_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/control"


def _ping_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/api/ping"


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return int(raw) if raw.isdigit() else None


def _write_pid_file(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _remove_pid_file(path: Path) -> None:
    for _ in range(10):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.15)
    return


def _pid_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = _run_subprocess(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                timeout=8,
            )
        except Exception:
            return False
        output = (result.stdout or "").strip()
        return bool(output and "No tasks are running" not in output and "INFO:" not in output)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _find_listening_pid(port: int) -> int | None:
    if os.name != "nt":
        return None

    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        (
            "$conn = Get-NetTCPConnection -LocalPort "
            f"{port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if ($conn) { $conn.OwningProcess }"
        ),
    ]
    try:
        result = _run_subprocess(command, timeout=8)
    except Exception:
        return None

    output = (result.stdout or "").strip()
    return int(output) if output.isdigit() else None


def _process_command_line(pid: int) -> str:
    if os.name != "nt":
        return ""
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        (
            "$proc = Get-CimInstance Win32_Process -Filter "
            f"\"ProcessId = {pid}\" -ErrorAction SilentlyContinue; "
            "if ($proc) { $proc.CommandLine }"
        ),
    ]
    try:
        result = _run_subprocess(command, timeout=8)
    except Exception:
        return ""
    return (result.stdout or "").strip()


def _is_managed_dashboard_process(pid: int) -> bool:
    command_line = _process_command_line(pid).lower()
    return "serve_dashboard.py" in command_line and str(ROOT).lower() in command_line


def _terminate_pid(pid: int) -> None:
    if os.name == "nt":
        _run_subprocess(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            timeout=20,
        )
    else:
        os.kill(pid, signal.SIGTERM)


def _wait_for_pid_exit(pid: int, timeout_sec: float = 10.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.3)
    return not _pid_exists(pid)


def _server_ready(port: int, timeout_sec: float = 0.8) -> bool:
    request = urllib.request.Request(
        _ping_url(port),
        headers={
            "Accept": "application/json",
            "User-Agent": "Agent-Eval-Lab/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            response.read()
            return 200 <= getattr(response, "status", 200) < 300
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        return False


def _wait_for_server(port: int, timeout_sec: float = 12.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if _server_ready(port):
            return True
        time.sleep(0.4)
    return False


def _open_browser(port: int) -> None:
    webbrowser.open(_server_url(port), new=2)


def _start_server(port: int, open_browser: bool) -> int:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file(port)
    stdout_log = _stdout_log(port)
    stderr_log = _stderr_log(port)
    pid = _read_pid_file(pid_file)
    if _pid_exists(pid) and _server_ready(port):
        if open_browser:
            _open_browser(port)
        print(f"Dashboard server already running with PID {pid}.")
        return 0

    if pid is not None and not _pid_exists(pid):
        _remove_pid_file(pid_file)

    existing_pid = _find_listening_pid(port)
    if existing_pid is not None and _pid_exists(existing_pid) and _server_ready(port):
        _write_pid_file(pid_file, existing_pid)
        if open_browser:
            _open_browser(port)
        print(f"Dashboard server already running on port {port} with PID {existing_pid}.")
        return 0
    if existing_pid is not None and _pid_exists(existing_pid):
        if _is_managed_dashboard_process(existing_pid):
            _terminate_pid(existing_pid)
            _wait_for_pid_exit(existing_pid)
            _remove_pid_file(pid_file)
        else:
            raise RuntimeError(f"Port {port} is already in use by PID {existing_pid}.")

    command = [
        sys.executable,
        str((ROOT / "scripts" / "serve_dashboard.py").resolve()),
        "--port",
        str(port),
        "--pid-file",
        str(pid_file.resolve()),
    ]
    stdout_handle = stdout_log.open("a", encoding="utf-8")
    stderr_handle = stderr_log.open("a", encoding="utf-8")
    try:
        creationflags = 0
        popen_kwargs: dict[str, object] = {
            "cwd": str(ROOT),
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_handle,
            "stderr": stderr_handle,
        }
        if os.name == "nt":
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **popen_kwargs)
    finally:
        stdout_handle.close()
        stderr_handle.close()

    if not _wait_for_server(port):
        if process.poll() is not None:
            _remove_pid_file(pid_file)
            raise RuntimeError(
                f"Dashboard server exited early with code {process.returncode}. See {stderr_log.resolve()}."
            )
        raise RuntimeError(
            f"Dashboard server did not become ready on port {port}. See {stdout_log.resolve()} and {stderr_log.resolve()}."
        )

    pid = _read_pid_file(pid_file) or process.pid
    _write_pid_file(pid_file, pid)
    if open_browser:
        _open_browser(port)
    print(f"Dashboard server started with PID {pid}.")
    return 0


def _stop_server(port: int) -> int:
    pid_file = _pid_file(port)
    pid = _read_pid_file(pid_file)
    if pid is None:
        pid = _find_listening_pid(port)

    if pid is None or not _pid_exists(pid):
        _remove_pid_file(pid_file)
        if _server_ready(port):
            raise RuntimeError(f"Dashboard server is responding on port {port}, but no managed PID was found.")
        print("Dashboard server is not running.")
        return 0

    _terminate_pid(pid)

    if not _wait_for_pid_exit(pid):
        listening_pid = _find_listening_pid(port)
        if listening_pid is None and not _server_ready(port):
            _remove_pid_file(pid_file)
            print(f"Dashboard server stopped (PID {pid}, port released).")
            return 0
        raise RuntimeError(f"Dashboard server PID {pid} did not exit cleanly.")

    _remove_pid_file(pid_file)
    print(f"Dashboard server stopped (PID {pid}).")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the local dashboard background service.")
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    os.chdir(ROOT)
    try:
        if args.action == "start":
            raise SystemExit(_start_server(args.port, open_browser=not args.no_browser))
        raise SystemExit(_stop_server(args.port))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
