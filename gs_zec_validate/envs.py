"""The two GraphSense environments: creating them (`install`) and running the worker in them.

Both graphsense-lib revisions are the same Python package, so each gets a virtual environment of its own:

    <env dir>/before    graphsense-lib as pinned for "before"
    <env dir>/after     graphsense-lib as pinned for "after"

Each is built from a hash-locked requirements file (`locks/`). Only ready-made wheels are accepted; nothing
is compiled. `install` is the only part of the tool that uses the network.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .manifest import text_sha256

SIDES = ("before", "after")
DEFAULT_ENV_DIR = ".envs"
SUPPORTED_MIN, SUPPORTED_BELOW = (3, 10), (3, 14)  # graphsense-lib 2.16.x: requires-python >=3.10,<3.14
FALLBACK_PYTHON = "3.12"  # requested from uv when the running interpreter is unsupported
VENDOR_DIR = "vendor"
WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py")


class SetupError(Exception):
    """An environment is missing, cannot be built, or does not hold the pinned code."""


def supported(version_info):
    return SUPPORTED_MIN <= tuple(version_info[:2]) < SUPPORTED_BELOW


def env_python(env_dir, side):
    """Path of the interpreter inside an environment (Windows keeps it under Scripts)."""
    if os.name == "nt":
        return os.path.join(env_dir, side, "Scripts", "python.exe")
    return os.path.join(env_dir, side, "bin", "python")


def _run(command, echo, watch):
    """Run an installer command. `watch` is given every line the command writes, as it arrives."""
    echo("  $ " + " ".join(command))
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as error:
        raise SetupError(f"cannot run {command[0]}: {error.strerror}") from None
    lines = []
    try:
        with process:
            for line in process.stdout or ():  # "or ()" is for the type checker
                lines.append(line)
                watch(line)
    except BaseException:  # never leave the installer running, also on an interrupt
        process.kill()
        raise
    output = "".join(lines)
    if process.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-25:])
        raise SetupError(f"command failed with exit status {process.returncode}: {' '.join(command)}\n{tail}")
    return output


_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_VERSION = r"[A-Za-z0-9][A-Za-z0-9.+!_]*"
_SIZE = r"[0-9.]+ ?(?:[kKMGT]i?B|bytes)"
_WHEEL = rf"([A-Za-z0-9][A-Za-z0-9._]*)-({_VERSION})-\S+\.whl"  # a wheel's file name starts with name and version
_COLOUR = re.compile(r"\x1b\[[0-9;]*m")  # installers can colour their output even in a pipe
_DOING = [  # (installer output line, wording shown)
    (rf"Downloading cpython-([0-9][0-9a-z.]*)\S* \(download\) \(({_SIZE})\)", "downloading Python {0} ({1})"),  # uv
    (r"Downloaded cpython-([0-9][0-9a-z.]*)\S* \(download\)", "Python {0} downloaded"),  # uv
    (rf"Downloading {_WHEEL} \(({_SIZE})\)", "downloading {0} {1} ({2})"),  # pip
    (rf"Using cached {_WHEEL} \(.*", "{0} {1} from the cache"),  # pip
    (rf"Collecting ({_NAME})==({_VERSION})(?: \(from .*)?", "collecting {0} {1}"),  # pip
    (rf"Downloading ({_NAME}) \(({_SIZE})\)", "downloading {0} ({1})"),  # uv, for the larger files
    (rf"Downloaded ({_NAME})", "{0} downloaded"),  # uv
    (r"Resolved (\d+ packages?) in \S+", "{0} resolved"),  # uv
    (r"Prepared (\d+ packages?) in \S+", "{0} prepared"),  # uv
    (r"Installed (\d+ packages?) in \S+", "{0} installed"),  # uv
]


def _doing(line):
    """What a line of an installer's output says it is working on, in a few words; None for any other line."""
    line = _COLOUR.sub("", line).strip()
    for pattern, wording in _DOING:
        match = re.fullmatch(pattern, line)
        if match:
            return wording.format(*match.groups())
    match = re.fullmatch(r"(Installing collected packages:|Successfully installed) (.+)", line)  # pip
    if match:
        count = len(re.split(",? ", match[2]))
        done = " installed" if match[1] == "Successfully installed" else ""
        return f"{count} package{'' if count == 1 else 's'}{done}"
    return None


