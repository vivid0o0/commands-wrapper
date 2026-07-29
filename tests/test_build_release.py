import gzip
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_release


class BuildReleaseTests(unittest.TestCase):
    def test_validate_build_toolchain_accepts_exact_versions(self):
        with mock.patch.object(
            build_release.importlib.metadata,
            "version",
            side_effect=lambda name: build_release._REQUIRED_BUILD_TOOLCHAIN[name],
        ):
            build_release._validate_build_toolchain()

    def test_validate_build_toolchain_rejects_missing_or_mismatched_versions(self):
        def version(name):
            if name == "pip":
                return "25.1.1"
            if name == "wheel":
                raise build_release.importlib.metadata.PackageNotFoundError(name)
            return build_release._REQUIRED_BUILD_TOOLCHAIN[name]

        with (
            mock.patch.object(build_release.importlib.metadata, "version", side_effect=version),
            self.assertRaisesRegex(RuntimeError, "pip 25.1.1.*wheel is not installed"),
        ):
            build_release._validate_build_toolchain()

    def _write_archive(self, path: Path, *, reverse: bool, mtime: int, uid: int) -> None:
        entries = [("project/file.txt", b"payload\n"), ("project/other.txt", b"other\n")]
        if reverse:
            entries.reverse()

        with path.open("wb") as raw:
            with gzip.GzipFile(
                filename="source-name.tar", mode="wb", fileobj=raw, mtime=mtime
            ) as gz:
                with tarfile.open(fileobj=gz, mode="w") as archive:
                    for name, content in entries:
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        member.mtime = mtime
                        member.uid = uid
                        member.gid = uid
                        member.uname = "builder"
                        member.gname = "builder"
                        archive.addfile(member, io.BytesIO(content))

    def test_normalize_sdist_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"
            self._write_archive(first, reverse=False, mtime=1_700_000_000, uid=1000)
            self._write_archive(second, reverse=True, mtime=1_710_000_000, uid=2000)

            build_release.normalize_sdist(first, 1_720_000_000)
            build_release.normalize_sdist(second, 1_720_000_000)

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_source_date_epoch_prefers_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1720000000"}):
                self.assertEqual(build_release._source_date_epoch(Path(tmp)), 1_720_000_000)

    def test_source_date_epoch_rejects_invalid_environment_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "invalid"}):
                with self.assertRaisesRegex(ValueError, "must be an integer"):
                    build_release._source_date_epoch(Path(tmp))

    def test_validate_output_dir_rejects_project_root_and_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()

            with self.assertRaisesRegex(ValueError, "contain the project source tree"):
                build_release._validate_output_dir(root, root)
            with self.assertRaisesRegex(ValueError, "contain the project source tree"):
                build_release._validate_output_dir(root, root.parent)

    def test_validate_output_dir_rejects_unrelated_project_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()

            with self.assertRaisesRegex(ValueError, "dedicated dist directory"):
                build_release._validate_output_dir(root, root / "scripts")

            build_release._validate_output_dir(root, root / "dist-recheck")

    def test_validate_output_dir_rejects_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            target = Path(tmp) / "target"
            link = Path(tmp) / "output-link"
            root.mkdir()
            target.mkdir()
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create symbolic link: {exc}")

            with self.assertRaisesRegex(ValueError, "symbolic link"):
                build_release._validate_output_dir(root, link)

    def test_validate_output_dir_rejects_symlink_parent(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            target = Path(tmp) / "target"
            link = Path(tmp) / "output-link"
            root.mkdir()
            target.mkdir()
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create symbolic link: {exc}")

            with self.assertRaisesRegex(ValueError, "parents must not be symbolic links"):
                build_release._validate_output_dir(root, link / "nested")

    def test_copy_release_tree_rejects_source_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            destination = Path(tmp) / "destination"
            root.mkdir()
            destination.mkdir()
            target = root / "target.txt"
            link = root / "link.txt"
            target.write_text("target", encoding="utf-8")
            try:
                link.symlink_to(target.name)
            except OSError as exc:
                self.skipTest(f"cannot create symbolic link: {exc}")

            with self.assertRaisesRegex(ValueError, "must not contain symbolic links"):
                build_release._copy_release_tree(root, destination)

    def test_clean_known_artifacts_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            foreign = output / "keep.txt"
            wheel = output / "commands_wrapper-0.9.0-py3-none-any.whl"
            sdist = output / "commands_wrapper-0.9.0.tar.gz"
            foreign.write_text("keep", encoding="utf-8")
            wheel.write_bytes(b"old wheel")
            sdist.write_bytes(b"old sdist")

            build_release._clean_known_artifacts(output)

            self.assertTrue(foreign.is_file())
            self.assertFalse(wheel.exists())
            self.assertFalse(sdist.exists())

    def test_source_date_epoch_falls_back_to_source_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.txt"
            source.write_text("content", encoding="utf-8")
            os.utime(source, (1_720_000_000, 1_720_000_000))

            with (
                mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": ""}, clear=False),
                mock.patch.object(build_release.subprocess, "run", side_effect=OSError),
            ):
                self.assertEqual(build_release._source_date_epoch(root), 1_720_000_000)

    def test_source_date_epoch_uses_resolved_git_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolved_git = "/opt/tools/git"
            completed = mock.Mock(stdout="1720000000\n")

            with (
                mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": ""}, clear=False),
                mock.patch.object(build_release.shutil, "which", return_value=resolved_git),
                mock.patch.object(
                    build_release.subprocess,
                    "run",
                    return_value=completed,
                ) as run_mock,
            ):
                epoch = build_release._source_date_epoch(root)

            self.assertEqual(epoch, 1_720_000_000)
            run_mock.assert_called_once_with(
                [resolved_git, "show", "-s", "--format=%ct", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )

    def test_copy_release_tree_excludes_active_output_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            output = root / "dist-recheck"
            destination = Path(tmp) / "staged"
            root.mkdir()
            output.mkdir()
            destination.mkdir()
            (root / "source.txt").write_text("source", encoding="utf-8")
            (output / "stale.txt").write_text("stale", encoding="utf-8")

            build_release._copy_release_tree(
                root,
                destination,
                excluded_roots=(output,),
            )

            self.assertEqual(
                (destination / "source.txt").read_text(encoding="utf-8"),
                "source",
            )
            self.assertFalse((destination / "dist-recheck").exists())

    def test_active_output_tree_is_excluded_from_release_sources_and_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            output = root / "dist-recheck"
            source = root / "source.txt"
            stale = output / "stale.txt"
            output.mkdir(parents=True)
            source.write_text("source", encoding="utf-8")
            stale.write_text("stale", encoding="utf-8")
            os.utime(source, (1_720_000_000, 1_720_000_000))
            os.utime(stale, (1_820_000_000, 1_820_000_000))

            sources = list(
                build_release._iter_release_sources(
                    root,
                    excluded_roots=(output,),
                )
            )

            self.assertIn(source, sources)
            self.assertNotIn(stale, sources)
            with (
                mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": ""}, clear=False),
                mock.patch.object(build_release.subprocess, "run", side_effect=OSError),
            ):
                self.assertEqual(
                    build_release._source_date_epoch(
                        root,
                        excluded_roots=(output,),
                    ),
                    1_720_000_000,
                )


if __name__ == "__main__":
    unittest.main()
