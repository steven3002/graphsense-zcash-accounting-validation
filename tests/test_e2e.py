"""End to end, with the two real GraphSense environments. Skipped until `install` has been run.

The environments are looked for in `.envs` of the repository, or in the directory named by GS_ZEC_VALIDATE_ENV_DIR.
"""

import json
import os
import subprocess
import sys

import pytest

from conftest import ENV_DIR, ROOT, edit_manifest, environments_installed
from gs_zec_validate import envs
from gs_zec_validate import report as report_module

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not environments_installed(), reason="the GraphSense environments are not installed"),
]


def run(root, report_dir, *arguments):
    command = [
        sys.executable,
        os.path.join(str(root), "gs-zec-validate.py"),
        "run",
        "--env-dir",
        ENV_DIR,
        "--report-dir",
        str(report_dir),
        *arguments,
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    )
    report = None
    if os.path.isfile(os.path.join(str(report_dir), "report.json")):
        with open(os.path.join(str(report_dir), "report.json"), encoding="utf-8") as handle:
            report = json.load(handle)
    return completed.returncode, completed.stdout + completed.stderr, report


def test_the_pinned_pair_passes_with_the_published_checksum(tmp_path):
    status, text, report = run(ROOT, tmp_path / "report")
    assert status == 0, text
    assert report["verdict"] == "pass" and report["results"]["canonical"] is True
    assert report["results_sha256"] == report_module.read_published(ROOT)["results"]
    assert report["results"]["summary"]["after"]["cases_matching"] == 47
    assert len(report["results"]["summary"]["before"]["cases_differing_from_expected"]) == 13
    for side in ("before", "after"):
        assert report["run"]["environments"][side]["loopback_connections"] > 0
    assert "PASS (exit status 0)" in text and (tmp_path / "report" / "report.md").is_file()


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX pipe")
@pytest.mark.parametrize("unbuffered", [False, True])
def test_a_run_whose_output_nobody_reads_still_completes(tmp_path, unbuffered):
    """`run | head`: the screen output is lost, the report is complete and the exit status is the verdict."""
    reader, writer = os.pipe()
    os.close(reader)
    command = [sys.executable, *(["-u"] if unbuffered else []), os.path.join(ROOT, "gs-zec-validate.py"), "run"]
    try:
        completed = subprocess.run(
            [*command, "--env-dir", ENV_DIR, "--report-dir", str(tmp_path / "report")],
            stdout=writer,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    finally:
        os.close(writer)
    assert (completed.returncode, completed.stderr) == (0, "")
    with open(tmp_path / "report" / "report.json", encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["verdict"] == "pass" and report["exit_status"] == 0
    assert report["results_sha256"] == report_module.read_published(ROOT)["results"]
    assert (tmp_path / "report" / "report.md").is_file()


def test_same_checksum_from_another_working_directory(tmp_path):
    command = [sys.executable, os.path.join(ROOT, "gs-zec-validate.py"), "run", "--env-dir", ENV_DIR]
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    with open(
        tmp_path / "report" / "report.json", encoding="utf-8"
    ) as handle:  # the default report directory is ./report
        assert json.load(handle)["matches_published"] is True


def test_old_code_as_after_fails_on_exactly_the_affected_cases(tmp_path, manifest):
    status, text, report = run(ROOT, tmp_path / "report", "--after-python", envs.env_python(ENV_DIR, "before"))
    assert status == 1 and report["results"]["canonical"] is False and "NON-CANONICAL RUN" in text
    affected = sorted(case["id"] for case in manifest["cases"] if case["old_code"]["differs"])
    assert report["results"]["summary"]["after"]["cases_mismatching"] == affected and len(affected) == 13
    assert all(
        case["after"]["matches_expected"]
        for case in report["results"]["cases"]
        if not case["old_code_expected_to_differ"]
    )


def test_corrected_code_as_before_fails(tmp_path):
    status, _, report = run(ROOT, tmp_path / "report", "--before-python", envs.env_python(ENV_DIR, "after"))
    assert status == 1
    failing = {check["id"] for check in report["results"]["checks"] if check["status"] == "fail"}
    assert failing == {"before.frozen", "before.explained"}


def test_expected_value_changed_by_one_zatoshi_fails(copy_of_repo, tmp_path):
    def change(document):
        next(case for case in document["cases"] if case["id"] == "3463373/2")["expected"]["fee"] += 1

    edit_manifest(copy_of_repo, change)
    status, text, _ = run(copy_of_repo, tmp_path / "report")
    assert status == 1
    assert (
        "case 3463373/2 (6f37465a…04c8) fee: the manifest says 30001, the evidence (graphsense-lib-fixtures) gives 30000"
        in text
    )
    assert "case 3463373/2 (6f37465a…04c8) fee: expected 30001, after reports 30000" in text


def test_wrong_revision_in_an_environment_is_exit_3(tmp_path):
    """The 'after' environment holds another version than the pinned one, and no override was given."""
    swapped = tmp_path / "envs"
    swapped.mkdir()
    os.symlink(os.path.join(ENV_DIR, "before"), swapped / "before")
    os.symlink(os.path.join(ENV_DIR, "before"), swapped / "after")
    command = [
        sys.executable,
        os.path.join(ROOT, "gs-zec-validate.py"),
        "run",
        "--env-dir",
        str(swapped),
        "--report-dir",
        str(tmp_path / "report"),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 3
    assert "gs-zec-validate: error: after: found graphsense-lib 2.16.3, the manifest pins 2.16.4" in completed.stderr
    assert not (tmp_path / "report" / "report.json").exists()


def test_source_directory_of_the_pinned_code_is_canonical(tmp_path):
    """--after-src with the very files of the pinned release: identified by checksum, so the run stays canonical."""
    import shutil
    import zipfile

    wheel = os.path.join(ROOT, "vendor", "graphsense_lib-2.16.4-py3-none-any.whl")
    if not os.path.isfile(wheel):
        pytest.skip("the wheel is not carried in vendor/")
    source = tmp_path / "src"
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(source)
    shutil.rmtree(next(source.glob("*.dist-info")))
    status, text, report = run(ROOT, tmp_path / "report", "--after-src", str(source))
    assert status == 0, text
    assert report["results"]["canonical"] is True and report["run"]["environments"]["after"]["override"] == "src"
    assert report["run"]["environments"]["after"]["graphsense_lib_version"] is None
    assert report["matches_published"] is True, "the checksum does not depend on how the pinned code was provided"


def test_relative_paths_work(tmp_path):
    """--env-dir and --after-python given relative to the working directory (as the reviewer page writes them)."""
    link = tmp_path / "envs"
    os.symlink(ENV_DIR, link)
    command = [sys.executable, os.path.join(ROOT, "gs-zec-validate.py"), "run", "--env-dir", "envs"]
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    relative = os.path.relpath(envs.env_python(str(link), "before"), tmp_path)
    completed = subprocess.run(
        [*command, "--after-python", relative], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 1 and "NON-CANONICAL RUN" in completed.stdout


def test_output_that_the_console_cannot_encode_does_not_crash(tmp_path):
    command = [
        sys.executable,
        os.path.join(ROOT, "gs-zec-validate.py"),
        "run",
        "--env-dir",
        ENV_DIR,
        "--report-dir",
        str(tmp_path / "report"),
    ]
    completed = subprocess.run(
        command, capture_output=True, check=False, env={**os.environ, "PYTHONIOENCODING": "ascii"}
    )
    assert completed.returncode == 0, completed.stderr
    assert b"PASS (exit status 0)" in completed.stdout and b"bd84ece1?6517" in completed.stdout
