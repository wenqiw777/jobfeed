"""Canonical wrapper and Compose contracts for baseline migration control-plane."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = _ROOT / "bin" / "jobfeed"
_COMPOSE = _ROOT / "docker-compose.yml"
_RUN_AND_DOWN_CALLS = 2
_GIT_COMMIT_LENGTH = 40
_FORMAL_FINGERPRINT_READS = 3


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    binary = tmp_path / "docker"
    log = tmp_path / "docker-argv.jsonl"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "with open(os.environ['FAKE_DOCKER_LOG'], 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': sys.argv[1:], 'env': {\n"
        "        'dump': os.environ.get('JOBFEED_MIGRATION_DUMP_HOST'),\n"
        "        'artifacts': os.environ.get(\n"
        "            'JOBFEED_MIGRATION_ARTIFACT_PARENT_HOST'),\n"
        "        'run_root': os.environ.get('JOBFEED_MIGRATION_RUN_HOST'),\n"
        "        'git_commit': os.environ.get('JOBFEED_MIGRATION_GIT_COMMIT'),\n"
        "    }}, ensure_ascii=False) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "formal_phase = os.environ.get('FAKE_FORMAL_PHASE', 'same')\n"
        "formal_changed = (formal_phase == 'changed' and os.path.exists(\n"
        " os.path.join(os.environ['JOBFEED_MIGRATION_OUTPUT_HOST'],\n"
        " 'capture-ready.json')))\n"
        "if args[:2] == ['inspect', 'jobfeed-postgres-1']:\n"
        "    if formal_phase == 'absent': raise SystemExit(1)\n"
        "    doc = {'Id': ('e' if formal_changed else 'd') * 64,\n"
        "      'State': {'Status': 'exited'},\n"
        "      'Mounts': [{'Type': 'volume', 'Name': 'jobfeed_pgdata',\n"
        "       'Destination': '/var/lib/postgresql/data', 'RW': True}]}\n"
        "    print(json.dumps([doc])); raise SystemExit(0)\n"
        "if args[:3] == ['volume', 'inspect', 'jobfeed_pgdata']:\n"
        "    if formal_phase == 'absent': raise SystemExit(1)\n"
        "    doc = {'Name': 'jobfeed_pgdata', 'Driver': 'local',\n"
        "      'CreatedAt': ('changed' if formal_changed else 'stable'),\n"
        "      'Options': None, 'Labels': {'compose': 'jobfeed'}}\n"
        "    print(json.dumps([doc])); raise SystemExit(0)\n"
        "ids = {'restore-source': 'a' * 64, 'restore-scratch': 'b' * 64,\n"
        "       'migration-runner': 'c' * 64}\n"
        "project = os.environ.get('JOBFEED_MIGRATION_PROJECT', 'missing')\n"
        "network = project + '_migration-internal'\n"
        "if 'run' in args and '-d' in args:\n"
        "    print('#1 simulated build output')\n"
        "    print(ids['migration-runner'])\n"
        "    root = os.environ['JOBFEED_MIGRATION_OUTPUT_HOST']\n"
        "    open(os.path.join(root, 'capture-ready.json'), 'w').write('{}')\n"
        "elif 'ps' in args and '-q' in args:\n"
        "    print(ids[args[-1]])\n"
        "elif args[:1] == ['inspect']:\n"
        "    cid = args[-1]\n"
        "    if cid.endswith('-migration-runner'): cid = ids['migration-runner']\n"
        "    service = next(name for name, value in ids.items() if value == cid)\n"
        "    mounts = [] if service != 'migration-runner' else [\n"
        "      {'Type': 'bind', 'Destination':\n"
        "       '/run/jobfeed-migration/source.dump', 'RW': False}]\n"
        "    doc = {'Id': cid, 'Image': 'sha256:' + cid,\n"
        "      'Config': {'Labels': {'com.docker.compose.project': project,\n"
        "       'com.docker.compose.service': service}}, 'NetworkSettings':\n"
        "      {'Networks': {network: {}}}, 'Mounts': mounts}\n"
        "    template = (args[args.index('--format') + 1]\n"
        "                if '--format' in args else '')\n"
        "    if template == '{{.Id}}': print(doc['Id'])\n"
        "    elif template == '{{.Image}}': print(doc['Image'])\n"
        "    elif template == '{{.State.Running}}':\n"
        "      print('false' if os.path.exists(os.path.join(\n"
        "       os.environ['JOBFEED_MIGRATION_INPUT_HOST'], 'post-inspection.json'))\n"
        "       else 'true')\n"
        "    elif 'compose.project' in template: print(project)\n"
        "    elif 'compose.service' in template: print(service)\n"
        "    elif 'NetworkSettings.Networks' in template: print(network)\n"
        "    else: print(json.dumps(doc))\n"
        "elif args[:2] == ['network', 'inspect']:\n"
        "    doc = {'Id': 'd' * 64, 'Name': network,\n"
        "      'Internal': True, 'Labels':\n"
        "      {'com.docker.compose.project': project}}\n"
        "    template = (args[args.index('--format') + 1]\n"
        "                if '--format' in args else '')\n"
        "    if template == '{{.Name}}': print(network)\n"
        "    elif template == '{{.Internal}}': print('true')\n"
        "    else: print(json.dumps(doc))\n"
        "elif args[:1] == ['wait']:\n"
        "    root = os.environ['JOBFEED_MIGRATION_OUTPUT_HOST']\n"
        "    open(os.path.join(root, 'provenance-verified.json'), 'w').write('{}')\n"
        "    print('0')\n"
        "if 'down' in sys.argv and os.environ.get('FAKE_DOCKER_DOWN_FAIL') == '1':\n"
        "    raise SystemExit(42)\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary, log


def _run_wrapper(
    tmp_path: Path,
    *arguments: str,
    cleanup_fails: bool = False,
    extra_env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    _, log = _fake_docker(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_DOCKER_DOWN_FAIL": "1" if cleanup_fails else "0",
    }
    env.update(extra_env or {})
    result = subprocess.run(
        [str(_WRAPPER), *arguments],
        cwd=_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    documents = (
        [json.loads(line) for line in log.read_text("utf-8").splitlines()]
        if log.exists()
        else []
    )
    return result, documents


def test_capture_route_uses_run_scoped_profile_and_rewrites_unicode_paths(
    tmp_path: Path,
) -> None:
    """Mount exact host paths and always tear the migration project down."""
    input_workspace = tmp_path / "迁移 input with spaces"
    input_workspace.mkdir()
    dump = input_workspace / "源 snapshot.dump"
    dump.write_bytes(b"pgdump")
    output_workspace = tmp_path / "迁移 evidence with spaces"
    output_workspace.mkdir()
    artifact = output_workspace / "输出 bundle"

    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "capture-postgres-baseline",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(artifact),
        "--machine-token-env",
        "JOBFEED_BENCH_MACHINE_TOKEN",
    )

    assert result.returncode == 0, result.stderr
    assert len(calls) > _RUN_AND_DOWN_CALLS
    run = next(call for call in calls if "up" in call["argv"])
    argv = run["argv"]
    assert argv[:2] == ["compose", "--file"]
    assert argv[2] == str(_COMPOSE)
    assert "--project-name" in argv
    project = argv[argv.index("--project-name") + 1]
    assert project.startswith("jobfeed-migration-")
    assert argv[argv.index("--profile") : argv.index("--profile") + 2] == [
        "--profile",
        "migration",
    ]
    assert argv[argv.index("up") : argv.index("up") + 7] == [
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "1800",
        "restore-source",
        "restore-scratch",
    ]
    detached = next(call for call in calls if "run" in call["argv"])
    detached_argv = detached["argv"]
    assert detached_argv[
        detached_argv.index("run") : detached_argv.index("run") + 4
    ] == [
        "run",
        "--build",
        "-d",
        "--no-deps",
    ]
    assert "_capture-preprovisioned-baseline" in detached_argv
    assert "/migration/artifacts/输出 bundle" in detached_argv
    assert run["env"]["dump"] == str(dump.resolve())
    assert run["env"]["artifacts"] == str(output_workspace.resolve())
    assert run["env"]["run_root"]
    assert len(run["env"]["git_commit"]) == _GIT_COMMIT_LENGTH
    assert any(call["argv"][:1] == ["inspect"] for call in calls)
    assert any(call["argv"][:1] == ["wait"] for call in calls)
    assert detached["env"] == {
        "dump": str(dump.resolve()),
        "artifacts": str(output_workspace.resolve()),
        "run_root": detached["env"]["run_root"],
        "git_commit": detached["env"]["git_commit"],
    }
    down = next(call["argv"] for call in calls if "down" in call["argv"])
    assert project in down
    assert down[-3:] == ["--volumes", "--remove-orphans", "--timeout=30"]
    assert "--wait-timeout" in argv


def test_import_route_reuses_isolated_restore_and_forwards_only_artifact_path(
    tmp_path: Path,
) -> None:
    """Canonical forward import uses the same socket-free run-scoped project."""
    input_dir = tmp_path / "导入 source"
    input_dir.mkdir()
    dump = input_dir / "source 0007.dump"
    dump.write_bytes(b"pgdump")
    output_dir = tmp_path / "导入 evidence"
    output_dir.mkdir()
    artifact = output_dir / "cutover bundle"

    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "import-postgres-snapshot",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(artifact),
    )

    assert result.returncode == 0, result.stderr
    detached = next(call for call in calls if "run" in call["argv"])
    argv = detached["argv"]
    assert "_import-preprovisioned-snapshot" in argv
    assert "/migration/artifacts/cutover bundle" in argv
    assert "--workload" not in argv
    assert "--machine-token-env" not in argv
    down = next(call["argv"] for call in calls if "down" in call["argv"])
    assert down[-3:] == [
        "--volumes",
        "--remove-orphans",
        "--timeout=30",
    ]


@pytest.mark.parametrize("formal_phase", ["same", "absent"])
def test_import_requires_stable_read_only_formal_fingerprint(
    tmp_path: Path, formal_phase: str
) -> None:
    """Stable present and explicit absent formal resources both pass."""
    source = tmp_path / "source.dump"
    source.write_bytes(b"pgdump")
    output = tmp_path / "output"
    output.mkdir()

    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "import-postgres-snapshot",
        "--source-dump",
        str(source),
        "--artifact-dir",
        str(output / "bundle"),
        extra_env={"FAKE_FORMAL_PHASE": formal_phase},
    )

    assert result.returncode == 0, result.stderr
    assert [call["argv"][:2] for call in calls].count(
        ["inspect", "jobfeed-postgres-1"]
    ) == _FORMAL_FINGERPRINT_READS
    assert [call["argv"][:3] for call in calls].count(
        ["volume", "inspect", "jobfeed_pgdata"]
    ) == _FORMAL_FINGERPRINT_READS


def test_formal_after_fingerprint_is_captured_after_work_before_provenance() -> None:
    """Persist the observed post-work state rather than a seeded before copy."""
    wrapper = _WRAPPER.read_text("utf-8")
    capture_ready = wrapper.index('[ -f "$output_host/capture-ready.json" ] || fail')
    post_work = wrapper.index('formal_after_work="$(fingerprint_formal_resources)"')
    publish_gate = wrapper.index('write_inspection "$input_host/post-inspection.json"')

    assert capture_ready < post_work < publish_gate


def test_import_rejects_changed_formal_fingerprint_after_cleanup(
    tmp_path: Path,
) -> None:
    """Canonical acceptance fails when a formal resource identity changes."""
    source = tmp_path / "source.dump"
    source.write_bytes(b"pgdump")
    output = tmp_path / "output"
    output.mkdir()

    result, _calls = _run_wrapper(
        tmp_path,
        "migrate",
        "import-postgres-snapshot",
        "--source-dump",
        str(source),
        "--artifact-dir",
        str(output / "bundle"),
        extra_env={"FAKE_FORMAL_PHASE": "changed"},
    )

    assert result.returncode != 0
    assert "formal PostgreSQL resources changed" in result.stderr


def test_capture_polls_runner_state_before_blocking_wait() -> None:
    """Runner failure is observed through bounded state polls before docker wait."""
    wrapper = _WRAPPER.read_text("utf-8")
    assert "{{.State.Running}}" in wrapper
    assert "runner exited before capture-ready" in wrapper
    assert "deadline=$((SECONDS + migration_timeout))" in wrapper


def test_capture_cleanup_failure_is_nonzero_and_prints_recovery_command(
    tmp_path: Path,
) -> None:
    """A failed Compose down cannot be mistaken for a successful capture."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    dump = input_dir / "source.dump"
    dump.write_bytes(b"pgdump")
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "capture-postgres-baseline",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(output_dir / "bundle"),
        cleanup_fails=True,
    )

    assert len(calls) > _RUN_AND_DOWN_CALLS
    assert result.returncode != 0
    assert "cleanup failed" in result.stderr
    assert "docker compose" in result.stderr
    assert "down --volumes --remove-orphans" in result.stderr


