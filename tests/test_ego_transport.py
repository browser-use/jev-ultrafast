"""Offline contracts for the persistent Ego request transport."""

from __future__ import annotations

import os
import re

import pytest

from jev_ultrafast.backends.transport import (
    EgoMalformedResponse,
    EgoProcessExited,
    EgoTimeout,
    EgoTransport,
    EgoUnavailable,
)


class _FakeStdin:
    def __init__(self, owner):
        self.owner = owner

    def write(self, data):
        self.owner.receive(data.decode())
        return len(data)

    def flush(self):
        return None


class _FakeStdout:
    def __init__(self, read_fd):
        self.read_fd = read_fd

    def fileno(self):
        return self.read_fd

    def read(self, size):
        return os.read(self.read_fd, size)


class _FakeProcess:
    def __init__(self, *, behavior="ok"):
        self.behavior = behavior
        self.returncode = None
        self.received = []
        self.read_fd, self.write_fd = os.pipe()
        self.stdout = _FakeStdout(self.read_fd)
        self.stdin = _FakeStdin(self)

    def receive(self, text):
        self.received.append(text)
        if ".exit" in text:
            return
        bootstrap = re.search(r"__ULTRAFAST_BOOTSTRAP__[A-Za-z0-9_-]+__", text)
        if bootstrap:
            if self.behavior == "bootstrap_error":
                self.emit(bootstrap.group(0) + '{"error":"bootstrap failed"}\n')
            else:
                self.emit(bootstrap.group(0) + "ok\n")
            return
        marker = re.search(r"__ULTRAFAST_RESULT__[A-Za-z0-9_-]+__", text)
        if not marker or self.behavior == "timeout":
            return
        prefix = marker.group(0)
        if self.behavior == "malformed":
            self.emit(prefix + "{broken\n")
        elif self.behavior == "unmatched":
            self.emit("__ULTRAFAST_RESULT__old__{" + '"ok":true,"value":"old"}\n')
            self.emit(prefix + '{"ok":true,"value":"current"}\nwarning from stderr\n')
        else:
            self.emit("stderr warning\n" + prefix + '{"ok":true,"value":"ok"}\nrepl> ')

    def emit(self, text):
        os.write(self.write_fd, text.encode())

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


def _factory(process):
    def create(*_args, **_kwargs):
        return process

    return create


def test_request_id_framing_ignores_prompt_and_stderr_noise():
    process = _FakeProcess(behavior="unmatched")
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=500)
    try:
        transport.start("globalThis.__jevReadState='state'")
        assert transport.request("observe", {}) == "current"
        assert transport.spawn_count == 1
        assert len(process.received) == 2  # bootstrap + one request
    finally:
        transport.close()
        process.close()


def test_malformed_response_is_rejected():
    process = _FakeProcess(behavior="malformed")
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=500)
    try:
        transport.start("globalThis.__jevReadState='state'")
        with pytest.raises(EgoMalformedResponse):
            transport.request("observe", {})
    finally:
        transport.close()
        process.close()


def test_timeout_does_not_spawn_a_second_runtime():
    process = _FakeProcess(behavior="timeout")
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=20)
    try:
        transport.start("globalThis.__jevReadState='state'")
        with pytest.raises(EgoTimeout):
            transport.request("observe", {})
        assert transport.spawn_count == 1
    finally:
        transport.close()
        process.close()


def test_process_exit_is_reported():
    process = _FakeProcess()
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=100)
    try:
        transport.start("globalThis.__jevReadState='state'")
        process.returncode = 17
        with pytest.raises(EgoProcessExited):
            transport.request("observe", {})
    finally:
        transport.close()
        process.close()


def test_bootstrap_failure_closes_started_process():
    process = _FakeProcess(behavior="bootstrap_error")
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=100)
    with pytest.raises(EgoUnavailable, match="bootstrap failed"):
        transport.start("globalThis.__jevReadState='state'")
    assert transport.cleanup_status["orphan_process_remaining"] is False
    assert transport.process is None
    process.close()


def test_close_is_three_stage_and_idempotent():
    process = _FakeProcess()
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=100)
    transport.start("globalThis.__jevReadState='state'")
    transport.close()
    transport.close()
    assert transport.cleanup_status == {
        "graceful_exit": True,
        "forced_terminate": False,
        "forced_kill": False,
        "orphan_process_remaining": False,
    }
    process.close()


def test_json_payload_is_framed_without_shell_interpretation():
    process = _FakeProcess()
    transport = EgoTransport(use_pty=False, popen_factory=_factory(process), timeout_ms=100)
    try:
        transport.start("globalThis.__jevReadState='state'")
        transport.request("act", {"text": 'quote ", newline\n, $()'})
        request = process.received[-1]
        assert '"text":"quote \\", newline\\n, $()"' in request
    finally:
        transport.close()
        process.close()
