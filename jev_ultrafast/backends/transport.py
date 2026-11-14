"""A request framed transport for one persistent ``ego-browser nodejs`` REPL.

The CLI switches to its interactive REPL only when stdin is a terminal.  A
pseudo-terminal keeps that mode while allowing Python to write many requests
to the same Node runtime.  REPL prompts and stderr notices are deliberately
treated as noise; only the request-id frame is accepted as a response.
"""

from __future__ import annotations

import errno
import json
import os
import pty
import select
import shutil
import subprocess
import termios
import time
from threading import RLock
from typing import Any, Callable, Sequence

FRAME_PREFIX = b"__ULTRAFAST_RESULT__"
BOOTSTRAP_PREFIX = b"__ULTRAFAST_BOOTSTRAP__"


class EgoTransportError(RuntimeError):
    """Base error for process and framing failures."""


class EgoUnavailable(EgoTransportError):
    """The Ego CLI is not available or cannot start its runtime."""


class EgoTimeout(EgoTransportError, TimeoutError):
    """A request did not produce its matching frame before the deadline."""


class EgoProcessExited(EgoTransportError):
    """The persistent runtime exited before answering a request."""


class EgoMalformedResponse(EgoTransportError):
    """A matching frame did not contain valid JSON."""


class EgoRemoteError(EgoTransportError):
    """The runtime returned an application error for a request."""

    def __init__(self, message: str, name: str | None = None) -> None:
        super().__init__(message)
        # Ego's own error class name (for example ElementResolutionError) is a
        # stronger classification signal than the message text.
        self.ego_name = name or ""


# Descriptive aliases keep callers from depending on the shorter internal
# names, and make the public failure categories easy to discover.
EgoTimeoutError = EgoTimeout
EgoMalformedResponseError = EgoMalformedResponse
EgoProcessExitedError = EgoProcessExited


