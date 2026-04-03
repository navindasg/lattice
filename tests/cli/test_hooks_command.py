"""Tests for the hook:install and hook:uninstall CLI commands."""
import stat
from pathlib import Path

import pytest

from lattice.cli.hooks import _hook_install_impl, _hook_uninstall_impl


class TestHookInstall:
    """Tests for _hook_install_impl core logic."""

    def test_hook_install_creates_script(self, tmp_path: Path) -> None:
        """hook:install on empty .git/hooks/ creates post-commit with shebang and sentinels."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        result = _hook_install_impl(tmp_path)

        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert hook_path.exists(), "post-commit hook should be created"
        content = hook_path.read_text(encoding="utf-8")
        assert "#!/bin/sh" in content
        assert "# LATTICE-HOOK-BEGIN" in content
        assert "# LATTICE-HOOK-END" in content
        assert "lattice map:queue" in content
        assert result["installed"] is True

    def test_hook_install_appends_to_existing(self, tmp_path: Path) -> None:
        """hook:install preserves existing hook content and appends Lattice section after it."""
        git_dir = tmp_path / ".git"
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True)

        existing_content = "#!/bin/sh\necho 'pre-existing hook'"
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text(existing_content, encoding="utf-8")

        result = _hook_install_impl(tmp_path)

        content = hook_path.read_text(encoding="utf-8")
        assert "echo 'pre-existing hook'" in content
        assert "# LATTICE-HOOK-BEGIN" in content
        assert "# LATTICE-HOOK-END" in content
        assert result["installed"] is True

    def test_hook_install_idempotent(self, tmp_path: Path) -> None:
        """Running hook:install twice does not duplicate the Lattice section."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        _hook_install_impl(tmp_path)
        result = _hook_install_impl(tmp_path)

        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        content = hook_path.read_text(encoding="utf-8")
        begin_count = content.count("# LATTICE-HOOK-BEGIN")
        assert begin_count == 1, "Lattice section should only appear once"
        assert result.get("already_present") is True

    def test_hook_install_sets_executable(self, tmp_path: Path) -> None:
        """post-commit file has executable bit set after hook:install."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        _hook_install_impl(tmp_path)

        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        file_stat = hook_path.stat()
        assert file_stat.st_mode & stat.S_IXUSR, "Owner execute bit should be set"

    def test_hook_install_no_git_dir(self, tmp_path: Path) -> None:
        """hook:install with no .git/ returns error dict with reason 'no_git_directory'."""
        result = _hook_install_impl(tmp_path)

        assert result["installed"] is False
        assert result["reason"] == "no_git_directory"

    def test_hook_install_creates_hooks_dir(self, tmp_path: Path) -> None:
        """hook:install creates hooks/ directory if it does not exist."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        # Don't create hooks/ dir - install should create it

        result = _hook_install_impl(tmp_path)

        hooks_dir = tmp_path / ".git" / "hooks"
        assert hooks_dir.exists(), "hooks/ directory should be created"
        assert result["installed"] is True

    def test_hook_install_section_content(self, tmp_path: Path) -> None:
        """The installed hook section contains LATTICE_COMMIT and LATTICE_FILES variables."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        _hook_install_impl(tmp_path)

        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        content = hook_path.read_text(encoding="utf-8")
        assert "LATTICE_COMMIT" in content
        assert "LATTICE_FILES" in content
        assert "map:queue" in content


class TestHookUninstall:
    """Tests for _hook_uninstall_impl core logic."""

    def test_hook_uninstall_removes_section(self, tmp_path: Path) -> None:
        """hook:uninstall removes lines between sentinels, preserving other content."""
        git_dir = tmp_path / ".git"
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True)

        hook_content = (
            "#!/bin/sh\n"
            "echo 'pre-existing'\n"
            "# LATTICE-HOOK-BEGIN\n"
            "LATTICE_COMMIT=$(git rev-parse HEAD)\n"
            "lattice map:queue . --commit $LATTICE_COMMIT &>/dev/null &\n"
            "# LATTICE-HOOK-END\n"
        )
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text(hook_content, encoding="utf-8")

        result = _hook_uninstall_impl(tmp_path)

        content = hook_path.read_text(encoding="utf-8")
        assert "# LATTICE-HOOK-BEGIN" not in content
        assert "# LATTICE-HOOK-END" not in content
        assert "echo 'pre-existing'" in content
        assert result["removed"] is True

    def test_hook_uninstall_no_section(self, tmp_path: Path) -> None:
        """hook:uninstall when no sentinels found returns removed=False with reason."""
        git_dir = tmp_path / ".git"
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True)

        hook_path = hooks_dir / "post-commit"
        hook_path.write_text("#!/bin/sh\necho 'other hook'", encoding="utf-8")

        result = _hook_uninstall_impl(tmp_path)

        assert result["removed"] is False
        assert result["reason"] == "no_lattice_section"

    def test_hook_uninstall_no_file(self, tmp_path: Path) -> None:
        """hook:uninstall when no post-commit file exists returns removed=False."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        # No hooks dir or post-commit file

        result = _hook_uninstall_impl(tmp_path)

        assert result["removed"] is False
        assert result["reason"] == "no_lattice_section"

    def test_hook_uninstall_deletes_empty_hook(self, tmp_path: Path) -> None:
        """hook:uninstall deletes the file if remaining content is only shebang or empty."""
        git_dir = tmp_path / ".git"
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True)

        hook_content = (
            "#!/bin/sh\n"
            "# LATTICE-HOOK-BEGIN\n"
            "lattice map:queue . &\n"
            "# LATTICE-HOOK-END\n"
        )
        hook_path = hooks_dir / "post-commit"
        hook_path.write_text(hook_content, encoding="utf-8")

        result = _hook_uninstall_impl(tmp_path)

        assert not hook_path.exists(), "Empty hook file should be deleted"
        assert result["removed"] is True

    def test_hook_install_then_uninstall_round_trip(self, tmp_path: Path) -> None:
        """Install then uninstall restores clean state."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()

        _hook_install_impl(tmp_path)
        result = _hook_uninstall_impl(tmp_path)

        hook_path = tmp_path / ".git" / "hooks" / "post-commit"
        assert result["removed"] is True
        # File should be deleted since there was only the lattice section
        assert not hook_path.exists()
