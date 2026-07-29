#!/usr/bin/env python3
"""Build deterministic wheel and source-distribution release artifacts."""

from __future__ import annotations

import argparse
import copy
import gzip
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Iterable

_GENERATED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "build",
    "dist",
    "htmlcov",
    "venv",
}
_GENERATED_SUFFIXES = (".egg-info",)
_MIN_ZIP_EPOCH = 315532800  # 1980-01-01, the earliest ZIP timestamp.
_REQUIRED_BUILD_TOOLCHAIN = {
    "pip": "26.1.2",
    "build": "1.5.0",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}


def _validate_build_toolchain() -> None:
    failures: list[str] = []
    for distribution, expected in _REQUIRED_BUILD_TOOLCHAIN.items():
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            failures.append(f"{distribution} is not installed (required {expected})")
            continue
        if actual != expected:
            failures.append(f"{distribution} {actual} is installed (required {expected})")
    if failures:
        raise RuntimeError("unsafe or uncontrolled release toolchain: " + "; ".join(failures))


def _iter_release_sources(
    root: Path,
    *,
    excluded_roots: Iterable[Path] = (),
) -> Iterable[Path]:
    excluded = tuple(path.expanduser().absolute() for path in excluded_roots)
    for path in root.rglob("*"):
        absolute_path = path.absolute()
        if any(
            absolute_path == excluded_root or absolute_path.is_relative_to(excluded_root)
            for excluded_root in excluded
        ):
            continue
        relative = path.relative_to(root)
        if any(
            part in _GENERATED_DIRS or part.endswith(_GENERATED_SUFFIXES) for part in relative.parts
        ):
            continue
        if path.name == ".coverage" or "__pycache__" in relative.parts:
            continue
        if path.is_file() or path.is_symlink():
            yield path