def test_capture_rejects_rw_alias_to_dump(tmp_path: Path) -> None:
    """The migration dump cannot be re-exposed through RW artifacts."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    dump = input_dir / "source.dump"
    dump.write_bytes(b"pgdump")
    rejected, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "capture-postgres-baseline",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(input_dir / "bundle"),
    )
    assert rejected.returncode != 0
    assert "read-write artifact mount would expose source dump" in rejected.stderr
    assert calls == []


def test_capture_rejects_user_supplied_attestation_before_docker(
    tmp_path: Path,
) -> None:
    """The public route derives provenance and never forwards user evidence."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    dump = input_dir / "source.dump"
    dump.write_bytes(b"pgdump")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "capture-postgres-baseline",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(output_dir / "bundle"),
        "--source-restore-attestation",
        "forged.json",
    )
    assert result.returncode != 0
    assert "not accepted" in result.stderr
    assert calls == []


def test_capture_ignores_compose_file_environment_override(tmp_path: Path) -> None:
    """Canonical migration always names the reviewed Compose file explicitly."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    dump = input_dir / "source.dump"
    dump.write_bytes(b"pgdump")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result, calls = _run_wrapper(
        tmp_path,
        "migrate",
        "capture-postgres-baseline",
        "--source-dump",
        str(dump),
        "--artifact-dir",
        str(output_dir / "bundle"),
        extra_env={"COMPOSE_FILE": "/tmp/attacker-compose.yml"},
    )
    assert result.returncode == 0, result.stderr
    assert all("/tmp/attacker-compose.yml" not in call["argv"] for call in calls)
    compose_calls = [call for call in calls if call["argv"][:1] == ["compose"]]
    assert all(call["argv"][1:3] == ["--file", str(_COMPOSE)] for call in compose_calls)


def test_migration_compose_services_are_socket_free_and_formal_db_independent() -> None:
    """Migration profile has exactly its isolated network, stores, and mounts."""
    document = yaml.safe_load(_COMPOSE.read_text("utf-8"))
    services = document["services"]
    source = services["restore-source"]
    scratch = services["restore-scratch"]
    runner = services["migration-runner"]

    for service in (source, scratch, runner):
        assert service["profiles"] == ["migration"]
        assert service.get("ports") is None
        assert service["networks"] == ["migration-internal"]
        volumes = service.get("volumes", [])
        assert all("docker.sock" not in str(volume) for volume in volumes)
    assert set(runner["depends_on"]) == {"restore-source", "restore-scratch"}
    assert "postgres" not in runner["depends_on"]
    assert runner["build"]["dockerfile"] == "Dockerfile.migration"
    assert set(runner["environment"]) == {
        "JOBFEED_MIGRATION_PG_URL",
        "JOBFEED_MIGRATION_SCRATCH_PG_URL",
        "JOBFEED_BENCH_MACHINE_TOKEN",
        "JOBFEED_MIGRATION_GIT_COMMIT",
        "JOBFEED_MIGRATION_TIMEOUT_SECONDS",
    }
    assert all("API_KEY" not in key for key in runner["environment"])
    mounts = {volume["target"]: volume for volume in runner["volumes"]}
    assert mounts["/run/jobfeed-migration/input"]["read_only"] is True
    assert mounts["/run/jobfeed-migration/output"].get("read_only") is not True
    assert (
        mounts["/run/jobfeed-migration/input"]["source"]
        != mounts["/run/jobfeed-migration/output"]["source"]
    )
    assert document["networks"]["migration-internal"]["internal"] is True
    assert all("pgdata" not in str(volume) for volume in source["volumes"])
    assert all("pgdata" not in str(volume) for volume in scratch["volumes"])
    migration_dockerfile = (_ROOT / "Dockerfile.migration").read_text("utf-8")
    assert migration_dockerfile.startswith("FROM postgres:16-bookworm AS pg16\n")
    assert "FROM scratch\n" in migration_dockerfile
    assert "COPY --from=pg16 / /" in migration_dockerfile
    assert "postgresql-client" not in migration_dockerfile
    assert "docker.io" not in migration_dockerfile
    assert "docker-cli" not in migration_dockerfile


def test_migration_compose_config_is_parseable_when_docker_is_available(
    tmp_path: Path,
) -> None:
    """Docker Compose accepts spaces and Unicode in inactive-safe bind sources."""
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    dump = tmp_path / "源 dump with spaces"
    dump.write_bytes(b"pgdump")
    artifacts = tmp_path / "输出 parent"
    artifacts.mkdir()
    result = subprocess.run(
        ["docker", "compose", "--profile", "migration", "config", "--format", "json"],
        cwd=_ROOT,
        env={
            **os.environ,
            "JOBFEED_MIGRATION_DUMP_HOST": str(dump),
            "JOBFEED_MIGRATION_ARTIFACT_PARENT_HOST": str(artifacts),
        },
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    configured = json.loads(result.stdout)
    runner = configured["services"]["migration-runner"]
    assert runner.get("ports") in (None, [])
    assert configured["networks"]["migration-internal"]["internal"] is True
