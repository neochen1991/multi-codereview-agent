from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


WINDOWS_EXECUTABLE_SUFFIXES = ("", ".exe", ".cmd", ".bat", ".ps1")


def resolve_executable(executable: str) -> str:
    """Resolve a command name to an executable path with Windows-friendly fallbacks."""

    raw = str(executable or "").strip().strip('"')
    if not raw:
        return ""
    candidate_path = Path(raw).expanduser()
    if candidate_path.exists():
        return str(candidate_path)

    resolved = shutil.which(raw)
    if resolved:
        return resolved

    if Path(raw).name.lower().startswith("gitnexus"):
        gitnexus = _resolve_gitnexus_from_common_windows_locations(raw)
        if gitnexus:
            return gitnexus

    return _resolve_with_system_where(raw)


def _resolve_gitnexus_from_common_windows_locations(executable: str) -> str:
    executable_name = Path(executable).name
    base_name = executable_name if Path(executable_name).suffix else "gitnexus"
    candidate_dirs = [
        Path(value).expanduser()
        for value in [
            os.getenv("APPDATA", ""),
            os.getenv("LOCALAPPDATA", ""),
            os.getenv("ProgramFiles", ""),
            os.getenv("ProgramFiles(x86)", ""),
            os.getenv("ProgramData", ""),
            os.getenv("ChocolateyInstall", ""),
            os.getenv("SCOOP", ""),
        ]
        if str(value or "").strip()
    ]
    user_profile = str(os.getenv("USERPROFILE") or "").strip()
    if user_profile:
        root = Path(user_profile).expanduser()
        candidate_dirs.extend(
            [
                root / "AppData" / "Roaming" / "npm",
                root / "AppData" / "Local" / "Microsoft" / "WindowsApps",
                root / "AppData" / "Local" / "Programs" / "GitNexus",
                root / "AppData" / "Local" / "Programs" / "GitNexus" / "bin",
                root / "scoop" / "shims",
            ]
        )
    expanded_dirs: list[Path] = []
    for base_dir in candidate_dirs:
        expanded_dirs.append(base_dir)
        expanded_dirs.append(base_dir / "npm")
        expanded_dirs.append(base_dir / "GitNexus")
        expanded_dirs.append(base_dir / "GitNexus" / "bin")
        expanded_dirs.append(base_dir / "Microsoft" / "WindowsApps")
        expanded_dirs.append(base_dir / "chocolatey" / "bin")
        expanded_dirs.append(base_dir / "shims")

    for directory in expanded_dirs:
        for suffix in WINDOWS_EXECUTABLE_SUFFIXES:
            candidate = directory / (base_name if Path(base_name).suffix else f"{base_name}{suffix}")
            if candidate.exists():
                return str(candidate)
    return ""


def _resolve_with_system_where(executable: str) -> str:
    where_command = "where" if os.name == "nt" else "which"
    try:
        completed = subprocess.run(
            [where_command, executable],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    for line in str(completed.stdout or "").splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return ""
