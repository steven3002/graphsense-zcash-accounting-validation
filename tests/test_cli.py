"""The command line: exit status and message for everything that stops a run before GraphSense is started,
and what `install` prints.

Most of these tests start the launcher as a separate process, as a user would.
"""

import contextlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading

import pytest

from conftest import ROOT, edit_manifest, fake_worker
from gs_zec_validate import __version__, cli, envs, progress


def run(root, *arguments, env=None):
    completed = subprocess.run(
        [sys.executable, os.path.join(str(root), "gs-zec-validate.py"), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})},
    )
    return completed.returncode, completed.stdout, completed.stderr


def one_line_error(stderr):
    lines = [line for line in stderr.strip().splitlines() if line]
    assert len(lines) == 1 and lines[0].startswith("gs-zec-validate: error: ") and "Traceback" not in stderr, stderr
    return lines[0]


def test_version_and_help():
    assert run(ROOT, "--version")[1].strip() == f"gs-zec-validate {__version__}"
    status, out, _ = run(ROOT, "run", "--help")
    assert status == 0 and "exit status:" in out and "--report-dir" in out and "--after-python" in out
    assert "--verbose" in run(ROOT, "install", "--help")[1]


def test_usage_error_is_exit_2():
    assert run(ROOT)[0] == 2
    assert run(ROOT, "run", "--no-such-option")[0] == 2
    status, _, err = run(ROOT, "run", "--after-src", os.path.join(ROOT, "docs"))
    assert status == 2 and "no graphsenselib package in that directory" in err
    assert run(ROOT, "run", "--after-src", "x", "--after-python", "y")[0] == 2


def test_environments_not_installed_is_exit_3(copy_of_repo, tmp_path):
    status, _, err = run(
        copy_of_repo, "run", "--env-dir", str(tmp_path / "nowhere"), "--report-dir", str(tmp_path / "out")
    )
    assert status == 3
    assert re.fullmatch(
        r"gs-zec-validate: error: interpreter not found: .*before.* \(run the install step first\)", one_line_error(err)
    )


def test_missing_block_evidence_is_exit_3(copy_of_repo, tmp_path):
    os.remove(copy_of_repo / "evidence" / "zebra-6.4.2" / "block_1687194.json.gz")
    status, _, err = run(copy_of_repo, "run", "--report-dir", str(tmp_path / "out"))
    assert (
        status == 3
        and one_line_error(err)
        == "gs-zec-validate: error: evidence file missing: evidence/zebra-6.4.2/block_1687194.json.gz"
    )


def test_missing_previous_output_evidence_is_exit_3(copy_of_repo, tmp_path, manifest):
    path = manifest["previous_transactions"][0]["evidence"]["path"]
    os.remove(copy_of_repo / path)
    status, _, err = run(copy_of_repo, "run", "--report-dir", str(tmp_path / "out"))
    assert status == 3 and one_line_error(err) == f"gs-zec-validate: error: evidence file missing: {path}"


def test_one_changed_byte_of_evidence_is_exit_3(copy_of_repo, tmp_path):
    path = copy_of_repo / "evidence" / "graphsense-lib-fixtures" / "zcash_block_3479000.json.gz"
    data = bytearray(path.read_bytes())
    data[1000] ^= 0x01
    path.write_bytes(bytes(data))
    status, _, err = run(copy_of_repo, "run", "--report-dir", str(tmp_path / "out"))
    assert status == 3
    assert one_line_error(err).startswith(
        "gs-zec-validate: error: evidence file changed: evidence/graphsense-lib-fixtures/zcash_block_3479000.json.gz: sha256 is "
    )


@pytest.mark.parametrize(
    "change, message",
    [
        (
            lambda d: d["cases"][3]["expected"].update(fee="7000"),
            r"manifest does not follow its schema: \$\.cases\[3\]\.expected\.fee: expected integer, found string",
        ),
        (lambda d: d["cases"].append(dict(d["cases"][3])), "manifest is inconsistent: case 600000/3 is listed twice"),
        (
            lambda d: d["cases"].pop(3),
            r"manifest is inconsistent: block 600000 has 11 transactions but 10 cases \(no case for position 3\)",
        ),
    ],
    ids=["schema violation", "duplicated case", "removed case"],
)
def test_broken_manifest_is_exit_3(copy_of_repo, tmp_path, change, message):
    edit_manifest(copy_of_repo, change)
    status, _, err = run(copy_of_repo, "run", "--report-dir", str(tmp_path / "out"))
    assert status == 3 and re.search(message, one_line_error(err)), err
    assert run(copy_of_repo, "install", "--env-dir", str(tmp_path / "envs"))[0] == 3, (
        "install refuses a broken manifest too"
    )


@pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs a directory the user cannot write to",
)
def test_unwritable_report_directory_is_exit_4(copy_of_repo, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        status, _, err = run(copy_of_repo, "run", "--report-dir", str(locked / "report"))
    finally:
        locked.chmod(stat.S_IRWXU)
    assert status == 4 and one_line_error(err).startswith("gs-zec-validate: error: cannot write the report: ")


def test_report_path_that_is_a_file_is_exit_4(copy_of_repo, tmp_path):
    blocker = tmp_path / "report"
    blocker.write_text("in the way")
    status, _, err = run(copy_of_repo, "run", "--report-dir", str(blocker))
    assert status == 4 and "cannot write the report" in one_line_error(err)


def test_changed_lock_file_is_refused_by_install(copy_of_repo, tmp_path):
    with open(copy_of_repo / "locks" / "after.txt", "a", encoding="utf-8") as handle:
        handle.write("requests==2.0.0\n")
    status, _, err = run(copy_of_repo, "install", "--env-dir", str(tmp_path / "envs"))
    assert status == 3 and "lock file changed: locks/after.txt" in err


def test_explain_needs_no_environment(manifest):
    status, out, _ = run(ROOT, "explain", "1687194/4")
    assert status == 0
    assert "Sapling balance               +89,000" in out and "Orchard balance               -88,000" in out
    assert "fee                             1,000   = the remaining value" in out
    assert "equals the expected values in the manifest: yes" in out and "NO" not in out.replace("NON", "")
    assert run(ROOT, "explain", "nothing-like-this")[0] == 2


def run_unread(*arguments, unbuffered):
    """Start the launcher with a standard output that nobody reads: the reading end is closed before it starts."""
    reader, writer = os.pipe()
    os.close(reader)
    try:
        completed = subprocess.run(
            [sys.executable, *(["-u"] if unbuffered else []), os.path.join(ROOT, "gs-zec-validate.py"), *arguments],
            stdout=writer,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    finally:
        os.close(writer)
    return completed.returncode, completed.stderr


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX pipe")
@pytest.mark.parametrize("unbuffered", [False, True])
def test_output_that_nobody_reads_does_not_change_the_exit_status(unbuffered):
    """`... | head`: the reader goes away; the command still ends with its own exit status and without a traceback."""
    assert run_unread("explain", "1687194/4", unbuffered=unbuffered) == (0, "")
    assert run_unread("--version", unbuffered=unbuffered) == (0, "")
    status, err = run_unread("explain", "nothing-like-this", unbuffered=unbuffered)
    assert status == 2 and "Traceback" not in err and "BrokenPipe" not in err


def test_python_outside_the_supported_range_gets_a_clear_message(monkeypatch):
    monkeypatch.setattr(envs.shutil, "which", lambda name: None)
    monkeypatch.setattr(envs.sys, "version_info", (3, 14, 0, "final", 0))
    with pytest.raises(envs.SetupError, match=r"Python 3\.14 cannot run graphsense-lib 2\.16\.x"):
        envs.install(ROOT, {"revisions": {}}, "unused", installer="auto", echo=lambda line: None)


def test_installer_that_cannot_be_started_is_a_clear_error(tmp_path, manifest):
    with pytest.raises(envs.SetupError, match="cannot run 3.12: "):
        envs.install(ROOT, manifest, str(tmp_path / "envs"), installer="pip", python="3.12", echo=lambda line: None)


def test_install_never_replaces_a_directory_that_is_not_an_environment(tmp_path, manifest):
    (tmp_path / "envs" / "before").mkdir(parents=True)
    (tmp_path / "envs" / "before" / "precious.txt").write_text("keep me")
    with pytest.raises(
        envs.SetupError, match="refusing to replace .*before: it exists and is not a virtual environment"
    ):
        envs.install(
            ROOT, manifest, str(tmp_path / "envs"), installer="pip", python=sys.executable, echo=lambda line: None
        )
    assert (tmp_path / "envs" / "before" / "precious.txt").read_text() == "keep me"


# What `install` prints. Stand-ins replace uv, pip and the two environments; the lines they write are real
# output of uv 0.12 and pip 23 to 26.

UV_VENV = [
    "Using CPython 3.12.15 interpreter at: /usr/local/bin/python3\n",
    "Creating virtual environment at: .envs/before\n",
]
UV_SYNC = [
    "Using Python 3.12.15 environment at: .envs/before\n",
    "Resolved 21 packages in 502ms\n",
    "Downloading pydantic-core (2.0MiB)\n",
    " Downloaded pydantic-core\n",
    "Prepared 21 packages in 289ms\n",
    "Installed 21 packages in 59ms\n",
    " + annotated-types==0.8.0\n",
]
PIP_INSTALL = [
    "Looking in links: /somewhere/vendor\n",
    "Collecting graphsense-lib==2.16.3 (from -r /somewhere/locks/before.txt (line 3))\n",
    "  Downloading graphsense_lib-2.16.3-py3-none-any.whl (1.2 MB)\n",
    "     ━━━━━━━━━━━━━━━━━━━━ 1.2/1.2 MB 30.8 MB/s eta 0:00:00\n",
    "Collecting base58==2.1.1\n",
    "  Using cached base58-2.1.1-py3-none-any.whl (5.6 kB)\n",
    "Installing collected packages: base58, graphsense-lib\n",
    "Successfully installed base58-2.1.1 graphsense-lib-2.16.3\n",
]
UV = "/opt/tools/uv"
VENDOR = os.path.join(ROOT, "vendor")  # optional: the package also works without it
A_TERMINAL = {"TERM": "xterm-256color"}


class Screen:
    """Stands in for standard output: it records what is written and shows what a terminal would then hold."""

    def __init__(self, terminal=True, encoding="utf-8", broken=False, descriptor=None):
        self.terminal, self.encoding, self.broken, self.written = terminal, encoding, broken, []
        self.descriptor, self.drawn = descriptor, threading.Event()

    def isatty(self):
        return self.terminal

    def fileno(self):
        if self.descriptor is None:
            raise OSError("this stand-in has no file descriptor")
        return self.descriptor

    def write(self, text):
        if self.broken and text.startswith("\r"):
            raise OSError("this terminal refuses the animated line")
        self.written.append(text)
        if text.startswith("\r") and text.strip("\r "):
            self.drawn.set()
        return len(text)

    def next_frame(self):
        """Wait until one more frame has been drawn (by the animation thread, on its own clock)."""
        self.drawn.clear()
        assert self.drawn.wait(timeout=10)

    def flush(self):
        pass

    def output(self):
        return "".join(self.written)

    def shows(self):
        """(the lines on the screen, the cursor's column). The last line is the one the cursor is on.

        A carriage return goes back to the start of the line; a line feed starts the next line.
        """
        lines, column = [""], 0
        for character in self.output():
            if character == "\n":
                lines.append("")
                column = 0
            elif character == "\r":
                column = 0
            else:
                lines[-1] = lines[-1][:column] + character + lines[-1][column + 1 :]
                column += 1
        return [line.rstrip() for line in lines], column

    def animated(self):
        """Every piece that was written after a carriage return: the frames, and the spaces that wipe."""
        return [piece for text in self.written if text.startswith("\r") for piece in text[1:].split("\r")]

    def frames(self):
        return [piece for piece in self.animated() if piece[:1] in progress.DOTS + progress.ASCII and piece[1:2] == " "]

    def texts(self):
        """The texts of the animated line in the order they appeared, each once."""
        texts = [frame[2:] for frame in self.frames()]
        return [text for index, text in enumerate(texts) if index == 0 or text != texts[index - 1]]


class Installer:
    """Stands in for a uv or pip process. Before and after each line it writes, time passes: `meanwhile` is called."""

    def __init__(self, command, lines, returncode, meanwhile):
        self.command, self.returncode, self.killed = command, returncode, False
        self.stdout = self._write(lines, meanwhile)

    @staticmethod
    def _write(lines, meanwhile):
        for line in lines:
            meanwhile()
            yield line
        meanwhile()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def kill(self):
        self.killed = True


def as_standard_output(monkeypatch):
    """Make standard output a terminal of 100 columns, with nothing in the environment that speaks against an animation."""
    screen = Screen(descriptor=1)
    monkeypatch.setattr(sys, "stdout", screen)
    monkeypatch.setattr(progress.os, "get_terminal_size", lambda descriptor: os.terminal_size((100, 30)))
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("CI", raising=False)
    return screen


def animating():
    return any(thread.name == "progress line" for thread in threading.enumerate())


def stand_ins(monkeypatch, manifest, meanwhile=lambda: None, returncode=0, lines=None):
    """Put the stand-ins in place. Returns the list in which every process that is started gets recorded."""
    started = []

    def start(command, **options):
        if lines is not None:
            written = lines
        elif "sync" in command:
            written = UV_SYNC
        elif "pip" in command:
            written = PIP_INSTALL
        else:
            written = UV_VENV if command[0] == UV else []  # `python -m venv` writes nothing
        process = Installer(command, written, returncode, meanwhile)
        started.append(process)
        return process

    def worker(python, job, timeout=600):
        meanwhile()
        return fake_worker(manifest, "before" if os.sep + "before" + os.sep in python else "after")

    monkeypatch.setattr(envs.subprocess, "Popen", start)
    monkeypatch.setattr(envs.shutil, "which", lambda name: UV)
    monkeypatch.setattr(envs, "run_worker", worker)
    return started


def install(shown, manifest, env_dir, installer, **options):
    envs.install(ROOT, manifest, str(env_dir), installer=installer, echo=shown.line, status=shown.text, **options)


def lines_that_stay(manifest, installer):
    """The quiet form. (The stand-in environments report Python 3.12.0.)"""
    pinned = [(side, manifest["revisions"][side]) for side in envs.SIDES]

    def carried(name):
        if os.path.isfile(os.path.join(VENDOR, name)):
            return "the copy in vendor/ has that checksum"
        return "no copy in vendor/"

    return [
        f"installer: {installer}",
        *(
            f"{side}: graphsense-lib {r['version']} (release file pinned by checksum; {carried(r['wheel']['filename'])}); "
            f"dependencies from {r['locks']['default']['path']}"
            for side, r in pinned
        ),
        *(
            f"{side}: graphsense-lib {r['version']} on Python 3.12.0, code checksum {r['package_tree_sha256'][:16]}… verified"
            for side, r in pinned
        ),
        "environments are ready",
    ]


@pytest.mark.parametrize("installer", ["uv", "pip"])
def test_install_prints_the_quiet_form(monkeypatch, tmp_path, manifest, installer):
    stand_ins(monkeypatch, manifest)
    screen = Screen(terminal=False)
    install(progress.Progress(screen), manifest, tmp_path / "envs", installer)
    assert screen.output() == "\n".join(lines_that_stay(manifest, installer)) + "\n"
    assert not re.search(r"^  \$ ", screen.output(), re.MULTILINE), "no command line"
    for path in (ROOT, str(tmp_path), sys.executable, UV):
        assert path not in screen.output()
    assert not re.search(r"(^|\s)(/|[A-Za-z]:\\)", screen.output(), re.MULTILINE), "no absolute path"


@pytest.mark.parametrize("copies", [True, False], ids=["a copy in vendor/", "no copy in vendor/"])
def test_the_status_line_says_what_was_checked_and_not_where_the_release_file_comes_from(
    monkeypatch, tmp_path, manifest, copies
):
    """Both wordings, whatever this checkout carries: the lock files with the two release files, and without them."""
    root = tmp_path / "root"
    shutil.copytree(os.path.join(ROOT, "locks"), root / "locks")
    if copies:
        if not os.path.isdir(VENDOR):
            pytest.skip("the release files are not carried in vendor/")
        shutil.copytree(VENDOR, root / "vendor")
    stand_ins(monkeypatch, manifest)
    lines = []
    envs.install(str(root), manifest, str(tmp_path / "envs"), installer="pip", python=sys.executable, echo=lines.append)
    found = "the copy in vendor/ has that checksum" if copies else "no copy in vendor/"
    assert lines[1:3] == [
        f"{side}: graphsense-lib {manifest['revisions'][side]['version']} (release file pinned by checksum; {found}); "
        f"dependencies from locks/{side}.txt"
        for side in envs.SIDES
    ]
    assert not re.search(r"PyPI|from vendor|\.whl", "\n".join(lines)), "no word about where the file is installed from"


def test_what_the_status_line_says_is_true_of_the_packaged_files(manifest):
    """Each lock file `install` accepts names the release with one sha256, the manifest's; a copy in vendor/ has it."""
    for side in envs.SIDES:
        revision = manifest["revisions"][side]
        pin = f"graphsense-lib=={revision['version']} \\\n    --hash=sha256:{revision['wheel']['sha256']}\n"
        for lock in revision["locks"].values():
            path = os.path.join(ROOT, *lock["path"].split("/"))
            assert envs._text_sha256_file(path) == lock["sha256"], lock["path"]
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            assert text.count("graphsense-lib==") == 1 and text.count(pin) == 1, lock["path"]
        copy = os.path.join(VENDOR, revision["wheel"]["filename"])
        if os.path.isfile(copy):
            assert envs._sha256_file(copy) == revision["wheel"]["sha256"]


@pytest.mark.parametrize("installer", ["uv", "pip"])
def test_verbose_prints_the_interpreter_and_every_command_and_no_progress_line(
    monkeypatch, tmp_path, manifest, installer
):
    """`--verbose` on the command line, on a terminal: what `install` printed before it became quiet by default."""
    threads = []
    started = stand_ins(monkeypatch, manifest, meanwhile=lambda: threads.append(animating()))
    screen = as_standard_output(monkeypatch)
    env_dir = str(tmp_path / "envs")
    assert cli.main(["install", "--verbose", "--installer", installer, "--env-dir", env_dir]) == 0
    links = ["--find-links", VENDOR] if os.path.isdir(VENDOR) else []
    commands = []
    for side in envs.SIDES:
        target, lock = os.path.join(env_dir, side), os.path.join(ROOT, "locks", f"{side}.txt")
        python = envs.env_python(env_dir, side)
        if installer == "uv":
            commands.append([UV, "venv", "--no-project", "--python", sys.executable, target])
            commands.append([UV, "pip", "sync", "--python", python, "--require-hashes", "--no-build", *links, lock])
        else:
            commands.append([sys.executable, "-m", "venv", target])
            options = ["--disable-pip-version-check", "--no-input", "--no-deps", "--require-hashes"]
            commands.append([python, "-m", "pip", "install", *options, "--only-binary", ":all:", *links, "-r", lock])
    assert [process.command for process in started] == commands
    quiet = lines_that_stay(manifest, installer)
    shown = ["  $ " + " ".join(command) for command in commands]
    assert screen.output().splitlines() == [
        f"installer: {installer}; interpreter for the environments: {sys.executable}",
        quiet[1],
        *shown[:2],
        quiet[2],
        *shown[2:],
        *quiet[3:],
    ]
    assert "\r" not in screen.output() and "\x1b" not in screen.output() and threads and not any(threads)


def test_install_on_the_command_line_shows_frames_and_ends_with_the_quiet_form(monkeypatch, tmp_path, manifest):
    """Through the command line, on a terminal, with the animation thread running on its own clock."""
    waited = []

    def meanwhile():  # every process takes at least until one more frame is on the screen
        if len(started) > len(waited):
            waited.append(terminal.next_frame())

    started = stand_ins(monkeypatch, manifest, meanwhile=meanwhile)
    terminal = as_standard_output(monkeypatch)
    assert cli.main(["install", "--installer", "pip", "--env-dir", str(tmp_path / "envs")]) == 0
    assert len(started) == 4 and len(terminal.frames()) >= 4
    assert {f"{side}: {step}" for side in envs.SIDES for step in ("creating the environment", "installing")} <= set(
        terminal.texts()
    )
    assert terminal.shows() == ([*lines_that_stay(manifest, "pip"), ""], 0)
    assert all(len(piece) <= 99 for piece in terminal.animated())
    assert not animating()


@pytest.mark.parametrize("verbose", [False, True])
def test_a_failing_command_shows_the_command_and_the_end_of_its_output(monkeypatch, tmp_path, manifest, verbose):
    written = [f"line {number} of the installer\n" for number in range(1, 41)]
    screen = Screen()
    shown = progress.Progress(screen, animate=not verbose, environ=A_TERMINAL, columns=lambda: 80)
    started = stand_ins(monkeypatch, manifest, meanwhile=shown.tick, returncode=2, lines=written)
    with pytest.raises(envs.SetupError) as failure, contextlib.closing(shown):
        install(shown, manifest, tmp_path / "envs", "uv", verbose=verbose)
    command = " ".join(started[0].command)
    assert str(failure.value) == f"command failed with exit status 2: {command}\n" + "".join(written[-25:]).strip()
    lines, column = screen.shows()
    assert lines[-1] == "" and column == 0, "the animated line is gone before the error is shown"
    assert (f"  $ {command}" in lines) is verbose and bool(screen.frames()) is not verbose


@pytest.mark.skipif(os.name == "nt", reason="needs an executable script")
def test_a_failing_install_on_the_command_line(tmp_path, manifest):
    """The same through the launcher: exit status 3, the command and the last 25 lines of its output on standard error."""
    script = tmp_path / "not-a-python"
    script.write_text('#!/bin/sh\nfor n in $(seq 1 40); do echo "line $n of the installer"; done\nexit 7\n')
    script.chmod(stat.S_IRWXU)
    env_dir = tmp_path / "envs"
    command = f"{script} -m venv {env_dir / 'before'}"
    tail = "\n".join(f"line {number} of the installer" for number in range(16, 41))
    first = lines_that_stay(manifest, "pip")[1]
    for options, printed in (
        ((), ["installer: pip", first]),
        (("--verbose",), [f"installer: pip; interpreter for the environments: {script}", first, f"  $ {command}"]),
    ):
        status, out, err = run(
            ROOT, "install", "--installer", "pip", "--python", str(script), "--env-dir", str(env_dir), *options
        )
        assert status == 3 and out.splitlines() == printed
        assert err == f"gs-zec-validate: error: command failed with exit status 7: {command}\n{tail}\n"


def test_drawing_one_frame():
    assert progress.DOTS == "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" and progress.INTERVAL == 0.08
    assert progress.render("⠋", "before: installing", 80, 0) == ("\r⠋ before: installing", 20)
    # a text that is too long is cut, and the last column stays free
    characters, cells = progress.render("⠙", "x" * 100, 40, 0)
    assert characters == "\r⠙ " + "x" * 34 + "..." and cells == 39
    # a longer line before it is wiped first
    assert progress.render("⠹", "short", 80, 30) == ("\r" + " " * 30 + "\r⠹ short", 7)
    assert progress.render("⠸", "short", 80, 7) == ("\r⠸ short", 7)
    # also when the terminal has become narrower than that line was
    assert progress.render("⠼", "short", 20, 60) == ("\r" + " " * 19 + "\r⠼ short", 7)
    # terminals too narrow for a text
    assert progress.render("⠴", "text", 5, 0) == ("\r⠴...", 4) and progress.render("⠦", "text", 3, 0) == ("\r⠦ ", 2)


def test_a_terminal_that_does_not_tell_its_width_gets_no_frame():
    """`docker run -t` without anyone attached is such a terminal: 0 columns."""
    for columns in (None, lambda: 0, lambda: 1):
        screen = Screen()
        shown = progress.Progress(screen, environ=A_TERMINAL, columns=columns)
        shown.text("before: installing")
        shown.tick()
        shown.line("a line that stays")
        shown.close()
        assert shown.animated is True and screen.written == ["a line that stays\n"]


@pytest.mark.parametrize("installer", ["uv", "pip"])
def test_the_animated_line_from_the_first_frame_to_the_quiet_form(monkeypatch, tmp_path, manifest, installer):
    """A terminal, and installers that take two frames' time for everything they write."""
    screen = Screen()
    shown = progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 100)
    stand_ins(monkeypatch, manifest, meanwhile=lambda: (shown.tick(), shown.tick()))
    with contextlib.closing(shown):  # no animation thread: every frame is one the stand-ins asked for
        install(shown, manifest, tmp_path / "envs", installer)
    frames = screen.frames()
    assert len(frames) > 40 and [frame[0] for frame in frames[:12]] == list("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠋⠙")
    doing = {
        "uv": [
            "21 packages resolved",
            "downloading pydantic-core (2.0MiB)",
            "pydantic-core downloaded",
            "21 packages prepared",
            "21 packages installed",
        ],
        "pip": [
            "collecting graphsense-lib 2.16.3",
            "downloading graphsense_lib 2.16.3 (1.2 MB)",
            "collecting base58 2.1.1",
            "base58 2.1.1 from the cache",
            "2 packages",
            "2 packages installed",
        ],
    }[installer]
    assert screen.texts() == [
        *(
            text
            for side in envs.SIDES
            for text in (
                f"{side}: creating the environment",
                f"{side}: installing",
                *(f"{side}: installing - {what}" for what in doing),
            )
        ),
        "before: verifying the installed code",
        "after: verifying the installed code",
    ]
    assert screen.shows() == ([*lines_that_stay(manifest, installer), ""], 0), "nothing of the animation is left"
    assert all(len(piece) <= 99 for piece in screen.animated())
    assert set(re.findall(r"[\x00-\x1f\x7f]", screen.output())) == {"\r", "\n"}, "no escape sequence, no other control"


def test_the_animated_line_never_wraps(monkeypatch, tmp_path, manifest):
    screen = Screen()
    shown = progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 40)
    stand_ins(monkeypatch, manifest, meanwhile=shown.tick)
    install(shown, manifest, tmp_path / "envs", "uv")
    assert max(len(piece) for piece in screen.animated()) == 39
    assert "before: installing - downloading p..." in screen.texts(), "cut, and marked as cut"
    assert screen.shows() == ([*lines_that_stay(manifest, "uv"), ""], 0)


