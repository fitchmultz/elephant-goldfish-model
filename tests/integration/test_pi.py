# Copyright 2026 Mitch Fultz.
# SPDX-License-Identifier: Apache-2.0

"""Opt-in native Pi probes (official and fitchmultz Pi 1.5.0).

No-model package/discovery probe::

    EGM_PI_TEST=1 EGM_PI_BIN=/absolute/path/to/pi \\
        uv run --group test pytest tests/integration/test_pi.py

Live probes additionally install the separate pi-subagents companion and use a
real provider. Supply an existing private directory (mode 0700)
for retained evidence and temporary credentials::

    EGM_PI_LIVE=1 EGM_PI_BIN=/absolute/path/to/pi \\
        EGM_PI_TEST_DIR=/absolute/private/test-dir \\
        EGM_PI_AUTH_AGENT_DIR=/absolute/path/to/auth-agent-dir \\
        EGM_PI_MODEL=provider/model \\
        uv run --group test pytest tests/integration/test_pi.py

EGM_PI_MODEL is optional; omission uses Pi's default model selection, not the
source agent's settings. EGM_PI_SUBAGENTS optionally selects a built local
package or a pinned npm source; default: npm:@fitchmultz/pi-subagents.
Only auth.json and models.json are copied, never settings or extensions. The
copies are removed at teardown; wire logs, native sessions, packed resources,
and process outcomes remain in the reported test directory. Do not publish
these private logs. No print-mode host, global configuration edits, or mocks.

Contracts: Pi v1.5.0 docs/{packages,rpc,rpc-commands,session-format}.md and
pi-subagents scripts/{real-pi-live,real-pi-events}.mjs, src/agent/contracts.ts.
"""

import contextlib
import json
import os
import pathlib
import queue
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest


LIVE = os.environ.get("EGM_PI_LIVE") == "1"
pytestmark = pytest.mark.skipif(
    os.environ.get("EGM_PI_TEST") != "1" and not LIVE,
    reason="Set EGM_PI_TEST=1 (discovery) or EGM_PI_LIVE=1 (real provider)",
)
SKILLS = {
    "egm-bootstrap", "egm-brainstorm", "egm-draft", "egm-drift-report",
    "egm-resolve", "egm-review", "egm-review-loop",
}


def read_jsonl(path: pathlib.Path) -> list[dict]:
    # LF only: Unicode line separators inside JSON strings are not framing.
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()]


@dataclass
class InstalledPi:
    binary: pathlib.Path
    root: pathlib.Path
    package: pathlib.Path
    workspace: pathlib.Path
    env: dict[str, str] = field(repr=False)

    def run(self, label: str, args: list[str]) -> str:
        log = self.root / label
        command = [str(self.binary), *args]
        with log.with_suffix(".stdout").open("w") as stdout, (
            log.with_suffix(".stderr").open("w")
        ) as stderr:
            result = subprocess.run(
                command, cwd=self.workspace, env=self.env,
                stdout=stdout, stderr=stderr, timeout=120,
            )
        log.with_suffix(".status.json").write_text(json.dumps({
            "command": command, "returncode": result.returncode,
        }))
        assert result.returncode == 0, f"Pi failed; inspect {log}.*"
        return log.with_suffix(".stdout").read_text()


