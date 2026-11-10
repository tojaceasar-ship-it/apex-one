"""Artifact validation and isolated input/output staging for SandboxJobs."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from apex_one.sandbox.contracts import (
    ArtifactRule,
    InputFileSpec,
    ValidatedArtifact,
    validate_safe_relative_path,
)


class ArtifactValidationError(ValueError):
    """Raised when sandbox output violates security or size constraints."""


class ArtifactValidator:
    """Validates sandbox output files against traversal, symlinks, extensions, and quotas."""

    def __init__(self, rule: ArtifactRule) -> None:
        self._rule = rule
        self._allowed_exts = frozenset(rule.allowed_extensions)

    def validate_directory(self, output_dir: Path) -> list[ValidatedArtifact]:
        resolved_root = output_dir.resolve()
        if not resolved_root.exists() or not resolved_root.is_dir():
            raise ArtifactValidationError("Output directory does not exist.")

        artifacts: list[ValidatedArtifact] = []
        total_bytes = 0

        for root, dirs, files in os.walk(resolved_root, followlinks=False):
            root_path = Path(root)
            for dname in dirs:
                candidate_dir = root_path / dname
                if candidate_dir.is_symlink():
                    raise ArtifactValidationError(
                        f"Symlink directory '{dname}' is forbidden in sandbox output."
                    )

            for fname in sorted(files):
                candidate = root_path / fname
                if candidate.is_symlink():
                    raise ArtifactValidationError(
                        f"Symlink artifact '{fname}' is forbidden in sandbox output."
                    )
                resolved_file = candidate.resolve()
                try:
                    rel_posix = resolved_file.relative_to(resolved_root).as_posix()
                except ValueError as exc:
                    raise ArtifactValidationError(
                        "Artifact escaped sandbox output root."
                    ) from exc

                rel_posix = validate_safe_relative_path(rel_posix)
                if len(artifacts) >= self._rule.max_files:
                    raise ArtifactValidationError(
                        f"Artifact count exceeds max_files ({self._rule.max_files})."
                    )

                ext = resolved_file.suffix.lower()
                if ext not in self._allowed_exts:
                    raise ArtifactValidationError(
                        f"Extension '{ext or '<none>'}' for '{rel_posix}' is not in allowed_extensions."
                    )

                stat = resolved_file.stat()
                if not os.path.isfile(resolved_file):
                    raise ArtifactValidationError(
                        f"Special file '{rel_posix}' is forbidden in sandbox output."
                    )

                size_bytes = int(stat.st_size)
                if size_bytes > self._rule.max_single_file_bytes:
                    raise ArtifactValidationError(
                        f"Artifact '{rel_posix}' ({size_bytes} B) exceeds single-file cap "
                        f"({self._rule.max_single_file_bytes} B)."
                    )

                total_bytes += size_bytes
                if total_bytes > self._rule.max_total_bytes:
                    raise ArtifactValidationError(
                        f"Total artifacts ({total_bytes} B) exceed max_total_bytes "
                        f"({self._rule.max_total_bytes} B)."
                    )

                digest = hashlib.sha256(resolved_file.read_bytes()).hexdigest()
                artifacts.append(
                    ValidatedArtifact(
                        relative_path=rel_posix,
                        size_bytes=size_bytes,
                        sha256=digest,
                        extension=ext,
                    )
                )

        return artifacts


class IsolatedIOWorkspace:
    """Context manager creating isolated read-only `input/` and bounded `output/` directories."""

    def __init__(
        self,
        inputs: list[InputFileSpec],
        artifact_rule: ArtifactRule,
    ) -> None:
        self._inputs = inputs
        self._validator = ArtifactValidator(artifact_rule)
        self._root: Path | None = None

    @property
    def input_dir(self) -> Path:
        if self._root is None:
            raise RuntimeError("IsolatedIOWorkspace is not entered.")
        return self._root / "input"

    @property
    def output_dir(self) -> Path:
        if self._root is None:
            raise RuntimeError("IsolatedIOWorkspace is not entered.")
        return self._root / "output"

    def __enter__(self) -> IsolatedIOWorkspace:
        raw_dir = tempfile.mkdtemp(prefix="apex_sandbox_io_")
        os.chmod(raw_dir, 0o700)
        self._root = Path(raw_dir)
        in_dir = self.input_dir
        out_dir = self.output_dir
        in_dir.mkdir(mode=0o700)
        out_dir.mkdir(mode=0o700)

        for item in self._inputs:
            rel = validate_safe_relative_path(item.relative_path)
            target = (in_dir / rel).resolve()
            if not str(target).startswith(str(in_dir.resolve()) + os.sep):
                raise ArtifactValidationError("Input path escaped staging root.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content_utf8, encoding="utf-8")
            os.chmod(target, 0o400)

        return self

    def collect_validated_artifacts(self) -> list[ValidatedArtifact]:
        return self._validator.validate_directory(self.output_dir)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._root is not None and self._root.exists():
            # Restore write bits on read-only input files before cleanup
            for root, dirs, files in os.walk(self._root):
                for d in dirs:
                    try:
                        os.chmod(os.path.join(root, d), 0o700)
                    except OSError:
                        pass
                for f in files:
                    try:
                        os.chmod(os.path.join(root, f), 0o600)
                    except OSError:
                        pass
            shutil.rmtree(self._root, ignore_errors=True)
        self._root = None
