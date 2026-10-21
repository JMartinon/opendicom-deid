"""Build and archive the application on the host OS; no cross-compilation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from build_environment import packaging_environment, require_build_environment
from collect_licenses import collect

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "OpenDICOM-De-ID"


def main() -> None:
    require_build_environment()
    system = platform.system()
    if system not in {"Windows", "Darwin", "Linux"}:
        raise SystemExit("Supported build hosts: Windows, macOS, Linux.")
    architecture = {"AMD64": "x86_64", "aarch64": "arm64"}.get(
        platform.machine(), platform.machine()
    )
    if architecture not in {"x86_64", "arm64"}:
        raise SystemExit("Build requires x86-64 or ARM64 Python.")
    if system in {"Windows", "Linux"} and architecture != "x86_64":
        raise SystemExit("Windows and Ubuntu distribution targets require x86-64 Python.")

    licenses = ROOT / "build" / "licenses"
    if licenses.exists():
        shutil.rmtree(licenses)
    collect(licenses)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--paths",
        str(ROOT / "src"),
        "--specpath",
        str(ROOT / "build"),
        "--workpath",
        str(ROOT / "build" / "pyinstaller"),
        "--distpath",
        str(ROOT / "dist"),
        "--runtime-hook",
        str(ROOT / "scripts" / "frozen_runtime.py"),
        "--additional-hooks-dir",
        str(ROOT / "scripts" / "hooks"),
        "--add-data",
        f"{licenses}:licenses",
        "--copy-metadata",
        "opendicom-deid",
        "--copy-metadata",
        "dicom-anonymizer",
        "--copy-metadata",
        "pydicom",
        # Never package the upstream example DICOMs or optional pixel codecs.
        # We preserve encoded pixel bytes, and do not download sample data.
        "--exclude-module",
        "pydicom.examples",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "IPython",
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "PIL",
    ]
    if system == "Windows":
        command.extend(["--onefile", "--windowed"])
    else:
        command.append("--onedir")
        if system == "Darwin":
            command.extend(
                [
                    "--windowed",
                    "--osx-bundle-identifier",
                    "org.opendicom.deid",
                    "--target-architecture",
                    architecture,
                ]
            )
    command.append(str(ROOT / "scripts" / "desktop_entry.py"))
    build_env = packaging_environment()
    build_env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-cache")
    subprocess.run(command, cwd=ROOT, env=build_env, check=True)
    version = importlib.metadata.version("opendicom-deid")
    os_name = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Ubuntu"}[system]
    stem = f"{APP_NAME}-{version}-{os_name}-{architecture}"
    release = ROOT / "dist" / "release"
    release.mkdir(parents=True, exist_ok=True)
    if system == "Darwin":
        archive = release / f"{stem}.zip"
        subprocess.run(
            [
                "ditto",
                "-c",
                "-k",
                "--sequesterRsrc",
                "--keepParent",
                str(ROOT / "dist" / f"{APP_NAME}.app"),
                str(archive),
            ],
            check=True,
        )
    elif system == "Windows":
        archive = release / f"{stem}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(ROOT / "dist" / f"{APP_NAME}.exe", f"{APP_NAME}.exe")
            for path in sorted(licenses.rglob("*")):
                if path.is_file():
                    bundle.write(path, Path("licenses") / path.relative_to(licenses))
            bundle.write(ROOT / "docs" / "distribution.md", "distribution.md")
    else:
        folder = ROOT / "dist" / APP_NAME
        shutil.copy2(ROOT / "docs" / "distribution.md", folder / "README.md")
        launcher = folder / "OpenDICOM-De-ID.desktop"
        launcher.write_text(
            "[Desktop Entry]\nType=Application\nName=OpenDICOM De-ID\n"
            "Comment=Offline DICOM de-identification\n"
            'Exec=sh -c \'"$(dirname "%k")/OpenDICOM-De-ID"\'\n'
            "Terminal=false\nCategories=Science;MedicalSoftware;\n",
            encoding="utf-8",
        )
        launcher.chmod(0o755)
        archive = release / f"{stem}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(folder, arcname=APP_NAME)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="ascii",
    )
    application = ROOT / "dist" / (
        f"{APP_NAME}.app" if system == "Darwin"
        else f"{APP_NAME}.exe" if system == "Windows"
        else f"{APP_NAME}/{APP_NAME}"
    )
    print(f"\nBuild complete.\nApplication: {application}\nArchive: {archive}")
    if system == "Windows":
        print(f"Start the GUI by double-clicking {application.name} in dist/.")
    else:
        launch = shlex.quote(str(application))
        if system == "Darwin":
            launch = f"open {launch}"
        print(f"Start the GUI:\n  {launch}")
    if system == "Linux":
        print(
            f"Keep the entire dist/{APP_NAME}/ folder together, including _internal/.\n"
            "The build/ directory contains intermediate files; launch from dist/."
        )
    print("No publisher signing or notarization was performed.")


if __name__ == "__main__":
    main()
