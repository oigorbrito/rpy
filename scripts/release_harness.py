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
    project = metadata["project"]
    version = project["version"]
    tag = f"v{version}"

    release_notes = ROOT / "docs" / "release" / f"{tag}.md"
    candidate = ROOT / "docs" / "release" / "offline-release-candidate.md"
    readme = ROOT / "README.md"
    license_file = ROOT / "LICENSE"
    notice_file = ROOT / "NOTICE"
    smoke_sh = ROOT / "scripts" / "smoke_offline.sh"
    smoke_ps1 = ROOT / "scripts" / "smoke_offline.ps1"

    for path in (
        release_notes,
        candidate,
        readme,
        license_file,
        notice_file,
        smoke_sh,
        smoke_ps1,
    ):
        if not path.exists():
            errors.append(f"missing required release artifact: {path.relative_to(ROOT)}")

    if project.get("license") != "MIT":
        errors.append("pyproject project.license must be MIT for the v0 release")

    if not errors:
        release_text = read(release_notes)
        candidate_text = read(candidate)
        readme_text = read(readme)
        license_text = read(license_file)
        notice_text = read(notice_file)
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

        if "licença MIT" not in readme_text:
            errors.append("README must declare the MIT project license")
        if not license_text.startswith("MIT License"):
            errors.append("LICENSE must contain the MIT license text")
        if "HydraTask" not in notice_text or "Iank314/task-queue" not in notice_text:
            errors.append("NOTICE must retain third-party provenance entries")

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

    print(
        f"Release harness: OK (version={version}, tag={tag}, license=MIT, canonical smoke entrypoints verified)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