@pytest.mark.parametrize(
    "terminal, environ",
    [(False, A_TERMINAL), (True, {"TERM": "dumb"}), (True, {"TERM": "xterm-256color", "CI": "true"})],
    ids=["not a terminal", "TERM=dumb", "a CI system"],
)
def test_no_animation_and_no_control_character_off_a_terminal(monkeypatch, tmp_path, manifest, terminal, environ):
    screen = Screen(terminal=terminal)
    shown = progress.Progress(screen, environ=environ, columns=lambda: 80)
    stand_ins(monkeypatch, manifest, meanwhile=shown.tick)
    with shown:
        assert not animating()
        install(shown, manifest, tmp_path / "envs", "uv")
    assert shown.animated is False
    assert screen.output() == "\n".join(lines_that_stay(manifest, "uv")) + "\n"
    assert "\r" not in screen.output() and "\x1b" not in screen.output()


@pytest.mark.parametrize("encoding", ["latin-1", "ascii", "cp1252", "euc-jp", None, "no-such-encoding"])
def test_a_terminal_whose_encoding_lacks_the_dots_gets_ascii_frames(monkeypatch, tmp_path, manifest, encoding):
    screen = Screen(encoding=encoding)
    shown = progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 80)
    stand_ins(monkeypatch, manifest, meanwhile=shown.tick)
    install(shown, manifest, tmp_path / "envs", "uv")
    assert [frame[0] for frame in screen.frames()[:5]] == list("|/-\\|")
    assert not set(screen.output()) & set(progress.DOTS)
    assert screen.shows() == ([*lines_that_stay(manifest, "uv"), ""], 0)


