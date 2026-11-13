# Pi-native EGM package

* **Author:** Mitch Fultz
* **Status:** Current

## Goal

Install this fork through `pi install` and run EGM using Pi's native skill
commands and fresh child sessions. Preserve the seven workflows, reviewer
personas, document standards, artifacts, and human design decisions.

## Non-goals

No new agent runtime, provider client, model routing, session store, automatic
implementation, or user-configuration changes. Other harness packages remain
available from the shared source.

## Package and ownership

`package.json` declares the existing `skills/` directory as Pi resources.
`/skill:egm-*` loads the selected canonical `SKILL.md`. Each skill routes Pi
to `docs/pi.md` before executing the workflow.

The main session owns orchestration, synthesis, stage transitions, and target
edits. `pi-subagents` owns child lifecycle, cancellation, saved conversations,
and completion delivery. Each Goldfish uses `delegate` with `context: "fresh"`,
reads one canonical persona and the supplied target and standards, and returns
findings without writing files. The main session inspects complete results,
then writes the reports and consolidated artifacts.

Package resources resolve from the installed skill path. User documents and
reports resolve from the project directory. Absolute paths cross the child
boundary. This separation allows a global install to review any project without
copying EGM resources there.

Pi children cannot delegate, so the main session dispatches reviewers and
bootstrap scouts directly. It applies the synthesizer's aggregation rules
without spawning an intermediate orchestrator.

## Alternatives considered

1. **Shared skills with a Pi guide and native package manifest.** One canonical
   workflow and standards tree. Pi-specific path resolution and delegation
   remain in one document read by all seven skills.
2. **A generated Pi distribution with rewritten skill bodies.** This could remove
   other harness examples, but it adds a generator, duplicated workflow text,
   and another release artifact to keep aligned.
3. **An executable extension backed by the Pi SDK.** A dedicated tool could
   launch sessions, but it would duplicate the installed delegation capability
   and introduce runtime state for an instruction-driven workflow.

Choose the first design. Native skill commands already accept arguments and
load supporting resources. A custom slash-command wrapper adds no capability.
Install `pi-subagents` separately so an existing installation does not load a
second tool registration from this package's dependencies.

## Validation boundary

Use isolated agent directories and projects for the latest official Pi release
and latest `fitchmultz/pi`. Verify installation and all seven commands through
the real resource loader. Exercise fresh delegation with actual models and
inspect native child journals for successful persona, target, and standard
reads. Record full workflow execution separately from package discovery.

Keep the main process alive across asynchronous child completion. Record any
unverified workflows and UI checkpoints rather than treating a package-loader
pass as full EGM verification.