@pytest.fixture(scope="module")
def installed_pi(repo_root: pathlib.Path) -> Iterator[InstalledPi]:
    binary = pathlib.Path(os.environ.get("EGM_PI_BIN", ""))
    assert binary.is_absolute() and os.access(binary, os.X_OK), (
        "EGM_PI_BIN must be an explicit absolute executable path to the selected Pi host"
    )
    base_value = os.environ.get("EGM_PI_TEST_DIR")
    if LIVE:
        assert base_value, "Set EGM_PI_TEST_DIR for retained live-probe evidence"
    base = pathlib.Path(base_value).resolve() if base_value else None
    if base is not None:
        assert base.is_dir(), "EGM_PI_TEST_DIR must already exist"
        assert base.stat().st_uid == os.getuid()
        assert stat.S_IMODE(base.stat().st_mode) & 0o077 == 0, (
            "EGM_PI_TEST_DIR must be private (chmod 700)"
        )
    root = pathlib.Path(tempfile.mkdtemp(prefix="egm-pi-", dir=base)).resolve()
    assert not root.is_relative_to(repo_root.resolve()), "Test workspace must be outside the repo"
    workspace = root / "workspace"
    workspace.mkdir()
    agent = root / "agent"
    agent.mkdir(mode=0o700)
    home = root / "home"
    home.mkdir(mode=0o700)
    # Do not inherit the invoking Pi's session, package override, broker identity,
    # or resource configuration. HOME also isolates ~/.agents skill discovery.
    env = {key: value for key, value in os.environ.items() if not key.startswith("PI_")}
    env.update({"HOME": str(home), "PI_CODING_AGENT_DIR": str(agent)})
    host = InstalledPi(binary, root, root / "package", workspace, env)
    credentials = [agent / name for name in ("auth.json", "models.json")]
    try:
        host.run("version", ["--version"])
        with (root / "pack.stderr").open("w") as stderr:
            packed = subprocess.run(
                ["npm", "pack", "--ignore-scripts", "--json", "--pack-destination", str(root)],
                cwd=repo_root, env={**env, "npm_config_cache": str(root / "npm-cache")},
                capture_output=False, stdout=subprocess.PIPE, stderr=stderr,
                text=True, timeout=120,
            )
        (root / "pack.stdout").write_text(packed.stdout)
        (root / "pack.status.json").write_text(json.dumps({"returncode": packed.returncode}))
        assert packed.returncode == 0, f"npm pack failed; inspect {root}/pack.*"
        tarball = root / json.loads(packed.stdout)[0]["filename"]
        with tarfile.open(tarball) as archive:
            members = archive.getmembers()
            assert all(
                (member.isfile() or member.isdir())
                and (root / member.name).resolve().is_relative_to(host.package)
                for member in members
            ), "Packed resources must be ordinary files within package/"
            archive.extractall(root, members=members)
        host.run("install-egm", ["install", str(host.package)])
        if LIVE:
            source = pathlib.Path(os.environ.get("EGM_PI_AUTH_AGENT_DIR", ""))
            assert source.is_absolute() and source.is_dir(), (
                "Set EGM_PI_AUTH_AGENT_DIR explicitly; the user's default agent dir is not assumed"
            )
            for destination in credentials:
                original = source / destination.name
                if original.is_file():
                    shutil.copyfile(original, destination)
                    destination.chmod(0o600)
            host.run("install-subagents", [
                "install", os.environ.get("EGM_PI_SUBAGENTS", "npm:@fitchmultz/pi-subagents"),
            ])
        print(f"Pi integration evidence: {root}")
        yield host
    finally:
        for credential in credentials:
            credential.unlink(missing_ok=True)