def _step(step, command, echo, doing):
    """Run one installer command. `doing` is told the step, and then every line the installer writes."""
    doing(step)
    _run(command, echo, lambda line: doing(step, line))


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def _text_sha256_file(path):
    with open(path, "rb") as handle:
        return text_sha256(handle.read())


def install(
    root, manifest, env_dir, installer="auto", python=None, transformer=False, echo=print, verbose=False, status=None
):
    """Create both environments from the lock files and confirm they hold the pinned code.

    `echo` prints the lines that stay. With `verbose` these include the interpreter and every command that is
    run. `status`, if given, is told what is being done at the moment; it is there for a progress line.
    """

    def doing(step, line=None):
        """Tell `status` the step that is running, or what a line of an installer's output adds to it."""
        nonlocal status
        try:
            what = _doing(line) if line else None
            if status and (what or line is None):
                status(f"{step} - {what}" if what else step)
        except Exception:  # noqa: BLE001  (progress is optional: an error only switches it off)
            status = None

    shown = echo if verbose else (lambda line: None)
    uv = shutil.which("uv")
    if installer == "auto":
        installer = "uv" if uv else "pip"
    if installer == "uv" and not uv:
        raise SetupError("uv was asked for but is not on PATH; install it or use --installer pip")
    if python is None:
        if supported(sys.version_info):
            python = sys.executable
        elif installer == "uv":
            python = FALLBACK_PYTHON  # uv finds or downloads it
        else:
            raise SetupError(
                f"Python {sys.version_info[0]}.{sys.version_info[1]} cannot run graphsense-lib 2.16.x (it needs >=3.10,<3.14). "
                "Install uv, which provides a suitable interpreter, or pass --python with the path of a Python 3.10 to 3.13."
            )
    echo(f"installer: {installer}" + (f"; interpreter for the environments: {python}" if verbose else ""))

    vendor = os.path.join(root, VENDOR_DIR)
    for side in SIDES:
        revision = manifest["revisions"][side]
        lock = revision["locks"]["transformer" if transformer else "default"]
        lock_path = os.path.join(root, *lock["path"].split("/"))
        if not os.path.isfile(lock_path):
            raise SetupError(f"lock file missing: {lock['path']}")
        if _text_sha256_file(lock_path) != lock["sha256"]:
            raise SetupError(f"lock file changed: {lock['path']}: its sha256 is not the one the manifest pins")
        wheel = os.path.join(vendor, revision["wheel"]["filename"])
        if os.path.isfile(wheel):
            if _sha256_file(wheel) != revision["wheel"]["sha256"]:
                raise SetupError(
                    f"wheel changed: {VENDOR_DIR}/{revision['wheel']['filename']}: its sha256 is not the one the manifest pins"
                )
            carried = f"the copy in {VENDOR_DIR}/ has that checksum"
        else:
            carried = f"no copy in {VENDOR_DIR}/"
        # No source is named: the installer chooses it (index, vendor/ or its cache).
        echo(
            f"{side}: graphsense-lib {revision['version']} (release file pinned by checksum; {carried}); "
            f"dependencies from {lock['path']}"
        )

        target = os.path.join(env_dir, side)
        if os.path.exists(target):
            if not os.path.isfile(os.path.join(target, "pyvenv.cfg")):  # only ever replace an environment
                raise SetupError(f"refusing to replace {target}: it exists and is not a virtual environment")
            shutil.rmtree(target)
        os.makedirs(env_dir, exist_ok=True)
        interpreter = env_python(env_dir, side)
        links = ["--find-links", vendor] if os.path.isdir(vendor) else []
        creating, installing = f"{side}: creating the environment", f"{side}: installing"
        if installer == "uv":
            _step(creating, [uv, "venv", "--no-project", "--python", python, target], shown, doing)
            _step(
                installing,
                [uv, "pip", "sync", "--python", interpreter, "--require-hashes", "--no-build", *links, lock_path],
                shown,
                doing,
            )
        else:
            _step(creating, [python, "-m", "venv", target], shown, doing)
            _step(
                installing,
                [
                    interpreter,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--no-deps",
                    "--require-hashes",
                    "--only-binary",
                    ":all:",
                    *links,
                    "-r",
                    lock_path,
                ],
                shown,
                doing,
            )

    for side in SIDES:
        doing(f"{side}: verifying the installed code")
        result = run_worker(
            env_python(env_dir, side),
            {
                "source_dir": None,
                "transformer": transformer,
                "partition_batch_size": 0,
                "accounting_files": sorted(manifest["revisions"][side]["files"]),
                "runs": [],
            },
        )
        problem = identity_problem(manifest["revisions"][side], result)
        if problem:
            raise SetupError(f"{side}: {problem}")
        identity = result["identity"]
        echo(
            f"{side}: graphsense-lib {identity['distribution_version']} on Python {identity['python']}, code checksum {identity['package_tree_sha256'][:16]}… verified"
        )
    echo("environments are ready")