def test_where_the_dots_are_used():
    utf8, latin1 = Screen(), Screen(encoding="latin-1")
    assert progress.frames(utf8, A_TERMINAL, "posix") == progress.DOTS
    assert progress.frames(Screen(encoding="gb18030"), A_TERMINAL, "posix") == progress.DOTS
    assert progress.frames(latin1, A_TERMINAL, "posix") == progress.ASCII
    assert progress.frames(utf8, {"TERM": "linux"}, "posix") == progress.ASCII, "the Linux console"
    assert progress.frames(utf8, A_TERMINAL, "nt") == progress.ASCII, "a Windows console"
    assert progress.animates(utf8, A_TERMINAL) and progress.animates(utf8, {})
    assert not progress.animates(Screen(terminal=False), A_TERMINAL)
    assert not progress.animates(utf8, {"TERM": "dumb"}) and not progress.animates(utf8, {"CI": ""})


def test_an_error_in_the_progress_line_does_not_fail_install(monkeypatch, tmp_path, manifest):
    # a terminal that fails on the first frame: the animation is off from then on, the lines that stay are all there
    screen = Screen(broken=True)
    shown = progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 80)
    stand_ins(monkeypatch, manifest, meanwhile=shown.tick)
    with contextlib.closing(shown):
        install(shown, manifest, tmp_path / "one", "uv")
    assert shown.animated is False and screen.output() == "\n".join(lines_that_stay(manifest, "uv")) + "\n"

    # a terminal that cannot be asked anything
    class Closed(Screen):
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    screen = Closed()
    install(progress.Progress(screen), manifest, tmp_path / "two", "uv")
    assert screen.output() == "\n".join(lines_that_stay(manifest, "uv")) + "\n"

    # a status that raises: it is not asked again, and the installation goes on
    calls, lines = [], []

    def status(text):
        calls.append(text)
        raise RuntimeError("the progress line is broken")

    envs.install(ROOT, manifest, str(tmp_path / "three"), installer="uv", echo=lines.append, status=status)
    assert calls == ["before: creating the environment"] and lines == lines_that_stay(manifest, "uv")

    # a reader of the installers' lines that fails: likewise
    monkeypatch.setattr(envs, "_doing", lambda line: 1 / 0)
    calls, lines = [], []
    envs.install(ROOT, manifest, str(tmp_path / "four"), installer="uv", echo=lines.append, status=calls.append)
    assert calls == ["before: creating the environment"] and lines == lines_that_stay(manifest, "uv")

    # and a text that is no text
    shown = progress.Progress(Screen(), environ=A_TERMINAL, columns=lambda: 80)
    shown.text(None)
    shown.tick()
    assert shown.animated is True


