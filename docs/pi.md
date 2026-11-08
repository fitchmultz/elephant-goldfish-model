# Use EGM in Pi

EGM runs as a native Pi skills package. The main session is the Elephant.
Goldfish reviewers are fresh Pi sessions created by
[`pi-subagents`](https://github.com/fitchmultz/pi-subagents).
Pi owns model selection, credentials, tools, sessions, and context management.
EGM adds no agent loop, provider client, or background service.

## Install

Use the latest Pi release or the latest
[`fitchmultz/pi`](https://github.com/fitchmultz/pi) runtime.
Install the reviewer capability and this package:

```sh
pi install npm:@fitchmultz/pi-subagents
pi install git:github.com/fitchmultz/elephant-goldfish-model
```

If you already use `@fitchmultz/pi-subagents`, keep that installation. EGM does not bundle a
second copy. Start a new session, or run `/reload` in an existing session.
`pi list` lists both packages. Command completion includes all seven
`/skill:egm-*` commands.

Install the entire package, not a copy of `skills/`. Reviewers also read
`agents/` and `resources/document_standards/`.

## Run a workflow

Start Pi in the project you want to work on:

```text
/skill:egm-brainstorm Add webhook delivery to this project.
/skill:egm-draft
/skill:egm-review design_draft.md
```

Run each command when you reach that stage. Brainstorming produces
`concepts_brief.md`. Drafting produces `design_draft.md`. Review produces stage
reports and a design for your approval. These workflows write documents, not
production code.

The remaining commands are:

| Command | Purpose |
| --- | --- |
| `/skill:egm-resolve` | Resolve findings in the Elephant session. |
| `/skill:egm-review-loop` | Review a document with selected personas and standards. |
| `/skill:egm-bootstrap` | Document an existing codebase from the source. |
| `/skill:egm-drift-report` | Compare documents with the current implementation. |

Skills require explicit invocation. They do not start reviews automatically.
When one skill calls another, read the next skill's `SKILL.md` from this package
and follow it in the main session. Text mentioning `/skill:...` is not a tool
call and does not execute another command.

## Instructions for the Elephant

Read this section before following a shared EGM skill in Pi. Apply these
mappings wherever that skill uses another harness's examples.

### Resolve resources from the installed package

Pi supplies the absolute path of the invoked `SKILL.md`. Its directory is
`<package>/skills/<skill-name>`. Resolve `../../` from that directory to find
the package root.

Read personas, standards, documentation, and sibling skills from that root.
Resolve skill-local `references/` from the active skill's directory.
Do not look for an `agents/` directory in the user's project.
Resolve the target document, source reading list, and output paths from the
project working directory. Resolve links inside a target document from that
document's directory. Pass absolute resource paths to children.
Never write reports into the installed package.

| Reviewer name | Persona relative to the package root |
| --- | --- |
| `egm-design`, `design_critic` | `agents/design_critic.md` |
| `egm-codebase-details`, `codebase_details` | `agents/codebase_details.md` |
| `egm-zoom-out`, `zoom_out` | `agents/zoom_out.md` |
| `egm-comprehension`, `comprehension` | `agents/comprehension.md` |
| `egm-implementation-readiness`, `implementation_readiness` | `agents/implementation_readiness.md` |
| `egm-structural`, `structural` | `agents/structural.md` |
| `egm-style`, `style` | `agents/style.md` |
| `egm-inventive-catalyst`, `inventive_catalyst` | `agents/inventive_catalyst.md` |

Resolve standard shortnames under `resources/document_standards/` in the
package. For example, `zoom-out-standard` means
`resources/document_standards/zoom-out-standard.md`.
Custom paths still resolve from the project, as the skill specifies.

### Delegate fresh reviewers from the main session

Use the existing `delegate` and `agent_runs` tools. If they are not listed,
search for them with `tool_search` before concluding they are unavailable.
If fresh delegation is unavailable, stop Goldfish review and give the
`pi-subagents` installation command. Never substitute a review in the
Elephant's context.

Pi children cannot delegate. Use **direct dispatch** for every review:

1. Launch each requested reviewer from the main session with
   `context: "fresh"` and `cwd` set to the project directory.
2. Include the complete assignment in `prompt`: mode, absolute persona path,
   target and semantic range, designated standards, reading list,
   and expected report format.
3. Tell each reviewer to read its persona first, then the target, standards,
   and supplied context. Reviewers return their complete findings through the
   native run result. They do not write files or edit the target or source.
   Tell them not to use `notes`, `history`, `intercom`, or other memory tools
   to recover the Elephant's discussions or conclusions.
4. Inspect each completed result, paging until complete. The main session
   writes all report files. Apply `agents/synthesizer.md` there to deduplicate
   findings and write the aggregate. Its spawning step is already satisfied by
   the main session's dispatch. Do not repeat it or delegate it to a child.

For example, after resolving the placeholders to real paths:

```javascript
delegate({
	name: "zoom-out",
	context: "fresh",
	cwd: "/absolute/project",
	prompt: "Mode: REVIEW. First read /absolute/package/agents/zoom_out.md. " +
		"Review /absolute/project/design_draft.md, full file, against " +
		"/absolute/package/resources/document_standards/zoom-out-standard.md. " +
		"GoldfishContext: []. Do not write or modify any files. " +
		"Do not use notes, history, intercom, or memory tools. " +
		"Return all findings with severity, affected scope, and rationale."
});
```

Do not pass `context: "fork"` or copy the parent conversation into the
assignment. Supply the hypothesis or document being tested, not the Elephant's
preferred conclusion. Fresh context excludes parent turns, not necessarily
host-provided instructions, tools, or project guidance. This is conversation
isolation, not a filesystem sandbox or a read-only permission boundary.

Leave model and thinking overrides unset unless the user requested them.
Reviewers inherit Pi's selected model and reasoning settings.

### Respect completion and stage order

A launch receipt is not a finished review. Keep each returned run ID with its
reviewer, round, stage, and intended report path. Complete independent work while children
run. When their results are needed, end the turn and let native completion
notifications resume the main session. Do not poll `agent_runs`.

After completion, use `agent_runs` with `action: "inspect"` and read the complete
returned findings. A missing result is not a clean review. Follow the shared
skill's failed-reviewer rule and record unresolved failures as `not run`.

Run independent reviewers within a stage concurrently. In `egm-review`, finish
and collect each stage before launching the next: initial zoom out,
comprehension, critic, intermediate zoom out, then implementation readiness.
The main session writes the five aggregate stage reports. For comprehension,
give the reviewer the document alone, without a standard or `GoldfishContext`.
Preserve its complete teach-back, not just its findings.
For interactive critic and readiness stages, save the loop result as
`critic_report.md` or `implementation_readiness_report.md` before a later
resolution round overwrites `goldfish_review_report.md`.

Resume the same native main session. Runs belong to its identity, not to the
project directory or a new session forked from its history.
On resume, inspect saved runs, the current target, and existing reports before
launching replacements.
Use `agent_runs` with `action: "continue"` only to continue a saved assignment;
a completed run's continuation keeps its earlier conversation.
A new independent review round needs a new `delegate` call with fresh context.
When the user stops the workflow, stop its active children with
`agent_runs` and verify their process status. Escape stops the parent reply,
not already-launched children. A normal checkpoint pause is not cancellation.
Leave unrelated runs alone.

Keep the main Pi session alive while reviewers run. A one-shot print-mode
process can exit after its final turn and stop outstanding children. Use the
interactive session, or a persistent RPC client, for asynchronous reviews.

### Bootstrap without nested agents

For `egm-bootstrap`, the main session owns discovery, the post-order directory
walk, documentation edits, and the final subtree index.
Launch the requested read-only explorer assignments directly from the main
session. Collect a directory's scout summaries before documenting it, then pass
child-directory summaries to its parent directory's scouts.

Do not launch `egm-bootstrap-orchestrator` children that would need to spawn
their own scouts. Apply the shared skill's sequential fallback if delegation is
unavailable. Preserve its source-reading rules, existing-document edits,
directory approval checkpoints, and failure reporting.

### Use Pi tools and workspace files

Use Pi's available file-reading, search, writing, and editing tools. Tool names
from other hosts are examples, not Pi APIs. Do not assume tools such as
`spawn_agent`, `invoke_subagent`, or `RequestFeedback` exist.

Write artifacts to the working directory by default. Honor an
`EGM artifact directory: ...` instruction using the path rules in
[Harnesses](harnesses.md#artifact-directory). Point to the saved file in a short
reply instead of printing the entire document.

For human checkpoints, use `ask_question` if available and the client supports
it. Translate examples to its actual schema: one `question`, string `options`,
and `multiSelect: false`. Otherwise ask one question in chat and yield.
Do not invent approval in a headless run. An explicit user instruction can
answer a checkpoint in advance, including the skill's uninterrupted-review
mode. Keep design approval separate from implementation authorization.

For long sessions, let Pi manage context. If `pi-posthorse` is installed, keep
the current target, preset, stage, run IDs, report paths, round and re-audit
counts, settled findings, consecutive clean rounds, and unresolved decisions in
the task note. After a rollover, restore that state and inspect
live runs before resuming. Do not implement a separate memory or retry loop.

## Verification

See [Testing](https://github.com/fitchmultz/elephant-goldfish-model/blob/main/TESTING.md)
for native install and live-review probes.
Install/discovery checks, parent-conversation isolation, resource reads, and
end-to-end workflow runs are separate evidence. None establishes that a design
is correct or that reviewers lack access to instruction files.
