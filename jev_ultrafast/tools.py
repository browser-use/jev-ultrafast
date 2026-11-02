"""Host-side tools for orchestrated tasks: search, pages, files, documents, terminal.

Safety model:
- Every file path resolves inside the task workspace; escapes are rejected.
- Terminal commands follow a permission policy: read-only commands run,
  destructive patterns are blocked outright, everything else needs explicit
  user approval (the sidebar asks).
- Network tools (search, read, download) are size-capped and text-only.
"""

import html
import json
import os
import re
import shlex
import subprocess
import time
import urllib.parse
from pathlib import Path

from .redact import redact

MAX_TOOL_OUTPUT = 8000
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 120
AUDIT_LOG = Path(os.environ.get("JEV_AUDIT_LOG", "artifacts/audit.jsonl"))

READ_ONLY_COMMANDS = (
    "pwd", "ls", "cat", "head", "tail", "wc", "file", "echo", "grep", "find",
    "git status", "git log", "git diff", "git show", "git branch", "git remote",
    "git rev-parse", "python --version", "python3 --version", "node --version",
    "npm --version", "npm ls", "pip list", "pip show", "uname", "whoami",
)
DANGEROUS_PATTERNS = (
    "sudo", "rm -rf", "rm -fr", "mkfs", "shutdown", "reboot", "dd if=",
    "chmod -R 777 /", "> /dev/sd", "curl ", "wget ", "powershell", "kill -9 1",
    "init 0", "init 6", "mkfs.ext",
)
# exact-name commands that must not run as substrings of filenames ("halt" etc.)
DANGEROUS_EXACT = {"halt", "poweroff", "reboot"}


class ToolError(ValueError):
    """A tool rejected the request; the message is safe to show the model."""