def _source_date_epoch(
    root: Path,
    *,
    excluded_roots: Iterable[Path] = (),
) -> int:
    configured = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if configured:
        try:
            epoch = int(configured)
        except ValueError as exc:
            raise ValueError("SOURCE_DATE_EPOCH must be an integer") from exc
        if epoch < _MIN_ZIP_EPOCH:
            raise ValueError("SOURCE_DATE_EPOCH must be on or after 1980-01-01")
        return epoch

    git_executable = shutil.which("git")
    if git_executable:
        try:
            result = subprocess.run(
                [git_executable, "show", "-s", "--format=%ct", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            epoch = int(result.stdout.strip())
            if epoch >= _MIN_ZIP_EPOCH:
                return epoch
        except (OSError, subprocess.CalledProcessError, ValueError):
            pass

    mtimes = [
        int(path.lstat().st_mtime)
        for path in _iter_release_sources(root, excluded_roots=excluded_roots)
    ]
    if not mtimes:
        raise ValueError("cannot determine SOURCE_DATE_EPOCH from an empty source tree")
    return max(max(mtimes), _MIN_ZIP_EPOCH)


def _normalized_member(member: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    normalized = copy.copy(member)
    normalized.mtime = epoch
    normalized.uid = 0
    normalized.gid = 0
    normalized.uname = ""
    normalized.gname = ""
    normalized.pax_headers = {}
    return normalized


def normalize_sdist(archive_path: Path, epoch: int) -> None:
    """Rewrite an sdist with deterministic ordering and metadata."""
    archive_path = archive_path.resolve()
    with tempfile.TemporaryDirectory(
        prefix=".commands-wrapper-sdist-",
        dir=archive_path.parent,
    ) as temp_dir:
        temp_root = Path(temp_dir)
        tar_path = temp_root / "normalized.tar"
        output_path = temp_root / archive_path.name

        with (
            tarfile.open(archive_path, mode="r:gz") as source,
            tarfile.open(
                tar_path,
                mode="w",
                format=tarfile.GNU_FORMAT,
            ) as destination,
        ):
            for member in sorted(source.getmembers(), key=lambda item: item.name):
                normalized = _normalized_member(member, epoch)
                fileobj = source.extractfile(member) if member.isfile() else None
                try:
                    destination.addfile(normalized, fileobj)
                finally:
                    if fileobj is not None:
                        fileobj.close()

        with tar_path.open("rb") as source_tar, output_path.open("wb") as output_file:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=output_file,
                mtime=epoch,
            ) as compressed:
                shutil.copyfileobj(source_tar, compressed, length=1024 * 1024)

        os.replace(output_path, archive_path)


def _validate_output_dir(root: Path, output_dir: Path) -> None:
    root = root.resolve()
    unresolved_output = output_dir.expanduser().absolute()
    output_dir = unresolved_output.resolve()
    if output_dir == Path(output_dir.anchor):
        raise ValueError("release output directory must not be a filesystem root")
    if output_dir == root or root.is_relative_to(output_dir):
        raise ValueError("release output directory must not contain the project source tree")

    if output_dir.is_relative_to(root):
        relative = output_dir.relative_to(root)
        top_level = relative.parts[0] if relative.parts else ""
        if top_level != "dist" and not top_level.startswith("dist-"):
            raise ValueError(
                "release output inside the project must use a dedicated dist directory"
            )

    # Inspect only the caller-controlled tail of the output path. Stopping at
    # the first existing non-symlink parent permits normal platform layouts
    # such as macOS' /var -> /private/var while still rejecting an output path
    # (or immediate parent) explicitly redirected through a symlink.
    for component in (unresolved_output, *unresolved_output.parents):
        if component.is_symlink():
            raise ValueError("release output directory and its parents must not be symbolic links")
        if component.exists():
            break


def _copy_release_tree(
    root: Path,
    destination: Path,
    *,
    excluded_roots: Iterable[Path] = (),
) -> None:
    for source in _iter_release_sources(root, excluded_roots=excluded_roots):
        relative = source.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            raise ValueError(f"release source tree must not contain symbolic links: {relative}")
        shutil.copy2(source, target, follow_symlinks=False)


def _clean_known_artifacts(output_dir: Path) -> None:
    for pattern in ("commands_wrapper-*.whl", "commands_wrapper-*.tar.gz"):
        for artifact in output_dir.glob(pattern):
            if artifact.is_symlink() or artifact.is_file():
                artifact.unlink()


def _publish_artifact(source: Path, output_dir: Path) -> Path:
    destination = output_dir / source.name
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{source.name}.",
        suffix=".tmp",
        dir=output_dir,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def build_release(root: Path, output_dir: Path) -> list[Path]:
    root = root.resolve()
    output_dir = output_dir.expanduser().absolute()
    _validate_output_dir(root, output_dir)
    _validate_build_toolchain()
    epoch = _source_date_epoch(root, excluded_roots=(output_dir,))

    output_dir.mkdir(parents=True, exist_ok=True)
    _clean_known_artifacts(output_dir)

    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)

    with tempfile.TemporaryDirectory(prefix="commands-wrapper-release-") as temp_dir:
        temp_root = Path(temp_dir)
        staged_source = temp_root / "source"
        staged_output = temp_root / "dist"
        staged_source.mkdir()
        staged_output.mkdir()
        _copy_release_tree(root, staged_source, excluded_roots=(output_dir,))

        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(staged_output),
            ],
            cwd=staged_source,
            env=environment,
            check=True,
        )

        sdists = sorted(staged_output.glob("*.tar.gz"))
        wheels = sorted(staged_output.glob("*.whl"))
        if len(sdists) != 1 or len(wheels) != 1:
            raise RuntimeError(
                f"expected exactly one wheel and one sdist, found {len(wheels)} wheel(s) "
                f"and {len(sdists)} sdist(s)"
            )

        normalize_sdist(sdists[0], epoch)
        return [
            _publish_artifact(wheels[0], output_dir),
            _publish_artifact(sdists[0], output_dir),
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dist"),
        help="release artifact directory (default: dist)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    for artifact in build_release(root, args.output_dir):
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