class Rpc:
    """Keep the real main alive until native child notifications have settled."""

    def __init__(self, host: InstalledPi, label: str, *, live: bool = False):
        self.host = host
        self.log = host.root / label
        self.events: list[dict] = []
        self.incoming: queue.Queue = queue.Queue()
        self.args = [
            str(host.binary), "--mode", "rpc", "--approve", "--no-context-files",
            "--session-dir", str(host.root / (label + "-sessions")),
        ]
        if live:
            model = os.environ.get("EGM_PI_MODEL")
            if model:
                self.args.extend(["--model", model])
        else:
            self.args.append("--offline")

    def __enter__(self) -> "Rpc":
        self.stderr = self.log.with_suffix(".stderr").open("wb")
        self.process = subprocess.Popen(
            self.args, cwd=self.host.workspace, env=self.host.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        return self

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            with self.log.with_suffix(".jsonl").open("wb") as wire:
                for line in self.process.stdout:
                    wire.write(line)
                    wire.flush()
                    self.incoming.put(json.loads(line))
        except Exception as error:
            self.incoming.put(error)
        finally:
            self.incoming.put(None)

    def send(self, command: str, **fields: object) -> str:
        request_id = uuid.uuid4().hex
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps({
            "id": request_id, "type": command, **fields,
        }).encode() + b"\n")
        self.process.stdin.flush()
        return request_id

    def receive(self, deadline: float) -> dict:
        try:
            record = self.incoming.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            pytest.fail(f"RPC deadline exceeded; inspect {self.log}.*")
        assert record is not None, f"RPC exited early; inspect {self.log}.*"
        assert isinstance(record, dict), f"Invalid RPC framing; inspect {self.log}.*"
        self.events.append(record)
        assert record.get("type") != "extension_error", f"Extension failed; inspect {self.log}.*"
        return record

    def command(self, command: str, **fields: object) -> dict:
        request_id = self.send(command, **fields)
        deadline = time.monotonic() + 60
        while True:
            record = self.receive(deadline)
            if record.get("type") == "response" and record.get("id") == request_id:
                assert record.get("success") is True, f"RPC rejected {command}; inspect {self.log}.*"
                return record.get("data", {})

    def prompt(self, prompt: str) -> list[dict]:
        request_id = self.send("prompt", message=prompt)
        deadline = time.monotonic() + 600
        accepted = False
        while True:
            record = self.receive(deadline)
            if record.get("type") == "response" and record.get("id") == request_id:
                assert record.get("success") is True, f"Prompt rejected; inspect {self.log}.*"
                accepted = True
            if record.get("type") != "agent_settled" or not accepted:
                continue
            assert record.get("aborted") is not True, f"Run aborted; inspect {self.log}.*"
            receipts = launch_receipts(self.events)
            notices = {
                event["message"]["details"]["id"]: event["message"]["details"]
                for event in self.events
                if event.get("type") == "message_end"
                and event.get("message", {}).get("customType") == "agent-completion"
            }
            # A main can settle immediately after a launch receipt. It must stay
            # alive: completion delivery starts a later native parent turn.
            if any(receipt["id"] not in notices for receipt in receipts):
                continue
            for receipt in receipts:
                assert notices[receipt["id"]]["result"]["status"] == "completed", self.log
            replies = [
                (index, event["message"]) for index, event in enumerate(self.events)
                if event.get("type") == "message_end"
                and event.get("message", {}).get("role") == "assistant"
            ]
            assert replies and replies[-1][1].get("stopReason") == "stop", self.log
            notice_indices = [
                index for index, event in enumerate(self.events)
                if event.get("type") == "message_end"
                and event.get("message", {}).get("customType") == "agent-completion"
            ]
            if notice_indices and replies[-1][0] < max(notice_indices):
                continue
            return self.events

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.process.stdin is not None
        with contextlib.suppress(BrokenPipeError):
            self.process.stdin.close()
        forced = False
        try:
            self.process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            forced = True
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        finally:
            self.reader.join(timeout=5)
            self.stderr.close()
            children = []
            for receipt in launch_receipts(self.events):
                directory = pathlib.Path(receipt["sessionFile"]).parent
                identity_file = directory / "actor.lock/process.json"
                identity = json.loads(identity_file.read_text()) if identity_file.exists() else None
                exit_file = directory / f"exit-{identity['token']}.json" if identity else None
                outcome = json.loads(exit_file.read_text()) if exit_file and exit_file.exists() else None
                children.append({"id": receipt["id"], "process": identity, "exit": outcome})
            self.log.with_suffix(".status.json").write_text(json.dumps({
                "command": self.args, "returncode": self.process.returncode,
                "forced_shutdown": forced, "children": children,
            }))
        if exc_type is None:
            assert not forced and self.process.returncode == 0, (
                f"RPC shutdown failed; inspect {self.log}.*"
            )
            assert all(
                child["exit"] is not None
                and child["exit"]["pid"] == child["process"]["pid"]
                for child in children
            ), f"Child exit unconfirmed; inspect {self.log}.status.json"


def launch_receipts(events: list[dict]) -> list[dict]:
    return [
        event["result"]["details"] for event in events
        if event.get("type") == "tool_execution_end"
        and event.get("toolName") == "delegate"
        and event.get("result", {}).get("details", {}).get("receipt") == "launch"
        and not event.get("isError")
    ]


