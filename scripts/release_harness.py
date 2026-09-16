from __future__ import annotations

import os
import stat
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    errors: list[str] = []

    pyproject = ROOT / "pyproject.toml"
    metadata = tomllib.loads(read(pyproject))
    version = metadata["project"]["version"]
    tag = f"v{version}"

    release_notes = ROOT / "docs" / "release" / f"{tag}.md"
    candidate = ROOT / "docs" / "release" / "offline-release-candidate.md"
    readme = ROOT / "README.md"
    smoke_sh = ROOT / "scripts" / "smoke_offline.sh"
    smoke_ps1 = ROOT / "scripts" / "smoke_offline.ps1"

    for path in (release_notes, candidate, readme, smoke_sh, smoke_ps1):
        if not path.exists():
            errors.append(f"missing required release artifact: {path.relative_to(ROOT)}")

    if not errors:
        release_text = read(release_notes)
        candidate_text = read(candidate)
        readme_text = read(readme)
        sh_text = read(smoke_sh)
        ps1_text = read(smoke_ps1)

        if tag not in release_text:
            errors.append(f"release notes must identify tag {tag}")
        if f"`{version}`" not in candidate_text or f"`{tag}`" not in candidate_text:
            errors.append("offline release candidate must match pyproject version/tag")
        if "- [ ]" in candidate_text:
            errors.append("offline release Definition of Done still contains unchecked items")

        for command in ("./scripts/smoke_offline.sh", ".\\scripts\\smoke_offline.ps1"):
            if command not in readme_text:
                errors.append(f"README missing canonical smoke command: {command}")

        mode = smoke_sh.stat().st_mode
        if not mode & stat.S_IXUSR:
            errors.append("scripts/smoke_offline.sh must remain executable")
        if "docker info" not in sh_text or "trap cleanup EXIT" not in sh_text:
            errors.append("Unix smoke must fail early on Docker daemon errors and keep cleanup trap")
        if "docker info" not in ps1_text or "down -v --remove-orphans" not in ps1_text:
            errors.append("PowerShell smoke must check Docker daemon and retain best-effort cleanup")

    if errors:
        print("Release harness: FAILED")
        for error in errors:
            print(f" - {error}")
        return 1

    print(f"Release harness: OK (version={version}, tag={tag}, canonical smoke entrypoints verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
