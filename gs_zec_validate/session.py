"""A long-running worker in one GraphSense environment, processing block ranges on request (see worker.serve)."""

import collections
import json
import os
import subprocess
import threading

from .envs import WORKER, SetupError


class WorkerError(SetupError):
    """The worker could not be started, stopped answering, or ended unexpectedly."""


class Session:
    """Start `<python> -I worker.py --serve <job>` and exchange one JSON line per request with it.

    Standard error of the worker is read continuously; its last lines are kept for error messages, and every line
    is passed to `echo_stderr` when one is given (--verbose).
    """

    def __init__(self, python, job, scratch, echo_stderr=None):
        if not os.path.isfile(python):
            raise SetupError(f"interpreter not found: {python} (run the install step first)")
        scratch = os.path.abspath(scratch)
        self.job_path = os.path.join(scratch, f"job-{os.getpid()}-{id(self)}.json")
        with open(self.job_path, "w", encoding="utf-8") as handle:
            json.dump(job, handle)
        self._tail = collections.deque(maxlen=20)
        self._echo = echo_stderr
        try:
            self._process = subprocess.Popen(
                [python, "-I", WORKER, "--serve", self.job_path],
                cwd=scratch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            raise WorkerError(f"the worker could not be started: {python}: {error.strerror}") from None
        self._reader = threading.Thread(target=self._drain, name="worker stderr", daemon=True)
        self._reader.start()
        self.ready = self._receive()
        self.finished = None

    def _drain(self):
        for line in self._process.stderr or ():
            line = line.rstrip("\n")
            self._tail.append(line)
            if self._echo:
                self._echo(line)

    def _receive(self):
        assert self._process.stdout is not None
        line = self._process.stdout.readline()
        if not line:
            self._process.wait()
            self._reader.join(1.0)
            tail = "\n".join(self._tail)
            raise WorkerError(
                f"the worker ended unexpectedly (exit status {self._process.returncode})\n{tail}".rstrip()
            )
        return json.loads(line)

    def process(self, low, high):
        """Ask for one range and wait for the answer."""
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.write(json.dumps({"start": low, "end": high}) + "\n")
            stdin.flush()
        except OSError:  # the worker has ended; _receive reports why
            pass
        return self._receive()

    def close(self):
        """End the session normally and return the worker's final message."""
        stdin = self._process.stdin
        assert stdin is not None
        try:
            stdin.close()
        except OSError:
            pass
        self.finished = self._receive()
        self._process.wait(timeout=60)
        self._reader.join(5.0)
        return self.finished

    def kill(self):
        if self._process.poll() is None:
            self._process.kill()
            self._process.wait()

    @property
    def stderr_tail(self):
        return list(self._tail)

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.kill()
        try:
            os.remove(self.job_path)
        except OSError:
            pass