def completed_children(host: InstalledPi, events: list[dict]) -> list[list[dict]]:
    receipts = launch_receipts(events)
    assert receipts, "No real delegate launch receipts"
    children = []
    for receipt in receipts:
        assert receipt["context"] == "fresh", "Child inherited parent context"
        session = pathlib.Path(receipt["sessionFile"]).resolve()
        assert session.is_relative_to(pathlib.Path(host.env["PI_CODING_AGENT_DIR"]) / "sessions/agent-runs")
        launch = json.loads((session.parent / "launch.json").read_text())
        result = json.loads((session.parent / "result.json").read_text())
        assert launch["id"] == result["id"] == receipt["id"]
        assert launch["context"] == "fresh" and result["status"] == "completed"
        child = read_jsonl(session)
        answers = [
            entry for entry in child
            if entry.get("type") == "message"
            and entry.get("message", {}).get("role") == "assistant"
        ]
        assert answers and answers[-1]["message"]["stopReason"] == "stop", session
        output = "\n".join(
            part["text"] for part in answers[-1]["message"]["content"]
            if part["type"] == "text"
        )
        assert output.strip(), "Child returned no findings"
        assert (session.parent / "output.txt").read_text() == output, session
        if result.get("terminalEntryId"):
            assert result["terminalEntryId"] == answers[-1]["id"], session
        assert not any(
            part.get("name") in {"delegate", "agent_runs", "write", "edit", "bash"}
            for answer in answers for part in answer["message"].get("content", [])
            if part.get("type") == "toolCall"
        ), "Goldfish attempted nested fanout, writes, or shell commands"
        children.append(child)
    return children


def successful_reads(entries: list[dict], cwd: pathlib.Path) -> set[pathlib.Path]:
    """Pair executed native read calls with their successful native tool results."""
    calls = {}
    reads = set()
    for entry in entries:
        message = entry.get("message", {})
        if message.get("role") == "assistant":
            for part in message.get("content", []):
                if part.get("type") == "toolCall" and part.get("name") == "read":
                    calls[part["id"]] = part["arguments"]["path"]
        elif (message.get("role") == "toolResult" and message.get("toolName") == "read"
              and message.get("isError") is False and message.get("content")):
            path = calls.get(message["toolCallId"])
            if path:
                reads.add((cwd / path).resolve())
    return reads


def successful_writes(events: list[dict], cwd: pathlib.Path) -> dict[pathlib.Path, list[int]]:
    """Return parent write starts paired with successful native results."""
    calls = {}
    writes: dict[pathlib.Path, list[int]] = {}
    for index, event in enumerate(events):
        if event.get("type") == "tool_execution_start" and event.get("toolName") == "write":
            calls[event["toolCallId"]] = (index, (cwd / event["args"]["path"]).resolve())
        elif (event.get("type") == "tool_execution_end"
              and event.get("toolCallId") in calls and event.get("isError") is False):
            start, path = calls[event["toolCallId"]]
            writes.setdefault(path, []).append(start)
    return writes


def verify_reviewer_assignments(
    host: InstalledPi, receipts: list[dict], expected_personas: set[str],
) -> dict[str, set[pathlib.Path]]:
    """Require a distinct fresh, read-only child assignment for every lens."""
    covered = set()
    sessions = set()
    reads_by_run = {}
    for receipt in receipts:
        assert receipt["context"] == "fresh"
        session = pathlib.Path(receipt["sessionFile"]).resolve()
        assert session not in sessions, "Review assignments shared a child session"
        sessions.add(session)
        result = json.loads((session.parent / "result.json").read_text())
        assert result["id"] == receipt["id"] and result["status"] == "completed"
        entries = read_jsonl(session)
        reads = successful_reads(entries, host.workspace)
        lenses = {
            path.stem for path in reads if path.parent == host.package / "agents"
        } & expected_personas
        assert len(lenses) == 1, "One child must not stand in for multiple requested reviewers"
        covered.update(lenses)
        assert not any(
            part.get("name") in {"write", "edit", "delegate", "agent_runs"}
            for entry in entries
            for part in entry.get("message", {}).get("content", [])
            if isinstance(part, dict) and part.get("type") == "toolCall"
        ), "Reviewer wrote files or attempted nested delegation"
        reads_by_run[receipt["id"]] = reads
    assert expected_personas <= covered, "Missing independent reviewer assignments"
    return reads_by_run


def test_packed_manifest_discovery(installed_pi: InstalledPi, repo_root: pathlib.Path) -> None:
    host = installed_pi
    resources = (
        [pathlib.Path("skills") / skill / "SKILL.md" for skill in sorted(SKILLS)]
        + [path.relative_to(repo_root) for path in sorted((repo_root / "agents").glob("*.md"))]
        + [path.relative_to(repo_root) for path in sorted(
            (repo_root / "resources/document_standards").glob("*.md")
        )]
        + [pathlib.Path("docs/pi.md")]
    )
    for relative in resources:
        assert (host.package / relative).read_bytes() == (repo_root / relative).read_bytes(), relative
    with Rpc(host, "discovery") as rpc:
        commands = rpc.command("get_commands")["commands"]
    found = [command for command in commands if command["name"].startswith("skill:egm-")]
    assert len(found) == len(SKILLS), "Missing or duplicate EGM skill commands"
    assert {command["name"] for command in found} == {f"skill:{skill}" for skill in SKILLS}
    for command in found:
        assert command["source"] == "skill"
        source = command["sourceInfo"]
        assert source["origin"] == "package", "Skill bypassed package discovery"
        assert pathlib.Path(source["baseDir"]).resolve() == host.package
        skill = command["name"].removeprefix("skill:")
        assert pathlib.Path(source["path"]).resolve() == host.package / "skills" / skill / "SKILL.md"
    (host.root / "resources.json").write_text(json.dumps({
        "commands": found, "bundled_files": [str(path) for path in resources],
    }, indent=2))