def test_an_interrupt_leaves_a_clean_line_and_no_installer_behind(monkeypatch, tmp_path, manifest):
    screen = Screen()
    shown = progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 80)
    started = []

    def meanwhile():
        shown.tick()
        if len(started) == 2 and len(screen.frames()) > 6:  # Ctrl-C while the second command, the installation, runs
            raise KeyboardInterrupt

    started = stand_ins(monkeypatch, manifest, meanwhile=meanwhile)
    with pytest.raises(KeyboardInterrupt), contextlib.closing(shown):
        install(shown, manifest, tmp_path / "envs", "uv")
    assert started[1].killed and not started[0].killed
    assert screen.frames()[-1].startswith("⠦ before: installing")
    assert screen.shows() == ([*lines_that_stay(manifest, "uv")[:2], ""], 0), "the animated line is wiped"
    assert screen.written[-1] == "\r" + " " * 79 + "\r", "the whole row, with the ^C a terminal shows there"


class Clock:
    """Stands in for the pause between two frames: the animation thread goes on when the test says that time has passed."""

    def __init__(self):
        self.waiting, self.passed, self.over = threading.Semaphore(0), threading.Semaphore(0), False

    def pause(self, seconds):  # what the animation thread calls instead of waiting 80 ms
        assert seconds == progress.INTERVAL
        self.waiting.release()
        self.passed.acquire()
        return self.over

    def frames(self, count=1, over=False):  # let `count` intervals pass, and wait until the thread has drawn
        self.over = over
        for _ in range(count):
            self.passed.release()
            if not over:
                assert self.waiting.acquire(timeout=10)


