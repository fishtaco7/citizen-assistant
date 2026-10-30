"""Update this install from the public GitHub repository.

The overlay is a folder of Python files, so updating means replacing those
files and restarting the process: there is nothing to install and no registry
entry to rewrite.  The work is deliberately conservative:

* the archive is fetched over HTTPS and unpacked into a temporary folder first,
  so a failed download cannot leave a half-written install;
* only files that ship with the app are copied (``app/``, ``tools/``, the
  launchers, the spec, requirements, README);
* ``opencode.jsonc`` is never touched — it holds this project's agent
  permissions, which belong to the user, not to the project;
* ``%APPDATA%\\Citizen Assistant`` is never touched — settings, notes and logs
  live there, not in the install folder.

A PyInstaller build cannot update itself: Windows will not let a running .exe
replace its own files.  ``is_frozen()`` reports that case so the caller can say
so instead of failing halfway.
"""
from __future__ import annotations

import io
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from .version import VERSION

REPO = "fishtaco7/citizen-assistant"
BRANCH = "main"
RAW_VERSION = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/app/version.py"
ARCHIVE = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
USER_AGENT = "Citizen-Assistant/1.0 (personal overlay; +https://uexcorp.space)"

COPY_DIRS = ("app", "tools")
COPY_FILES = (
    "launcher.py",
    "citizen-assistant.spec",
    "requirements.txt",
    "requirements-lock.txt",
    "install.bat",
    "uninstall.bat",
    "setup.bat",
    "run.bat",
    "run-debug.bat",
    "run.command",
    "run-hidden.vbs",
    "README.md",
    "app.ico",
    ".gitignore",
)

# Never copied, even inside an otherwise-wanted folder.
SKIP_NAMES = {"__pycache__", ".git", ".venv", "opencode.jsonc"}


def is_frozen() -> bool:
    """True for the PyInstaller EXE, which cannot replace itself."""
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    """The folder a shortcut should point at."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _version_tuple(text: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", str(text or ""))
    return tuple(int(part) for part in numbers[:3]) or (0,)


def _newer(published: str, installed: str) -> bool:
    return _version_tuple(published) > _version_tuple(installed)


def _text_at(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def published_version(timeout: float = 15.0) -> str:
    """VERSION from GitHub's copy of app/version.py.

    raw.githubusercontent.com is fronted by a CDN that keeps serving the copy
    it already has for a few minutes after a push, which made CHECK report
    "up to date" right after a release. A unique query string gives the CDN a
    cache key it has never seen, so the request reaches the repository.
    """
    body = _text_at(f"{RAW_VERSION}?t={int(time.time())}", timeout)
    match = re.search(r'VERSION\s*=\s*["\']([^"\']+)', body)
    if not match:
        raise RuntimeError("GitHub returned an unexpected version file")
    return match.group(1).strip()


def newest(timeout: float = 15.0) -> tuple[str, str, bool]:
    """(installed, published, update_available)."""
    published = published_version(timeout)
    return VERSION, published, _newer(published, VERSION)


def is_newer(published: str) -> bool:
    """Whether ``published`` (a version string) is ahead of this install."""
    return _newer(published, VERSION)


def _version_in(folder: Path) -> str:
    path = folder / "app" / "version.py"
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "?"
    match = re.search(r'VERSION\s*=\s*["\']([^"\']+)', body)
    return match.group(1).strip() if match else "?"


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_tree(source: Path, root: Path) -> None:
    for name in COPY_DIRS:
        folder = source / name
        if not folder.is_dir():
            raise RuntimeError(f"the update is missing {name}/")
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_NAMES for part in path.relative_to(source).parts):
                continue
            _copy(path, root / path.relative_to(source))

    for name in COPY_FILES:
        path = source / name
        if path.is_file():
            _copy(path, root / name)


def apply(timeout: float = 90.0) -> str:
    """Download the branch archive and copy it over this install.

    Returns the version that was installed.  The process keeps running the old
    code until it restarts; the caller is expected to relaunch.
    """
    if is_frozen():
        raise RuntimeError("the EXE build has to be replaced by hand")

    request = urllib.request.Request(ARCHIVE, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        blob = response.read()
    if not blob:
        raise RuntimeError("GitHub sent an empty archive")

    root = install_root()
    with tempfile.TemporaryDirectory(prefix="ca-update-") as tmp:
        with zipfile.ZipFile(io.BytesIO(blob)) as bundle:
            bundle.extractall(tmp)
        # GitHub wraps the tree in a single "citizen-assistant-main/" folder.
        source = next((path for path in Path(tmp).iterdir() if path.is_dir()), None)
        if source is None:
            raise RuntimeError("the update archive was empty")
        version = _version_in(source)
        _copy_tree(source, root)
    return version
