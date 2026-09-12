"""Subprocess helpers.

Two flavours are used throughout the app:

* :func:`run` -- blocking, for short commands (adb queries, nmcli, modprobe).
  Always called from a worker thread via :func:`submit`, never from the GUI
  thread.
* :class:`ManagedProcess` -- a QProcess wrapper for long-running children
  (scrcpy) that need live log capture and a clean shutdown.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from PySide6.QtCore import (
    QObject,
    QProcess,
    QRunnable,
    QThreadPool,
    Signal,
    Slot,
)

log = logging.getLogger(__name__)

# Commands inherit a sanitised environment: we never want a stray LD_PRELOAD or
# a locale that reformats numbers we then parse.
_BASE_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "LANG": "C",
}


@dataclass(frozen=True)
class Result:
    """Outcome of a finished command."""

    argv: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        """stdout, falling back to stderr when the command wrote nothing."""
        return self.stdout.strip() or self.stderr.strip()

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{' '.join(self.argv)} rc={self.code}>"


class CommandError(RuntimeError):
    """Raised by :func:`check` when a command fails."""

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(
            f"{' '.join(result.argv)} exited {result.code}: {result.text or '(no output)'}"
        )


def have(program: str) -> bool:
    """True when *program* is on PATH."""
    return shutil.which(program) is not None


def run(argv: Sequence[str], timeout: float = 15.0, stdin: str | None = None) -> Result:
    """Run *argv* to completion and capture its output.

    Never raises for a non-zero exit; inspect :attr:`Result.ok`. A timeout or a
    missing binary is reported as a synthetic failure so callers only have one
    error path to handle.
    """
    argv = [str(a) for a in argv]
    log.debug("run: %s", " ".join(argv))
    try:
        proc = subprocess.run(
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_BASE_ENV,
            check=False,
        )
    except FileNotFoundError:
        return Result(tuple(argv), 127, "", f"{argv[0]}: not found")
    except subprocess.TimeoutExpired:
        return Result(tuple(argv), 124, "", f"timed out after {timeout:g}s")
    except OSError as exc:
        return Result(tuple(argv), 126, "", str(exc))
    return Result(tuple(argv), proc.returncode, proc.stdout, proc.stderr)


def check(argv: Sequence[str], timeout: float = 15.0) -> str:
    """Like :func:`run` but raise :class:`CommandError` unless it succeeded."""
    result = run(argv, timeout=timeout)
    if not result.ok:
        raise CommandError(result)
    return result.stdout


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:  # pragma: no cover - thread body
        try:
            value = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - reported to the UI verbatim
            log.exception("background task failed")
            self._emit(self.signals.failed, str(exc))
        else:
            self._emit(self.signals.done, value)

    @staticmethod
    def _emit(signal: Any, payload: Any) -> None:
        """Deliver a result, unless there is no longer anyone to deliver it to.

        Shutdown waits for queued work, but only for a few seconds, and some of
        this work legitimately takes longer -- sweeping a subnet, waiting for a
        Wi-Fi network to come up. A task that finishes after Qt has torn its
        objects down would otherwise end the process with a traceback about a
        deleted signal source, which looks like a crash and is not one.
        """
        try:
            signal.emit(payload)
        except RuntimeError:
            log.debug("dropped a result: the application had already shut down")


#: Tasks currently running. QThreadPool takes ownership of the QRunnable, but
#: nothing keeps the Python object alive, so without this the _Signals QObject
#: can be collected mid-flight and the emit fails with "Signal source has been
#: deleted" -- silently losing the callback.
_INFLIGHT: set[_Task] = set()
_INFLIGHT_LOCK = threading.Lock()


def submit(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    **kwargs: Any,
) -> None:
    """Run *fn* on the global thread pool, delivering results on the GUI thread.

    Callbacks are queued connections by virtue of the emitting thread differing
    from the receiver's, so handlers may touch widgets safely.
    """
    task = _Task(fn, args, kwargs)

    def release(*_args: Any) -> None:
        with _INFLIGHT_LOCK:
            _INFLIGHT.discard(task)

    if on_done is not None:
        task.signals.done.connect(on_done)
    if on_error is not None:
        task.signals.failed.connect(on_error)
    # Connected last so the caller's handler runs before we drop the reference.
    task.signals.done.connect(release)
    task.signals.failed.connect(release)

    with _INFLIGHT_LOCK:
        _INFLIGHT.add(task)
    QThreadPool.globalInstance().start(task)


def wait_for_idle(timeout_ms: int = 5000) -> bool:
    """Block until queued background work finishes. Used on shutdown."""
    return QThreadPool.globalInstance().waitForDone(timeout_ms)


class ManagedProcess(QObject):
    """A long-lived child process with buffered output and tidy teardown."""

    started = Signal()
    stopped = Signal(int)          # exit code
    output = Signal(str)           # one line at a time, stdout+stderr merged
    failed = Signal(str)

    #: how long to wait for SIGTERM before escalating to SIGKILL
    TERM_GRACE_MS = 3000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buffer = ""
        self._log: list[str] = []
        self._stopping = False

    # -- state ---------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def log_tail(self, lines: int = 200) -> str:
        return "\n".join(self._log[-lines:])

    # -- control -------------------------------------------------------------

    def start(self, argv: Sequence[str]) -> None:
        if self.running:
            raise RuntimeError("process is already running")
        argv = [str(a) for a in argv]
        log.info("spawn: %s", " ".join(argv))

        self._buffer = ""
        self._log = [f"$ {' '.join(argv)}"]
        self._stopping = False

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._drain)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc

        proc.start(argv[0], argv[1:])
        if not proc.waitForStarted(5000):
            self._proc = None
            self.failed.emit(f"could not start {argv[0]}: {proc.errorString()}")
            return
        self.started.emit()

    def write(self, payload: bytes) -> bool:
        """Send bytes to the child's stdin. False when it is not running."""
        if not self.running or self._proc is None:
            return False
        written = self._proc.write(payload)
        return written == len(payload)

    @property
    def stopping(self) -> bool:
        """True while a stop we asked for is in progress."""
        return self._stopping

    def stop(self) -> None:
        """Ask the child to exit, escalating if it ignores us."""
        if not self.running or self._proc is None:
            return
        self._stopping = True
        # Close stdin first: a child reading a pipe (ffmpeg) blocks on the read
        # and never sees SIGTERM, so without this every stop ends in a SIGKILL
        # that then looks like a crash.
        self._proc.closeWriteChannel()
        self._proc.terminate()
        if not self._proc.waitForFinished(self.TERM_GRACE_MS):
            log.warning("child ignored SIGTERM; killing")
            self._proc.kill()
            self._proc.waitForFinished(1000)

    # -- plumbing ------------------------------------------------------------

    @Slot()
    def _drain(self) -> None:
        if self._proc is None:
            return
        chunk = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += chunk
        *complete, self._buffer = self._buffer.split("\n")
        for line in complete:
            line = line.rstrip("\r")
            self._log.append(line)
            self.output.emit(line)

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        if self._buffer:  # flush a trailing partial line
            self._log.append(self._buffer)
            self.output.emit(self._buffer)
            self._buffer = ""
        self._proc = None
        self.stopped.emit(code)

    @Slot(QProcess.ProcessError)
    def _on_error(self, err: QProcess.ProcessError) -> None:
        # A terminate() we asked for surfaces as Crashed; that is not a failure.
        if self._stopping and err == QProcess.Crashed:
            return
        if self._proc is not None:
            self.failed.emit(self._proc.errorString())
