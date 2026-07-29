# Changelog

All notable changes to commands-wrapper are documented here. The project follows Semantic Versioning.

## [1.0.0] - 2026-07-28

### Added

- Production-ready cross-platform packaging for Linux, macOS, and Windows.
- Automated test, lint, type, security, dependency, build, and release workflows.
- SHA-256 release checksums and trusted-publishing support for PyPI.
- Strict command-schema validation, duplicate-key detection, and bounded YAML parsing.
- Explicit environment controls for color, development update sources, binary output paths, externally managed Python environments, and opt-in local-command promotion.
- Exact CI and release toolchain constraints for reproducible verification.

### Changed

- Restored the canonical `.commands-wrapper` release layout.
- Standardized all project and update URLs on `vivid0o0/commands-wrapper`.
- Made file updates atomic, permission-preserving, symlink-safe, and concurrency-safe.
- Reconciled generated wrappers immediately after command rename and removal.
- Made wrapper generation safe on case-insensitive macOS and Windows filesystems.
- Added hosted Linux, macOS, and native Windows installer lifecycle verification.
- Forced installer wrapper synchronization into the resolved Python scripts directory through an explicit CLI argument.
- Provisioned isolated integration subprocesses with the exact installed runtime dependency roots.
- Verified native Windows wrapper discovery plus session and persistent user PATH installation in hosted CI.
- Preserved compatibility with the Bash 3.2 version shipped by macOS.
- Removed cosmetic installer dependencies and reduced installer side effects.
- Made local-to-global command promotion explicit opt-in instead of an automatic side effect.
- Enforced command timeouts as wall-clock deadlines and terminated the complete child process group.
- Reworked release builds to use a staged, non-destructive, symlink-rejecting pipeline.
- Made standalone installers consume the stable released package instead of mutable branch source.
- Declared the actual supported Python range as Python 3.10 or newer.

### Security

- Restricted update and remote installer URLs to HTTPS by default.
- Changed the default self-update channel from a mutable branch archive to the stable PyPI package.
- Added archive size, member-count, path traversal, link, device, FIFO, redirect, and extracted-size protections to both Unix and PowerShell installers.
- Added bounded command-file snapshots and fail-closed update rollback checks.
- Added dependency auditing and pinned GitHub Actions revisions.
- Added verified release installer assets and SHA-256 checksums.
