"""Discovery of local LaTeX compilers, including Tectonic installed in WSL2."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import PureWindowsPath

from tracker.core.config import get_settings

_WSL_TECTONIC_COMMAND = (
    "if command -v tectonic >/dev/null 2>&1; then "
    'exec tectonic "$@"; '
    'elif [ -x "$HOME/.local/bin/tectonic" ]; then '
    'exec "$HOME/.local/bin/tectonic" "$@"; '
    "else exit 127; fi"
)


@dataclass(frozen=True)
class LatexCompiler:
    command: tuple[str, ...]
    tectonic: bool
    detail: str
    wsl: bool = False


@lru_cache(maxsize=4)
def _wsl_tectonic_version(wsl_exe: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603
            [wsl_exe, "--exec", "sh", "-c", _WSL_TECTONIC_COMMAND, "tectonic", "--version"],
            capture_output=True,
            check=False,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    version = next((line.strip() for line in result.stdout.splitlines() if re.match(r"Tectonic\b", line.strip())), None)
    return version or "Tectonic disponível"


def find_latex_compiler(command: str | None = None) -> LatexCompiler | None:
    configured = get_settings().latex_cmd if command is None else command
    parts = shlex.split(configured, posix=os.name != "nt") if configured else ["tectonic"]
    if not parts:
        parts = ["tectonic"]

    executable = parts[0]
    found = shutil.which(executable)
    if found:
        is_tectonic = PureWindowsPath(executable).name.lower() in {"tectonic", "tectonic.exe"}
        return LatexCompiler(tuple([found, *parts[1:]]), is_tectonic, found)

    if os.name == "nt" and PureWindowsPath(executable).name.lower() in {"tectonic", "tectonic.exe"}:
        wsl_exe = shutil.which("wsl.exe") or shutil.which("wsl")
        if wsl_exe:
            version = _wsl_tectonic_version(wsl_exe)
            if version:
                wrapper = (
                    wsl_exe,
                    "--exec",
                    "sh",
                    "-c",
                    _WSL_TECTONIC_COMMAND,
                    "tectonic",
                    *parts[1:],
                )
                return LatexCompiler(wrapper, True, f"{version} (WSL2)", wsl=True)
    return None
