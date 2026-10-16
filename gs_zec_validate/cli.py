"""Command line of gs-zec-validate."""

import argparse
import io
import os
import sys

from . import PROG, __version__, envs, evidence, progress, validate
from . import manifest as manifest_module
from . import node as node_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_DIR_VARIABLE = "GS_ZEC_VALIDATE_ENV_DIR"

RANGE_EXIT_CODES = """\
exit status:
  0  the summary was written and every difference is explained by the Orchard and Ironwood values
  1  a difference is not explained, or the results differ from the published summary for the range
  2  the command line was wrong
  3  the comparison could not run: node, capture, environments, or a revision raised
"""

SCAN_EXIT_CODES = """\
exit status:
  0  the summary was written
  2  the command line was wrong
  3  the scan could not run: node, environments, or a revision raised
"""

CHECK_EXIT_CODES = """\
exit status:
  0  every rule held in every block of the range
  1  at least one rule failed in at least one block
  2  the command line was wrong
  3  the check could not run: node, configuration, or the stored data cannot be read
"""

EXIT_CODES = """\
exit status:
  0  every required check passed
  1  the validation ran and at least one required check failed
  2  the command line was wrong
  3  the validation could not run: manifest, evidence, environments or revisions are not as required
  4  the report could not be written
"""


def parser():
    top = argparse.ArgumentParser(
        prog=PROG,
        description="Compare the old and the corrected GraphSense Zcash accounting against packaged mainnet evidence.",
        epilog=EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    top.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    commands = top.add_subparsers(
        dest="command",
        required=True,
        metavar="{install,run,explain,code-checksum,verify-node,compare-range,scan,check}",
    )

    def env_dir_option(sub):
        sub.add_argument(
            "--env-dir",
            default=os.environ.get(ENV_DIR_VARIABLE) or os.path.join(ROOT, envs.DEFAULT_ENV_DIR),
            metavar="DIR",
            help=f"where the two GraphSense environments live (default: ${ENV_DIR_VARIABLE} if set, otherwise .envs in the repository)",
        )

    def common(sub):
        env_dir_option(sub)
        sub.add_argument(
            "--with-transformer",
            action="store_true",
            help="also include GraphSense's transformer (stored rows, coinjoin flag); needs the larger dependency set",
        )

    install = commands.add_parser(
        "install", help="create the two pinned GraphSense environments (the only step that uses the network)"
    )
    common(install)
    install.add_argument(
        "--installer",
        choices=("auto", "uv", "pip"),
        default="auto",
        help="auto: uv if it is on PATH, otherwise pip (default)",
    )
    install.add_argument(
        "--python",
        metavar="PYTHON",
        help="interpreter for the environments: a path, or with uv also a version such as 3.12",
    )
    install.add_argument(
        "--verbose",
        action="store_true",
        help="also print the interpreter and every command that is run; no progress line",
    )

    run = commands.add_parser(
        "run",
        help="run the validation offline and write the report",
        epilog=EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common(run)
    run.add_argument(
        "--report-dir",
        default=os.path.join(os.getcwd(), "report"),
        metavar="DIR",
        help="where report.json and report.md are written (default: ./report)",
    )
    for side in envs.SIDES:
        group = run.add_mutually_exclusive_group()
        group.add_argument(
            f"--{side}-python",
            metavar="PYTHON",
            help=f"run '{side}' with the graphsenselib of another environment (marks the run as non-canonical unless it is the pinned code)",
        )
        group.add_argument(
            f"--{side}-src",
            metavar="DIR",
            help=f"run '{side}' with the graphsenselib package found in DIR, for example <checkout>/src",
        )

    code = commands.add_parser(
        "code-checksum",
        help="print the checksum of a graphsenselib package: a release file (.whl), or a directory that contains graphsenselib/",
    )
    code.add_argument(
        "path", help="a graphsense-lib wheel, or a directory such as <checkout>/src or an environment's site-packages"
    )

    explain = commands.add_parser("explain", help="show how the expected values of one case follow from the evidence")
    explain.add_argument("case", help="a case id such as 1687194/4, or the start of a transaction id")

    node = commands.add_parser(
        "verify-node", help="compare the packaged evidence with your own node (optional; uses the network)"
    )
    node_module.add_arguments(node)

    def block_range(sub, report_name):
        sub.add_argument("--start", type=int, required=True, metavar="HEIGHT", help="first block of the range")
        sub.add_argument("--end", type=int, required=True, metavar="HEIGHT", help="last block of the range")
        sub.add_argument(
            "--report-dir",
            metavar="DIR",
            help=f"where the results are written (default: ./report/{report_name}-<start>-<end>)",
        )
        sub.add_argument(
            "--rpc-workers",
            type=int,
            choices=(1, 2, 3, 4),
            default=2,
            help="requests sent to the node at the same time (default: 2)",
        )
        sub.add_argument(
            "--verbose",
            action="store_true",
            help="also print what GraphSense writes to standard error; no progress line",
        )

    compare = commands.add_parser(
        "compare-range",
        help="run the old and the corrected graphsense-lib over a block range of your node and summarise what changes",
        epilog=RANGE_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    env_dir_option(compare)
    block_range(compare, "compare")
    node_module.add_arguments(compare, required=False)
    source = compare.add_mutually_exclusive_group()
    source.add_argument(
        "--from-capture", metavar="DIR", help="use a capture written earlier by --keep-capture instead of a node"
    )
    source.add_argument(
        "--keep-capture", metavar="DIR", help="keep what the node answered in DIR, for running again offline"
    )
    for side in envs.SIDES:
        group = compare.add_mutually_exclusive_group()
        group.add_argument(
            f"--{side}-python", metavar="PYTHON", help=f"run '{side}' with the graphsenselib of another environment"
        )
        group.add_argument(
            f"--{side}-src", metavar="DIR", help=f"run '{side}' with the graphsenselib package found in DIR"
        )

    scan = commands.add_parser(
        "scan",
        help="list the blocks of a range whose transactions carry an Orchard or Ironwood value (what re-ingestion changes)",
        epilog=SCAN_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    env_dir_option(scan)
    block_range(scan, "scan")
    node_module.add_arguments(scan)
    for side in envs.SIDES:
        group = scan.add_mutually_exclusive_group()
        group.add_argument(
            f"--{side}-python", metavar="PYTHON", help=f"run '{side}' with the graphsenselib of another environment"
        )
        group.add_argument(
            f"--{side}-src", metavar="DIR", help=f"run '{side}' with the graphsenselib package found in DIR"
        )

    check = commands.add_parser(
        "check",
        help="check that the Zcash rows your GraphSense stored for a block range agree with your node",
        epilog=CHECK_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    check.add_argument(
        "--gs-python",
        required=True,
        metavar="PYTHON",
        help="interpreter of the environment in which you run graphsense-lib (with its ingest extra)",
    )
    check.add_argument(
        "--gs-config",
        metavar="FILE",
        help="graphsense-lib's configuration file (default: what graphsense-lib itself would read)",
    )
    check.add_argument("--gs-env", required=True, metavar="NAME", help="environment in graphsense-lib's configuration")
    check.add_argument(
        "--sink",
        action="append",
        choices=("cassandra", "delta"),
        help="what to check: the Cassandra raw keyspace, the Delta lake, or both (repeat the option); default cassandra",
    )
    check.add_argument(
        "--delta-path",
        metavar="DIR",
        help="a local Delta lake directory (default: the lake in graphsense-lib's configuration)",
    )
    block_range(check, "check")
    node_module.add_arguments(check)
    return top


def code_checksum(path):
    """Print the package-tree checksum the manifest pins, for a wheel or for a directory that contains graphsenselib/."""
    import tempfile
    import zipfile

    from . import worker

    if os.path.isfile(path):
        with tempfile.TemporaryDirectory(prefix="gs-zec-validate-") as scratch:
            try:
                with zipfile.ZipFile(path) as archive:
                    archive.extractall(
                        scratch, [name for name in archive.namelist() if name.startswith("graphsenselib/")]
                    )
            except (zipfile.BadZipFile, OSError) as error:
                print(f"{PROG}: error: {path} is not a readable wheel: {error}", file=sys.stderr)
                return validate.EXIT_USAGE
            return _print_checksum(worker, os.path.join(scratch, "graphsenselib"), path)
    return _print_checksum(worker, os.path.join(path, "graphsenselib"), path)


def _print_checksum(worker, package_dir, shown):
    if not os.path.isdir(package_dir):
        print(f"{PROG}: error: no graphsenselib package in {shown}", file=sys.stderr)
        return validate.EXIT_USAGE
    checksum, count = worker.tree_checksum(package_dir)
    print(f"{checksum}  graphsenselib ({count} files)")
    return validate.EXIT_PASS


class _Output:
    """Standard output that outlives its reader: after a broken pipe later output is dropped and the work goes on."""

    def __init__(self, stream):
        self._stream = stream
        self._dropped = False

    def write(self, text):
        if not self._dropped:
            try:
                return self._stream.write(text)
            except BrokenPipeError:
                self._drop()
        return len(text)

    def flush(self):
        if not self._dropped:
            try:
                self._stream.flush()
            except BrokenPipeError:
                self._drop()

    def _drop(self):
        self._dropped = True
        try:  # discard what the interpreter flushes at exit
            target = self._stream.fileno()
            nowhere = os.open(os.devnull, os.O_WRONLY)
            os.dup2(nowhere, target)
            os.close(nowhere)
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


def _overrides(arguments, sides):
    """--<side>-python / --<side>-src as the worker job expects them, or None when a directory has no package."""
    overrides = {}
    for side in sides:
        python = getattr(arguments, f"{side}_python", None)
        source = getattr(arguments, f"{side}_src", None)
        if python:
            overrides[side] = {"python": os.path.abspath(python)}  # not realpath: a venv's python is a link
        elif source:
            if not os.path.isdir(os.path.join(source, "graphsenselib")):
                print(
                    f"{PROG}: error: --{side}-src {source}: no graphsenselib package in that directory", file=sys.stderr
                )
                return None
            overrides[side] = {"src": os.path.abspath(source)}
    return overrides


def _range_command(arguments):
    from . import ranges

    if arguments.command == "compare-range" and not arguments.from_capture and not arguments.rpc_url:
        print(f"{PROG}: error: compare-range needs --rpc-url, or --from-capture", file=sys.stderr)
        return validate.EXIT_USAGE
    overrides = _overrides(arguments, envs.SIDES)
    if overrides is None:
        return validate.EXIT_USAGE
    with progress.Progress(sys.stdout, animate=not arguments.verbose) as shown:
        stderr = (lambda line: shown.line(f"  | {line}")) if arguments.verbose else None
        if arguments.command == "compare-range":
            return ranges.compare_range(
                ROOT, arguments, overrides, echo=shown.line, status=shown.text, echo_stderr=stderr
            )
        return ranges.scan(
            ROOT, arguments, overrides.get("after", {}), echo=shown.line, status=shown.text, echo_stderr=stderr
        )


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # unencodable characters become "?"
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")
    original = sys.stdout
    sys.stdout = _Output(original)
    try:
        return _command(argv)
    finally:  # also when argparse exits
        sys.stdout.flush()
        sys.stdout = original


def _command(argv):
    arguments = parser().parse_args(argv)
    if "://" in (getattr(arguments, "delta_path", None) or ""):
        print(
            f"{PROG}: error: --delta-path takes a local directory; a lake on S3 is read from graphsense-lib's configuration",
            file=sys.stderr,
        )
        return validate.EXIT_USAGE
    # The worker runs in a scratch directory: paths must be absolute.
    for name in ("env_dir", "report_dir", "from_capture", "keep_capture", "gs_config", "delta_path"):
        if getattr(arguments, name, None):
            setattr(arguments, name, os.path.abspath(getattr(arguments, name)))
    try:
        if arguments.command == "install":
            manifest, _ = manifest_module.load(ROOT)
            with progress.Progress(sys.stdout, animate=not arguments.verbose) as shown:
                envs.install(
                    ROOT,
                    manifest,
                    arguments.env_dir,
                    installer=arguments.installer,
                    python=arguments.python,
                    transformer=arguments.with_transformer,
                    echo=shown.line,
                    verbose=arguments.verbose,
                    status=shown.text,
                )
            return validate.EXIT_PASS
        if arguments.command == "run":
            overrides = _overrides(arguments, envs.SIDES)
            if overrides is None:
                return validate.EXIT_USAGE
            return validate.run(
                ROOT, arguments.env_dir, arguments.report_dir, overrides, transformer=arguments.with_transformer
            )
        if arguments.command == "code-checksum":
            return code_checksum(arguments.path)
        if arguments.command == "explain":
            from . import explain

            return explain.explain(ROOT, arguments.case)
        if arguments.command == "verify-node":
            from . import nodecheck

            return nodecheck.verify(ROOT, arguments)
        if arguments.command in ("compare-range", "scan"):
            return _range_command(arguments)
        if arguments.command == "check":
            from . import checks

            with progress.Progress(sys.stdout, animate=not arguments.verbose) as shown:
                stderr = (lambda line: shown.line(f"  | {line}")) if arguments.verbose else None
                return checks.run(ROOT, arguments, echo=shown.line, status=shown.text, echo_stderr=stderr)
    except validate.ReportError as error:
        print(f"{PROG}: error: {error}", file=sys.stderr)
        return validate.EXIT_REPORT
    except (manifest_module.ManifestError, evidence.IntegrityError, envs.SetupError) as error:
        print(f"{PROG}: error: {error}", file=sys.stderr)
        return validate.EXIT_SETUP
    return validate.EXIT_USAGE