class EgoTransport:
    """Keep one Ego Node runtime alive and exchange one framed request at a time.

    ``spawn_fn`` is intentionally injectable for offline tests.  Production
    uses a PTY because ``ego-browser nodejs`` treats a pipe as one finite
    script instead of a REPL.  No request starts another process.
    """

    def __init__(
        self,
        command: Sequence[str] | None = None,
        *,
        timeout_ms: int = 15_000,
        graceful_timeout_ms: int = 1_500,
        terminate_timeout_ms: int = 1_000,
        use_pty: bool = True,
        popen_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.command = tuple(command or ("ego-browser", "nodejs"))
        self.timeout_ms = timeout_ms
        self.graceful_timeout_ms = graceful_timeout_ms
        self.terminate_timeout_ms = terminate_timeout_ms
        self.use_pty = use_pty
        self.popen_factory = popen_factory or subprocess.Popen
        self._custom_popen = popen_factory is not None
        self.process: Any | None = None
        self._master: int | None = None
        self._slave: int | None = None
        self._stream: Any | None = None
        self._buffer = bytearray()
        self._next_request = 0
        self._lock = RLock()
        self._started = False
        self._closed = False
        self._prompt_seen = False
        self.spawn_count = 0
        self.cleanup_status: dict[str, bool | None] = {
            "graceful_exit": None,
            "forced_terminate": None,
            "forced_kill": None,
            "orphan_process_remaining": None,
        }

    @property
    def runtime_spawn_count(self) -> int:
        return self.spawn_count

    @property
    def orphan_process_remaining(self) -> bool | None:
        return self.cleanup_status["orphan_process_remaining"]

    def start(self, dispatcher_source: str | None = None) -> None:
        """Start the REPL exactly once and optionally install a JS dispatcher."""
        with self._lock:
            if self._started:
                return
            if self._closed:
                raise EgoTransportError("Ego transport is already closed")
            if not self._custom_popen and shutil.which(self.command[0]) is None and os.path.sep not in self.command[0]:
                raise EgoUnavailable(f"Ego backend requires {self.command[0]!r} on PATH")
            try:
                if self.use_pty and hasattr(pty, "openpty"):
                    self._start_pty()
                else:
                    self._start_pipe()
            except FileNotFoundError as error:
                raise EgoUnavailable(f"Could not start {self.command[0]!r}") from error
            self.spawn_count += 1
            self._started = True
            try:
                if self.use_pty and self._master is not None:
                    self._read_until(b"repl> ", self.timeout_ms, line=False)
                    self._prompt_seen = True
                if dispatcher_source:
                    self._bootstrap(dispatcher_source)
            except BaseException:
                self.close()
                raise

    def _start_pty(self) -> None:
        master, slave = pty.openpty()
        try:
            attributes = termios.tcgetattr(slave)
            # Keep the TTY that makes ego-browser enter its REPL, but remove
            # canonical line buffering: the installed snapshot helper is
            # larger than macOS's default MAX_CANON input line.
            attributes[3] &= ~(termios.ECHO | termios.ICANON)
            attributes[6][termios.VMIN] = 1
            attributes[6][termios.VTIME] = 0
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            self.process = self.popen_factory(
                list(self.command),
                stdin=slave,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            os.close(slave)
            raise
        self._master, self._slave = master, slave
        os.close(slave)
        self._slave = None

    def _start_pipe(self) -> None:
        self.process = self.popen_factory(
            list(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
        )
        self._stream = self.process.stdout

    def _bootstrap(self, source: str) -> None:
        marker = BOOTSTRAP_PREFIX + str(self._next_request).encode() + b"__"
        expression = (
            "(async()=>{try{"
            + source
            + f";process.stdout.write({marker.decode()!r}+'ok\\n')"
            + "}catch(error){process.stdout.write("
            + f"{marker.decode()!r}+JSON.stringify({{'error':String(error)}})+'\\n')"
            + "}})()"
        )
        self._write(expression)
        line = self._read_until(marker, self.timeout_ms)
        if b"\"error\"" in line or b"error:" in line:
            raise EgoUnavailable(line.decode(errors="replace"))

    def _write(self, expression: str) -> None:
        if self.process is None or self.process.poll() is not None:
            raise EgoProcessExited("Ego runtime is not running")
        data = (expression.rstrip("\n") + "\n").encode()
        if self._master is not None:
            offset = 0
            while offset < len(data):
                try:
                    offset += os.write(self._master, data[offset:])
                except OSError as error:
                    if error.errno == errno.EINTR:
                        continue
                    raise EgoProcessExited("Could not write to Ego runtime") from error
            return
        if self.process.stdin is None:
            raise EgoProcessExited("Ego runtime stdin is unavailable")
        self.process.stdin.write(data)
        self.process.stdin.flush()

    def _read_chunk(self) -> bytes:
        if self._master is not None:
            try:
                return os.read(self._master, 65_536)
            except OSError as error:
                if error.errno in {errno.EIO, errno.EBADF}:
                    return b""
                if error.errno == errno.EINTR:
                    return b""
                raise
        stream = self._stream
        if stream is None:
            return b""
        return stream.read(65_536)

    def _read_until(self, marker: bytes, timeout_ms: int, *, line: bool = True) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            index = self._buffer.find(marker)
            if index >= 0:
                end = index + len(marker)
                newline = self._buffer.find(b"\n", end)
                if not line:
                    result = bytes(self._buffer[index:end])
                    del self._buffer[:end]
                    return result
                if newline >= 0:
                    result = bytes(self._buffer[index:newline])
                    del self._buffer[: newline + 1]
                    return result
            if self.process is not None and self.process.poll() is not None:
                tail = bytes(self._buffer[-2000:])
                raise EgoProcessExited(
                    f"Ego runtime exited with code {self.process.returncode}: "
                    f"{tail.decode(errors='replace').strip()}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EgoTimeout(f"Timed out waiting for Ego frame {marker!r}")
            if self._master is not None:
                readable, _, _ = select.select([self._master], [], [], remaining)
                if not readable:
                    raise EgoTimeout(f"Timed out waiting for Ego frame {marker!r}")
            else:
                readable, _, _ = select.select([self._stream], [], [], remaining)
                if not readable:
                    raise EgoTimeout(f"Timed out waiting for Ego frame {marker!r}")
            chunk = self._read_chunk()
            if not chunk:
                continue
            self._buffer.extend(chunk)

    def _next_id(self) -> str:
        self._next_request += 1
        return f"r{self._next_request}"

    def request(self, operation: str, payload: dict[str, Any] | None = None, *, timeout_ms: int | None = None) -> Any:
        """Send one JSON request and return only its matching JSON value."""
        with self._lock:
            if not self._started:
                self.start()
            request_id = self._next_id()
            marker = FRAME_PREFIX + request_id.encode() + b"__"
            envelope = {"request_id": request_id, "op": operation, "payload": payload or {}}
            encoded = json.dumps(envelope, separators=(",", ":"))
            expression = (
                "(async()=>{try{const r=await globalThis.__jevUltrafastDispatch("
                + encoded
                + ");process.stdout.write("
                + repr(marker.decode())
                + "+JSON.stringify({ok:true,value:r})+'\\n')}catch(error){process.stdout.write("
                + repr(marker.decode())
                + "+JSON.stringify({ok:false,error:String(error),name:error?.name||'Error'})+'\\n')}})()"
            )
            self._write(expression)
            line = self._read_until(marker, self.timeout_ms if timeout_ms is None else timeout_ms)
            raw = line[len(marker) :]
            try:
                response = json.loads(raw)
            except (TypeError, ValueError) as error:
                raise EgoMalformedResponse(f"Malformed Ego response for {request_id}") from error
            if not isinstance(response, dict) or response.get("ok") is not True:
                message = response.get("error", "Ego operation failed") if isinstance(response, dict) else response
                name = response.get("name") if isinstance(response, dict) else None
                raise EgoRemoteError(str(message), name=str(name) if name else None)
            return response.get("value")

    def close(self) -> None:
        """Gracefully leave the REPL, then terminate and kill if necessary."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self.process
            if process is None:
                self.cleanup_status.update(
                    graceful_exit=True,
                    forced_terminate=False,
                    forced_kill=False,
                    orphan_process_remaining=False,
                )
                return
            try:
                if process.poll() is None:
                    try:
                        self._write(".exit")
                    except BaseException:
                        pass
                    try:
                        process.wait(timeout=self.graceful_timeout_ms / 1000)
                        self.cleanup_status["graceful_exit"] = True
                    except (subprocess.TimeoutExpired, TimeoutError):
                        self.cleanup_status["graceful_exit"] = False
                else:
                    self.cleanup_status["graceful_exit"] = True
                if process.poll() is None:
                    process.terminate()
                    self.cleanup_status["forced_terminate"] = True
                    try:
                        process.wait(timeout=self.terminate_timeout_ms / 1000)
                    except (subprocess.TimeoutExpired, TimeoutError):
                        pass
                else:
                    self.cleanup_status["forced_terminate"] = False
                if process.poll() is None:
                    process.kill()
                    self.cleanup_status["forced_kill"] = True
                    try:
                        process.wait(timeout=self.terminate_timeout_ms / 1000)
                    except (subprocess.TimeoutExpired, TimeoutError):
                        pass
                else:
                    self.cleanup_status["forced_kill"] = False
                self.cleanup_status["orphan_process_remaining"] = process.poll() is None
            finally:
                for fd_name in ("_master", "_slave"):
                    fd = getattr(self, fd_name)
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                        setattr(self, fd_name, None)
                self._stream = None
                self.process = None

    def __enter__(self) -> "EgoTransport":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