@pytest.mark.skipif(not LIVE, reason="Set EGM_PI_LIVE=1 to use a real provider and companion")
def test_fresh_delegation_isolation(installed_pi: InstalledPi) -> None:
    marker = "parent-only-" + uuid.uuid4().hex
    prompt = f"""Native EGM isolation probe. Parent-only marker: {marker}
Do not write or repeat the marker or pass it to another agent.
Use delegate exactly once with context: fresh. Its exact prompt is:
'Do not use tools. If a parent-only marker is visible in your conversation,
return it verbatim. Otherwise return exactly UNKNOWN.'
Delegate returns a launch receipt, not completion. End your turn after launch;
do not poll, wait in a tool, or call agent_runs. When its native completion
notification arrives, report its actual answer. Do not simulate a child.
Do not use external services, memory, filesystem reads, or intercom.
"""
    with Rpc(installed_pi, "isolation", live=True) as rpc:
        events = rpc.prompt(prompt)
        children = completed_children(installed_pi, events)
        assert len(children) == 1
        child = children[0]
        assert marker not in json.dumps(child), "Parent-only marker leaked into fresh child"
        answers = [
            entry["message"] for entry in child
            if entry.get("message", {}).get("role") == "assistant"
        ]
        text = "".join(part["text"] for part in answers[-1]["content"] if part["type"] == "text")
        assert text.strip() == "UNKNOWN", "Child failed the isolation probe"


@pytest.mark.skipif(not LIVE, reason="Set EGM_PI_LIVE=1 to use a real provider and companion")
def test_delegated_persona_target_standard_reads(installed_pi: InstalledPi) -> None:
    host = installed_pi
    target = host.workspace / "design.md"
    target.write_text("""# Offline line counter

## Feature specification
Count nonblank UTF-8 lines in one local file. Whitespace-only lines are blank.
Print a decimal count and newline. An empty file produces 0. Success exits 0.
Missing files, permission errors and invalid UTF-8 produce stderr and exit 1.
No network, directory traversal, input writes, or other features are allowed.

## Implementation plan
Create line_count.py using only the Python standard library. Accept exactly
one positional filename, read it as UTF-8, count lines where line.strip() is
nonempty, then print the count. Catch OSError and UnicodeError, print the error
to stderr, and exit 1. Add tests for blank input, mixed lines, missing input,
and invalid UTF-8. No implementation is requested by this review fixture.
""")
    persona = host.package / "agents/implementation_readiness.md"
    standard = host.package / "resources/document_standards/implementation-readiness-standard.md"
    report = host.workspace / "resource-review.md"
    prompt = f"""Bounded native EGM resource probe, not the full EGM workflow.
Read {host.package / 'docs/pi.md'} first, then call delegate exactly once with
context: fresh and the complete assignment below. Use the shared installed
persona and standard, not copied or rephrased versions. Do not review inline.
Child assignment:
'Read {persona} first with the native read tool. Mode: REVIEW.
Then read TargetFile: {target} and Target Standard: {standard} with native read.
Review the implementation plan against the standard; report concrete gaps in
at most 200 words. Remain read-only. Do not delegate, use intercom, run shell
commands, read conversation history, or use external or memory services.'
The launch receipt is not completion. End your turn after launch, do not poll
or call agent_runs, and consume the native completion notification before your
final reply. The child RETURNS findings only. After completion, use your own
write tool to save its exact returned output to {report}, then give the path.
Do not claim success unless the real child completed its reads.
"""
    with Rpc(host, "resource-reads", live=True) as rpc:
        events = rpc.prompt(prompt)
        children = completed_children(host, events)
        assert len(children) == 1
        reads = successful_reads(children[0], host.workspace)
        assert {persona, target, standard} <= reads, "Child did not successfully read all three resources"
        output_file = pathlib.Path(launch_receipts(events)[0]["sessionFile"]).parent / "output.txt"
        assert report.read_text().strip() == output_file.read_text().strip()
        assert report in successful_writes(events, host.workspace), (
            "Parent did not successfully write the returned findings"
        )
        (host.root / "live-resource-reads.json").write_text(json.dumps(sorted(map(str, reads)), indent=2))


