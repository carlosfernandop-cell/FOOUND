"""Exercise refusal paths with a fake psql. No database is contacted."""
import os
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).parent


@pytest.mark.parametrize("target, host", [("postgres", "127.0.0.1"),
                                         ("x';drop database x;", "127.0.0.1"),
                                         ("foound_017", "production.invalid")])
def test_harness_refuses_unsafe_targets_even_with_override(target, host):
    env = dict(os.environ, PGDATABASE=target, PGHOST=host, FOOUND_017_DISPOSABLE="1")
    result = subprocess.run(["bash", "sql/dev/run_017_harness.sh"], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "REFUSED" in result.stdout


@pytest.mark.parametrize("mode", ["error", "occupied"])
def test_preflight_failure_or_nonempty_schema_never_reaches_ddl(tmp_path, mode):
    psql = tmp_path / "psql"
    log = tmp_path / "calls"
    psql.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HARNESS_CALLS"\n'
                    'if [ "$HARNESS_MODE" = error ]; then exit 1; fi\necho 1\n')
    psql.chmod(0o700)
    env = dict(os.environ, PGDATABASE="foound_017", PGHOST="127.0.0.1",
               PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
               HARNESS_CALLS=str(log), HARNESS_MODE=mode,
               FOOUND_017_CREATE_DB="0")
    result = subprocess.run(["bash", "sql/dev/run_017_harness.sh"], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode != 0
    calls = log.read_text()
    assert "pg_class" in calls
    assert "-f sql/" not in calls
    assert "drop database" not in calls
