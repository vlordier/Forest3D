"""Gazebo executable capability checks."""

from unittest.mock import patch

from click.testing import CliRunner

from forest3d.cli.main import main


def _project(tmp_path):
    (tmp_path / "models").mkdir()
    (tmp_path / "worlds").mkdir()
    (tmp_path / "worlds" / "forest_world.world").write_text("<sdf/>")


def test_launch_reports_missing_gz():
    with patch("forest3d.cli.launch.shutil.which", return_value=None):
        result = CliRunner().invoke(main, ["launch"])

    assert result.exit_code != 0
    assert "Gazebo not found" in result.output


def test_launch_rejects_unrelated_gz(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    completed = __import__("subprocess").CompletedProcess(
        args=["gz", "sim", "--version"], returncode=0, stdout="git version 2.0\n", stderr=""
    )
    with patch("forest3d.cli.launch.find_gazebo", return_value=("/usr/bin/gz", "harmonic")), patch(
        "forest3d.cli.launch.subprocess.run", return_value=completed
    ):
        result = CliRunner().invoke(main, ["launch"])

    assert result.exit_code != 0
    assert "does not report Gazebo Sim capability" in result.output
    assert "/usr/bin/gz" in result.output


def test_launch_explains_broken_gz_sim(tmp_path, monkeypatch):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    completed = __import__("subprocess").CompletedProcess(
        args=["gz", "sim", "--version"], returncode=1, stdout="", stderr="broken sim plugin"
    )
    with patch("forest3d.cli.launch.find_gazebo", return_value=("/opt/gz/bin/gz", "harmonic")), patch(
        "forest3d.cli.launch.subprocess.run", return_value=completed
    ):
        result = CliRunner().invoke(main, ["launch"])

    assert result.exit_code != 0
    assert "does not report Gazebo Sim capability" in result.output
    assert "broken sim plugin" in result.output