@pytest.mark.skipif(not LIVE, reason="Set EGM_PI_LIVE=1 to use a real provider and companion")
def test_comprehension_target_only(installed_pi: InstalledPi) -> None:
    host = installed_pi
    target = host.workspace / "comprehension-design.md"
    target.write_text("""# Offline line counter

## Feature specification
Today users count nonblank lines manually in a text editor. The proposed
command accepts one local UTF-8 file and prints the number of nonblank lines.
A blank line contains only whitespace. The command never changes the file.

## Implementation plan
Create a Python standard-library command with one positional filename. Read
UTF-8 text and count lines whose stripped text is nonempty. Print the decimal
count and newline; exit 0 on success. Report file and decoding errors to stderr
and exit 1. Test empty files, mixed blank/nonblank lines, and invalid UTF-8.
""")
    persona = host.package / "agents/comprehension.md"
    report = host.workspace / "comprehension_report.md"
    prompt = f"""/skill:egm-review
TargetFile: {target}
Step: 6 (Comprehension Test only). No additional GoldfishContext.
This is a bounded noninteractive Stage 6 probe. Read the Pi integration first.
Use one real delegate with context: fresh and the installed comprehension
persona {persona}. Pass only the target and its FeatureRange/DetailedRange.
No document standard and no supplied extra project context: this stage tests
self-containment, not compliance with an implementation standard.
The child must read the persona and target using native read, then RETURN
the complete paraphrase and findings in its native output; no file writes.
Do not delegate a synthesizer, nest delegation, or perform another stage.
End your turn after the launch receipt; do not poll or call agent_runs. On
native completion, the parent writes the entire returned output to {report},
including both paraphrase headings, and stops. This fixture preapproves ending
the Stage 6 checkpoint; it does not test interactive approval behavior.
Do not edit the target or use memory or external services.
"""
    with Rpc(host, "comprehension", live=True) as rpc:
        events = rpc.prompt(prompt)
        children = completed_children(host, events)
        assert len(children) == 1
        reads = successful_reads(children[0], host.workspace)
        assert reads == {persona, target}, (
            "Comprehension must read only its persona and target, not a standard or extra context"
        )
        receipt = launch_receipts(events)[0]
        launch = json.loads(
            (pathlib.Path(receipt["sessionFile"]).parent / "launch.json").read_text()
        )
        assert "document_standards/" not in launch["prompt"], (
            "Stage 6 was supplied a document standard"
        )
        output = (pathlib.Path(receipt["sessionFile"]).parent / "output.txt").read_text()
        assert "What this proposes" in output
        assert "How the existing system works, per this document" in output
        assert report.read_text().strip() == output.strip(), (
            "Parent dropped or changed the comprehension paraphrase"
        )
        assert report in successful_writes(events, host.workspace), (
            "Parent did not successfully write the comprehension result"
        )