def identity_problem(revision, result):
    """Compare what a worker imported with the pinned revision. Returns a message, or None when it is the pinned code."""
    if result["import_error"]:
        return (
            f"graphsenselib cannot be imported: {result['import_error']['type']}: {result['import_error']['message']}"
        )
    identity = result["identity"]
    if identity["distribution_version"] is None:
        return f"the imported graphsenselib does not belong to an installed graphsense-lib distribution; the manifest pins {revision['version']}"
    if identity["distribution_version"] != revision["version"]:
        return f"found graphsense-lib {identity['distribution_version']}, the manifest pins {revision['version']}"
    if identity["package_tree_sha256"] != revision["package_tree_sha256"]:
        return (
            f"the installed graphsenselib files differ from the pinned release {revision['version']} "
            f"(code checksum {identity['package_tree_sha256'][:16]}…, pinned {revision['package_tree_sha256'][:16]}…)"
        )
    for name, pinned in revision["files"].items():
        if identity["files"].get(name) != pinned:
            return f"{name} differs from the pinned release {revision['version']}"
    return None


def run_worker(python, job, timeout=600):
    """Run the worker with the given interpreter and return its result.

    `-I` isolates the interpreter: no PYTHON* variables, no user site directory, and neither the script's
    directory nor the working directory on the import path.
    """
    if not os.path.isfile(python):
        raise SetupError(f"interpreter not found: {python} (run the install step first)")
    with tempfile.TemporaryDirectory(prefix="gs-zec-validate-") as scratch:
        job_path = os.path.join(scratch, "job.json")
        result_path = os.path.join(scratch, "result.json")
        with open(job_path, "w", encoding="utf-8") as handle:
            json.dump(job, handle)
        try:
            completed = subprocess.run(
                [python, "-I", WORKER, job_path, result_path],
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise SetupError(f"the worker did not finish within {timeout} seconds: {python}") from None
        except OSError as error:
            raise SetupError(f"the worker could not be started: {python}: {error.strerror}") from None
        if completed.returncode != 0 or not os.path.isfile(result_path):
            tail = "\n".join((completed.stderr or completed.stdout).strip().splitlines()[-15:])
            raise SetupError(f"the worker failed (exit status {completed.returncode}): {python}\n{tail}")
        with open(result_path, encoding="utf-8") as handle:
            result = json.load(handle)
    result["stderr_lines"] = len(completed.stderr.splitlines())
    return result