def test_the_animation_thread_draws_a_frame_per_interval_and_ends(tmp_path):
    screen, clock = Screen(), Clock()
    with progress.Progress(screen, environ=A_TERMINAL, columns=lambda: 80, pause=clock.pause) as shown:
        assert clock.waiting.acquire(timeout=10), "the thread is waiting for its first interval"
        clock.frames(2)
        assert screen.written == [], "nothing to say yet: nothing is drawn"
        shown.text("before: installing")
        clock.frames(3)
        assert screen.shows() == (["⠹ before: installing"], 20)
        shown.line("a line that stays")
        clock.frames(2)
        assert screen.shows() == (["a line that stays", ""], 0), "no text since that line: no frame"
        shown.text("after: installing")
        clock.frames(1)
        assert screen.shows() == (["a line that stays", "⠸ after: installing"], 19)
        clock.frames(over=True)
    assert screen.shows() == (["a line that stays", ""], 0)
    assert not animating()
    shown.close()  # closing again changes nothing
    shown.line("and one more")
    assert screen.shows() == (["a line that stays", "and one more", ""], 0)


@pytest.mark.parametrize(
    "line, wording",
    [
        # uv 0.12.23
        ("Downloading cpython-3.11.17-linux-x86_64-gnu (download) (29.4MiB)\n", "downloading Python 3.11.17 (29.4MiB)"),
        (" Downloaded cpython-3.11.17-linux-x86_64-gnu (download)\n", "Python 3.11.17 downloaded"),
        ("Resolved 21 packages in 502ms\n", "21 packages resolved"),
        ("Downloading pydantic-core (2.0MiB)\n", "downloading pydantic-core (2.0MiB)"),
        (" Downloaded pydantic-core\n", "pydantic-core downloaded"),
        ("Prepared 1 package in 136ms\n", "1 package prepared"),
        ("Installed 21 packages in 59ms\n", "21 packages installed"),
        ("\x1b[2mResolved \x1b[1m21 packages\x1b[0m \x1b[2min 39ms\x1b[0m\x1b[0m\n", "21 packages resolved"),
        # pip 23.0.1 and pip 24.0 to 26.2.1
        ("Collecting graphsense-lib==2.16.3\n", "collecting graphsense-lib 2.16.3"),
        (
            "Collecting typing-extensions==4.16.0 (from -r /x/locks/before.txt (line 680))\n",
            "collecting typing-extensions 4.16.0",
        ),
        (
            "  Downloading coincurve-21.0.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (1.6 MB)\n",
            "downloading coincurve 21.0.0 (1.6 MB)",
        ),
        ("  Downloading base58-2.1.1-py3-none-any.whl (5.6 kB)\n", "downloading base58 2.1.1 (5.6 kB)"),
        (
            "  Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)\n",
            "typing_extensions 4.16.0 from the cache",
        ),
        ("Installing collected packages: urllib3, typing-inspection, toolz\n", "3 packages"),
        ("Successfully installed annotated-types-0.8.0\n", "1 package installed"),
        # lines that say nothing about what is being worked on
        ("Using CPython 3.12.15 interpreter at: /usr/local/bin/python3\n", None),
        ("Creating virtual environment at: .envs/before\n", None),
        (" + annotated-types==0.8.0\n", None),
        ("Looking in links: /x/vendor\n", None),
        ("     ━━━━━━━━━━━━━━━━━━━━ 1.2/1.2 MB 30.8 MB/s eta 0:00:00\n", None),
        ("Downloading \x1b]0;a title\x07 (1MiB)\n", None),
        ("\n", None),
    ],
)
def test_what_a_line_of_an_installer_says_it_is_doing(line, wording):
    assert envs._doing(line) == wording