def _touches_outside_path(command):
    """True when any argument references a path outside the task workspace.

    Read-only commands are auto-allowed ONLY inside the workspace; ~, absolute
    paths, parent escapes and environment expansions always require approval
    (otherwise `cat ~/.ssh/id_rsa` would run unattended).
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return True  # unparseable quoting: be safe, ask
    for part in parts[1:]:
        if not part or part.startswith("-"):
            continue  # flags and the command word itself
        if (
            part.startswith(("~", "/", "$"))
            or ".." in part
            or re.match(r"^[A-Za-z]:[\\/]", part)
            or part.startswith("%")
        ):
            return True
    return False


def classify_command(command):
    """'allow' for read-only commands, 'deny' for destructive ones, else 'approve'."""
    stripped = command.strip()
    if not stripped:
        return "deny"
    lowered = stripped.lower()
    if any(pattern in lowered for pattern in DANGEROUS_PATTERNS):
        return "deny"
    if lowered.split()[:1] and lowered.split()[0] in DANGEROUS_EXACT:
        return "deny"
    compounds = ("&&", ";", "|", "\n", ">", ">>", "$(", "`", "<(")
    if any(marker in stripped for marker in compounds):
        return "approve"  # compound or redirecting commands are never auto-allowed
    for allowed in READ_ONLY_COMMANDS:
        if lowered == allowed or lowered.startswith(allowed + " "):
            if _touches_outside_path(stripped):
                return "approve"  # read-only, but reaching outside the workspace: ask first
            return "allow"
    return "approve"


def _subprocess_limits():
    """Bound approved commands: CPU seconds, address space, and file writes.

    POSIX only (preexec_fn is not available on Windows, where the wall-clock
    timeout plus the approval gate remain the guards). setrlimit only —
    allocation-free, the pattern subprocess documents for preexec_fn.
    """
    import resource

    cpu = COMMAND_TIMEOUT_SECONDS
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 ** 3, 2 * 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 ** 2, 512 * 1024 ** 2))


class ToolBox:
    """One workspace-scoped set of tools for a single orchestrated task."""

    def __init__(self, workspace, request_approval=None, browser_runner=None, audit_path=None, trace_id=None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.request_approval = request_approval or (lambda _command: False)
        self.browser_runner = browser_runner
        self.audit_path = Path(audit_path) if audit_path else AUDIT_LOG
        self.trace_id = trace_id
        self.registry = {
            "web_search": (self.web_search, "query, max_results=5 → titled URLs and snippets from the web"),
            "read_page": (self.read_page, "url → the page's readable text (truncated)"),
            "download_file": (self.download_file, "url, filename → saves the file into the workspace"),
            "list_files": (self.list_files, "path='.' → files in the workspace"),
            "read_file": (self.read_file, "path → the file's text content (truncated)"),
            "write_file": (self.write_file, "path, content → writes a text file in the workspace"),
            "parse_document": (self.parse_document, "filename → extracted text from a workspace PDF/DOCX/XLSX"),
            "run_command": (self.run_command, "command → shell command in the workspace (may need approval)"),
            "browser_task": (self.browser_task, "goal, url? → drives the live Firefox tab (browser mission)"),
        }

    # ── plumbing ──────────────────────────────────────────────────────────────

    def tool_descriptions(self):
        return "\n".join(f"- {name}({signature})" for name, (_fn, signature) in sorted(self.registry.items()))

    def call(self, name, args):
        started = time.perf_counter()
        ok, error, result = False, None, None
        try:
            if name not in self.registry:
                raise ToolError(f"Unknown tool '{name}'. Available: {', '.join(sorted(self.registry))}.")
            if not isinstance(args, dict):
                raise ToolError("Tool arguments must be a JSON object.")
            function = self.registry[name][0]
            result = function(**args)
            ok = True
            return _cap(result)
        except Exception as caught:  # noqa: BLE001 - audited, then re-raised to the model loop
            error = str(caught)[:200]
            raise
        finally:
            if name != "run_command":  # run_command writes its own richer entry
                self._audit(
                    name, args, ok=ok, error=error,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    preview=redact(str(result))[:120] if result is not None else None,
                )

    def _audit(self, tool, args, **extra):
        """Append one action record; audit failures never break a tool call."""
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "trace": self.trace_id,
                "tool": tool,
                "args": redact(json.dumps(args, ensure_ascii=False, default=str))[:400],
                **extra,
            }
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _resolve(self, path):
        candidate = (self.workspace / path).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ToolError("Path escapes the task workspace.")
        return candidate

    # ── web ───────────────────────────────────────────────────────────────────

    def web_search(self, query, max_results=5):
        query = str(query).strip()
        if not query:
            raise ToolError("Empty search query.")
        max_results = max(1, min(8, int(max_results)))
        from . import model

        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        try:
            response = model.CLIENT.get(url, timeout=20, follow_redirects=True)
        except Exception as error:  # noqa: BLE001 - surfaced as a readable message
            raise ToolError(f"Search failed: {error}") from None
        if response.is_error:
            raise ToolError(f"Search returned HTTP {response.status_code}.")
        results = []
        for match in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', response.text, re.S
        ):
            href, title = match.group(1), _strip_tags(match.group(2))
            if not title:
                continue
            results.append((title, _resolve_ddg_url(href)))
            if len(results) >= max_results:
                break
        if not results:
            return "No results. Try a different query."
        return "\n".join(f"{title} — {link}" for title, link in results)

    def read_page(self, url):
        url = str(url).strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("read_page needs a full http(s) URL.")
        from . import model

        try:
            response = model.CLIENT.get(url, timeout=25, follow_redirects=True)
        except Exception as error:  # noqa: BLE001
            raise ToolError(f"Could not fetch {url}: {error}") from None
        if response.is_error:
            raise ToolError(f"HTTP {response.status_code} for {url}.")
        text = _html_to_text(response.text)
        if not text:
            raise ToolError("The page returned no readable text (maybe it requires JavaScript).")
        return f"{url}\n\n{text}"

    def download_file(self, url, filename):
        url = str(url).strip()
        filename = str(filename).strip()
        if not url.startswith(("http://", "https://")):
            raise ToolError("download_file needs a full http(s) URL.")
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            raise ToolError("filename must be a plain name like 'report.pdf' (no paths).")
        from . import model

        target = self._resolve(filename)
        try:
            with model.CLIENT.stream("GET", url, timeout=60, follow_redirects=True) as response:
                if response.is_error:
                    raise ToolError(f"HTTP {response.status_code} for {url}.")
                size = 0
                with open(target, "wb") as handle:
                    for chunk in response.iter_bytes(65536):
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise ToolError("The file exceeds the 25 MB download limit.")
                        handle.write(chunk)
        except ToolError:
            target.unlink(missing_ok=True)
            raise
        except Exception as error:  # noqa: BLE001
            target.unlink(missing_ok=True)
            raise ToolError(f"Download failed: {error}") from None
        return f"Saved {filename} ({size} bytes)."

    # ── files ─────────────────────────────────────────────────────────────────

    def list_files(self, path="."):
        base = self._resolve(path)
        if not base.exists():
            raise ToolError(f"{path} does not exist.")
        entries = sorted(
            p.relative_to(self.workspace).as_posix() for p in base.rglob("*") if p.is_file()
        )
        return "\n".join(entries) if entries else "(the workspace is empty)"

    def read_file(self, path):
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"{path} does not exist.")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{path} is not a text file; use parse_document for documents.") from None

    def write_file(self, path, content):
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        return f"Wrote {len(str(content))} characters to {path}."

    # ── documents ─────────────────────────────────────────────────────────────

    def parse_document(self, filename):
        target = self._resolve(filename)
        if not target.is_file():
            raise ToolError(f"{filename} is not in the workspace; download_file it first.")
        suffix = target.suffix.lower()
        try:
            if suffix == ".pdf":
                return _parse_pdf(target)
            if suffix == ".docx":
                return _parse_docx(target)
            if suffix == ".xlsx":
                return _parse_xlsx(target)
        except ImportError as error:
            raise ToolError(
                f"Missing library for {suffix} files ({error.name}); "
                "install with: pip install -e .[documents]"
            ) from None
        raise ToolError(f"Unsupported document type: {suffix} (supported: .pdf, .docx, .xlsx)")

    # ── terminal ──────────────────────────────────────────────────────────────

    def run_command(self, command):
        command = str(command).strip()
        if not command:
            raise ToolError("Empty command.")
        verdict = classify_command(command)
        if verdict == "deny":
            self._audit("run_command", {"command": command}, verdict=verdict, approved=False,
                        ok=False, error="blocked by the safety policy")
            raise ToolError("That command is blocked by the safety policy (destructive or system-wide).")
        if verdict == "approve" and not self.request_approval(command):
            self._audit("run_command", {"command": command}, verdict=verdict, approved=False,
                        ok=False, error="not approved by the user")
            raise ToolError("The user did not approve this command.")
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                preexec_fn=_subprocess_limits if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            self._audit("run_command", {"command": command}, verdict=verdict, approved=True,
                        ok=False, error=f"timed out after {COMMAND_TIMEOUT_SECONDS}s",
                        duration_ms=round((time.perf_counter() - started) * 1000))
            raise ToolError(f"Command timed out after {COMMAND_TIMEOUT_SECONDS}s.") from None
        output = (completed.stdout or "") + (("\n[stderr]\n" + completed.stderr) if completed.stderr else "")
        self._audit("run_command", {"command": command}, verdict=verdict, approved=True,
                    ok=completed.returncode == 0, exit=completed.returncode,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    preview=redact(output)[:120])
        return f"exit code {completed.returncode}\n{output.strip() or '(no output)'}"

    # ── browser ───────────────────────────────────────────────────────────────

    def browser_task(self, goal, url=None):
        if self.browser_runner is None:
            raise ToolError("The browser is not available in this run.")
        return self.browser_runner(str(goal), url)


# ── helpers ───────────────────────────────────────────────────────────────────


def _cap(text):
    text = str(text)
    return text if len(text) <= MAX_TOOL_OUTPUT else text[:MAX_TOOL_OUTPUT] + "\n…(truncated)"


def _strip_tags(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _resolve_ddg_url(href):
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in (parsed.hostname or "") and parsed.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parsed.query).get("uddg", [None])[0]
        if target:
            return target
    return href


def _html_to_text(page):
    page = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    page = html.unescape(page)
    return re.sub(r"\s+", " ", page).strip()[:MAX_TOOL_OUTPUT]


def _parse_pdf(path):
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for number, page in enumerate(reader.pages[:40], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"[page {number}]\n{text}")
    return "\n\n".join(pages) or "(no extractable text; the PDF may be scanned images)"


def _parse_docx(path):
    import docx

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts) or "(empty document)"


def _parse_xlsx(path):
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    for sheet in workbook.worksheets:
        parts.append(f"[sheet: {sheet.title}]")
        for row in sheet.iter_rows(max_row=200, max_col=40, values_only=True):
            cells = ["" if value is None else str(value) for value in row]
            if any(cell.strip() for cell in cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts) or "(empty workbook)"