@pytest.mark.skipif(
    not LIVE or os.environ.get("EGM_PI_WORKFLOW") != "1",
    reason="Set EGM_PI_LIVE=1 EGM_PI_WORKFLOW=1 for the full model-backed workflow",
)
def test_brainstorm_draft_review_workflow(installed_pi: InstalledPi) -> None:
    """Protect fresh fanout, parent artifacts, and sequential review stages."""
    host = installed_pi
    host.run("install-posthorse", ["install", "npm:pi-posthorse"])
    source = host.workspace / "line_count.py"
    original = (
        "import pathlib\nimport sys\n\n"
        "text = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\n"
        "print(len(text.splitlines()))\n"
    )
    source.write_text(original)
    brief = host.workspace / "concepts_brief.md"
    draft = host.workspace / "design_draft.md"
    with Rpc(host, "workflow", live=True) as rpc:
        brainstorm = rpc.prompt(f"""/skill:egm-brainstorm
Add an optional --nonblank flag to {source}. Today it counts all lines in a
UTF-8 file. Without the flag preserve that behavior. With the flag count only
lines whose stripped text is nonempty. Preserve source input; no network or
third-party dependencies. Missing input, read errors, and invalid UTF-8 should
print stderr and exit 1; success prints the decimal count plus newline and
exits 0. Audience: local command-line users. Author: EGM Pi workflow fixture.
This fixture supplies all requirements and preanswers the readiness checkpoint:
Review with Goldfishes. Use all seven brainstorm personas, fresh direct
delegation, and real native completion. No nested orchestrator. Reviewers
return findings without writes. The main writes {brief}, then stops at the
handoff. Do not implement code, run other stages, or invent human decisions.
""")
        brainstorm_receipts = launch_receipts(brainstorm)
        expected_brainstorm = {
            "design_critic", "codebase_details", "zoom_out", "comprehension",
            "implementation_readiness", "structural", "inventive_catalyst",
        }
        verify_reviewer_assignments(host, brainstorm_receipts, expected_brainstorm)
        assert brief in successful_writes(brainstorm, host.workspace)
        assert brief.is_file() and brief.stat().st_size > 0
        rpc.prompt(f"""/skill:egm-draft
Use {brief}, the existing source {source}, and the requirements above.
Author: EGM Pi workflow fixture. Write {draft}.
For this noninteractive fixture, the user preapproves drafting each section
from the recorded requirements and accepts the agent's section proposals.
Include an implementation plan and tests. Stop after saving the complete draft.
Do not start review, approve the final design, or implement production code.
""")
        assert draft.is_file() and draft.stat().st_size > 0
        assert draft in successful_writes(rpc.events, host.workspace)
        before_review = draft.read_bytes()
        start = len(rpc.events)
        all_events = rpc.prompt(f"""/skill:egm-review
TargetFile: {draft}
Step: uninterrupted
GoldfishContext: [{source}]
Run all five stages in their prescribed order with all prescribed reviewers.
The user confirms the semantic split at the draft's implementation-plan heading.
For this fixture, setup inputs are complete. Do not pause for setup questions.
Use fresh direct native delegation; the main owns synthesis and report writes.
Use the native write tool for stage reports. Do not delegate the driver or a
synthesizer. Stage 6 receives only its persona and target, no source or standard.
Collect each stage completely before launching the next. End turns when native
child results are pending; do not poll. Finish the consolidated report and stop.
Do not resolve findings, edit the draft, approve the design, or implement code.
""")
        review = all_events[start:]
        stages = [
            ("zoom_out_report.md", {"zoom_out", "inventive_catalyst"}),
            ("comprehension_report.md", {"comprehension"}),
            ("critic_report.md", {"design_critic", "structural", "style"}),
            ("intermediate_zoom_out_report.md", {"zoom_out"}),
            ("implementation_readiness_report.md", {
                "implementation_readiness", "codebase_details", "style",
            }),
        ]
        previous_write = -1
        for filename, expected_personas in stages:
            report = host.workspace / filename
            writes = successful_writes(review, host.workspace).get(report, [])
            assert writes, f"Missing successful native parent write for {filename}"
            stage_write = writes[0]
            assert stage_write > previous_write, "Review stages ran out of order"
            receipts = launch_receipts(review[previous_write + 1:stage_write])
            reads_by_run = verify_reviewer_assignments(host, receipts, expected_personas)
            for receipt in receipts:
                reads = reads_by_run[receipt["id"]]
                assert draft in reads
                assert any(
                    event.get("type") == "message_end"
                    and event.get("message", {}).get("customType") == "agent-completion"
                    and event["message"]["details"]["id"] == receipt["id"]
                    for event in review[:stage_write]
                ), "Stage report written before its reviewer completed"
                if filename == "comprehension_report.md":
                    assert reads == {draft, host.package / "agents/comprehension.md"}
            assert report.is_file() and report.stat().st_size > 0
            previous_write = stage_write
        consolidated = host.workspace / "consolidated_review_report.md"
        assert consolidated.is_file() and consolidated.stat().st_size > 0
        assert consolidated in successful_writes(review, host.workspace)
        assert draft.read_bytes() == before_review, "Audit changed the target without approval"
        assert source.read_text() == original, "Design workflow modified production source"
        assert set(host.workspace.rglob("*.py")) == {source}, "Design workflow implemented code"
