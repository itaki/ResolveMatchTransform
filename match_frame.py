"""ResolveMatchTransform — Resolve Scripts menu entry point.

Resolve runs this script in its bundled Python. Because Resolve uses Qt
internally, we don't show a PyQt6 window in-process — instead we spawn a
detached subprocess that runs the panel under the user's system Python,
which is assumed to have PyQt6/OpenCV/numpy installed (see setup.sh).

Output (incl. subprocess stdout/stderr) is tee'd into LOG_PATH so we can
diagnose silent failures when Resolve's Console doesn't surface them.
"""

import os
import shutil
import subprocess
import sys
import time


def _locate_script_dir() -> str:
    # Resolve's script runner exec()s the file without setting __file__, so we
    # try a chain of sources to find where this script actually lives.
    candidates: list[str] = []
    try:
        candidates.append(__file__)  # standard Python
    except NameError:
        pass
    if sys.argv and sys.argv[0]:
        candidates.append(sys.argv[0])
    # Last-resort known install path (the launcher that setup.sh creates).
    candidates.append(os.path.expanduser(
        "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/ResolveMatchTransform.py"
    ))
    for c in candidates:
        if c and os.path.exists(c):
            return os.path.dirname(os.path.realpath(c))  # realpath follows the symlink
    raise RuntimeError(
        "Could not determine script directory — no __file__, no sys.argv[0], "
        "and the expected launcher at ~/Library/Application Support/Blackmagic "
        "Design/DaVinci Resolve/Fusion/Scripts/Utility/ResolveMatchTransform.py is missing."
    )


SCRIPT_DIR = _locate_script_dir()
PANEL = os.path.join(SCRIPT_DIR, "ui", "panel.py")
PINNED_PYTHON_FILE = os.path.join(SCRIPT_DIR, ".python_path")
LOG_PATH = os.path.expanduser("~/Library/Logs/ResolveMatchTransform.log")
PID_FILE = os.path.expanduser("~/Library/Logs/ResolveMatchTransform/panel.pid")

# Standard macOS install locations from CLAUDE.md.
DEFAULT_RESOLVE_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
DEFAULT_RESOLVE_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

# Common locations for Homebrew / system python3 — Resolve launched from
# Finder usually has a minimal PATH that omits these.
PATH_PREPEND = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]

REQUIRED_MODULES = ("PyQt6.QtWidgets", "cv2", "numpy")


def _log(line: str) -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
    # Resolve's bundled Python may default stdout to ASCII; encode defensively.
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _find_python() -> str:
    # 1. Pinned interpreter written by setup.sh — survives Resolve's minimal env.
    if os.path.exists(PINNED_PYTHON_FILE):
        with open(PINNED_PYTHON_FILE) as fh:
            pinned = fh.read().strip()
        if pinned and os.path.exists(pinned):
            return pinned

    # 2. PATH lookup (only useful when Resolve happens to inherit a useful PATH).
    env_path = os.pathsep.join(PATH_PREPEND + [os.environ.get("PATH", "")])
    for name in ("python3", "python"):
        cand = shutil.which(name, path=env_path)
        if cand and not cand.startswith("/Applications/DaVinci Resolve"):
            return cand

    # 3. Hardcoded fallbacks.
    for fallback in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
        if os.path.exists(fallback):
            return fallback
    raise RuntimeError("No external python3 found — install via Homebrew: brew install python")


def _precheck(python: str, env: dict) -> tuple[bool, str]:
    """Verify the target python can import the modules panel.py needs."""
    check = "import importlib, sys\n" + "\n".join(
        f"importlib.import_module({m!r})" for m in REQUIRED_MODULES
    ) + "\nprint('precheck OK', sys.version.split()[0])"
    result = subprocess.run(
        [python, "-c", check], env=env, capture_output=True, text=True, timeout=30,
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _existing_panel_pid():
    """Return the PID of a running panel.py, or None if no live panel."""
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)  # signal 0 = liveness check, no actual signal sent
    except OSError:
        return None
    # Confirm it's actually our panel and not a recycled PID.
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
        if "panel.py" in result.stdout:
            return pid
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _raise_to_front(pid: int) -> None:
    """Bring the panel window to the front via AppleScript."""
    script = (
        f'tell application "System Events" to '
        f'set frontmost of (every process whose unix id is {pid}) to true'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        _log(f"raise-to-front via osascript failed: {exc}")


def main() -> int:
    # Truncate the log per run so the user always sees the latest attempt.
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        open(LOG_PATH, "w", encoding="utf-8").close()
    except OSError:
        pass

    _log(f"ResolveMatchTransform launcher — Resolve python: {sys.executable}")
    _log(f"Log file: {LOG_PATH}")

    existing = _existing_panel_pid()
    if existing is not None:
        _log(f"Panel already running (pid={existing}); bringing to front, not spawning a new one")
        _raise_to_front(existing)
        return 0

    env = os.environ.copy()
    env.setdefault("RESOLVE_SCRIPT_API", DEFAULT_RESOLVE_API)
    env.setdefault("RESOLVE_SCRIPT_LIB", DEFAULT_RESOLVE_LIB)

    modules = os.path.join(env["RESOLVE_SCRIPT_API"], "Modules")
    pp = env.get("PYTHONPATH", "")
    parts = [p for p in pp.split(os.pathsep) if p]
    for needed in (modules, SCRIPT_DIR):
        if needed not in parts:
            parts.append(needed)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PATH"] = os.pathsep.join(PATH_PREPEND + [env.get("PATH", "")])

    try:
        python = _find_python()
    except RuntimeError as exc:
        _log(f"FATAL: {exc}")
        return 1
    _log(f"External python: {python}")

    ok, output = _precheck(python, env)
    _log(f"Dependency precheck: {'OK' if ok else 'FAILED'}")
    if output:
        for line in output.splitlines():
            _log(f"  {line}")
    if not ok:
        _log("Run setup.sh to install PyQt6, opencv-python, numpy for the python above.")
        _log(f"  cd {SCRIPT_DIR} && PYTHON={python} bash setup.sh")
        return 1

    log_fh = open(LOG_PATH, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [python, PANEL],
        env=env,
        cwd=SCRIPT_DIR,
        start_new_session=True,
        stdout=log_fh,
        stderr=log_fh,
    )
    _log(f"Spawned panel subprocess pid={proc.pid}")
    # Brief settle — if the subprocess crashes during import, surface it now.
    time.sleep(0.5)
    rc = proc.poll()
    if rc is not None:
        _log(f"Panel exited immediately with code {rc} — see log above for traceback.")
        return 1
    _log("Panel is running. If you don't see a window, check Mission Control / other Spaces.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
