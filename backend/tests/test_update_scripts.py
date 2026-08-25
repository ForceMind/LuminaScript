import os
from pathlib import Path
import shutil
import subprocess


ROOT_DIR = Path(__file__).resolve().parents[2]


def test_update_script_runs_git_from_project_directory(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    (project_dir / "backend").mkdir()
    (project_dir / "frontend").mkdir()
    shutil.copy2(ROOT_DIR / "update.sh", project_dir / "update.sh")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    trace_path = tmp_path / "git-pwd.txt"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'pwd > "$TRACE_PATH"\n'
        'if [ "$1" = "status" ]; then\n'
        "  printf ' M application.py\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["TRACE_PATH"] = str(trace_path)
    result = subprocess.run(
        ["bash", str(project_dir / "update.sh")],
        cwd=outside_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "检测到代码或配置存在未提交修改" in result.stdout
    assert trace_path.read_text(encoding="utf-8").strip() == str(project_dir)


def test_miaobi_runs_project_scripts_from_project_directory(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "backend").mkdir()
    (project_dir / "frontend").mkdir()
    shutil.copy2(ROOT_DIR / "miaobi", project_dir / "miaobi")

    trace_path = tmp_path / "miaobi-script-pwd.txt"
    (project_dir / "update.sh").write_text(
        '#!/bin/sh\npwd > "$TRACE_PATH"\n',
        encoding="utf-8",
    )
    (project_dir / "uninstall.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    environment = os.environ.copy()
    environment["MIAOBI_PROJECT_DIR"] = str(project_dir)
    environment["TRACE_PATH"] = str(trace_path)
    result = subprocess.run(
        ["bash", str(project_dir / "miaobi"), "update"],
        cwd=outside_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert trace_path.read_text(encoding="utf-8").strip() == str(project_dir)
