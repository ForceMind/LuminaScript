import os
from pathlib import Path
import shutil
import subprocess

import pytest


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


@pytest.mark.parametrize("script_name", ["deploy.sh", "update.sh"])
@pytest.mark.parametrize("build_exit, artifact_exit", [(17, 0), (0, 19), (0, 0)])
def test_frontend_build_and_artifact_failures_stop_deployment(
    tmp_path, script_name, build_exit, artifact_exit
):
    """Execute the real build stage with fake commands, without touching services."""
    source = (ROOT_DIR / script_name).read_text(encoding="utf-8")
    if script_name == "deploy.sh":
        stage = source.split("build_frontend() {", 1)[1].split("\npick_backend_port()", 1)[0]
        stage = "build_frontend() {" + stage + "\nbuild_frontend\n"
    else:
        stage = source.split('echo -e "${YELLOW}[5/6]', 1)[1].split(
            'echo -e "${YELLOW}[6/6]', 1
        )[0]
        stage = 'echo -e "${YELLOW}[5/6]' + stage

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package-lock.json").write_text("{}", encoding="utf-8")
    trace = tmp_path / "commands.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("npm", "npx", "node"):
        executable = fake_bin / command
        executable.write_text(
            '#!/bin/sh\nprintf "%s %s\\n" "$(basename "$0")" "$*" >> "$TRACE_PATH"\n'
            'case "$(basename "$0"):$1" in\n'
            '  npm:run) exit "$BUILD_EXIT" ;;\n'
            '  node:scripts/generate-pwa-sw.mjs) exit "$ARTIFACT_EXIT" ;;\n'
            '  npx:*) exit 0 ;;\n'
            'esac\nexit 0\n',
            encoding="utf-8",
        )
        executable.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        PATH=f"{fake_bin}:{environment['PATH']}",
        FRONTEND_DIR=str(frontend),
        TRACE_PATH=str(trace),
        BUILD_EXIT=str(build_exit),
        ARTIFACT_EXIT=str(artifact_exit),
    )
    result = subprocess.run(
        [
            "bash", "-c",
            'set -euo pipefail\nRED=""; GREEN=""; YELLOW=""; NC=""\n'
            'ensure_frontend_node_version() { return 0; }\n'
            + stage + '\nprintf "READY_TO_RESTART\\n"\n',
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    commands = trace.read_text(encoding="utf-8")
    assert "npx " not in commands
    assert ("node scripts/generate-pwa-sw.mjs --check" in commands) == (build_exit == 0)
    if build_exit or artifact_exit:
        assert result.returncode != 0
        assert "READY_TO_RESTART" not in result.stdout
    else:
        assert result.returncode == 0
        assert "READY_TO_RESTART" in result.stdout
