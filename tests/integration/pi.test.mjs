// Copyright 2026 Mitch Fultz.
// SPDX-License-Identifier: Apache-2.0

import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const repository = fileURLToPath(new URL('../../', import.meta.url));
const target = process.env.EGM_PI_PACKAGE_DIR;
const live = process.env.EGM_PI_LIVE === '1';
const skills = ['bootstrap', 'brainstorm', 'draft', 'drift-report', 'resolve', 'review', 'review-loop'];
const json = async (file) => JSON.parse(await readFile(file, 'utf8'));
const journal = async (file) => (await readFile(file, 'utf8')).split('\n').filter(Boolean).map((line) => JSON.parse(line));
const text = (message) => typeof message.content === 'string' ? message.content
  : message.content.filter((part) => part.type === 'text').map((part) => part.text).join('');
const launches = (events) => events.filter((event) => event.type === 'tool_execution_end'
  && event.toolName === 'delegate' && !event.isError && event.result?.details?.receipt === 'launch')
  .map((event) => event.result.details);
const completions = (events) => new Map(events.filter((event) => event.type === 'message_end'
  && event.message.customType === 'agent-completion').map((event) => [event.message.details.id, event.message.details]));

function writes(events, cwd) {
  const calls = new Map();
  const results = new Map();
  events.forEach((event, index) => {
    if (event.type === 'tool_execution_start' && event.toolName === 'write') {
      calls.set(event.toolCallId, { index, file: path.resolve(cwd, event.args.path) });
    } else if (event.type === 'tool_execution_end' && event.isError === false && calls.has(event.toolCallId)) {
      const call = calls.get(event.toolCallId);
      if (!results.has(call.file)) results.set(call.file, call.index);
    }
  });
  return results;
}

function reads(entries, cwd) {
  const calls = new Map();
  const files = new Set();
  for (const { message } of entries) {
    if (message?.role === 'assistant') {
      for (const part of message.content) {
        if (part.type === 'toolCall' && part.name === 'read') calls.set(part.id, part.arguments.path);
      }
    } else if (message?.role === 'toolResult' && message.toolName === 'read'
      && message.isError === false && message.content?.length && calls.has(message.toolCallId)) {
      files.add(path.resolve(cwd, calls.get(message.toolCallId)));
    }
  }
  return files;
}

async function reviewers(events, expected, installed, workspace, document) {
  const receipts = launches(events);
  const notices = completions(events);
  assert.equal(new Set(receipts.map((receipt) => receipt.sessionFile)).size, receipts.length);
  const covered = new Set();
  for (const receipt of receipts) {
    assert.equal(receipt.context, 'fresh');
    assert.equal(notices.get(receipt.id)?.result.status, 'completed');
    const entries = await journal(receipt.sessionFile);
    const opened = reads(entries, workspace);
    const lenses = expected.filter((name) => opened.has(path.join(installed, 'agents', `${name}.md`)));
    assert.equal(lenses.length, 1, 'Each child must represent one requested reviewer');
    covered.add(lenses[0]);
    if (document) assert(opened.has(document), 'Reviewer did not read the target');
    if (expected.length === 1 && expected[0] === 'comprehension') {
      assert.deepEqual(opened, new Set([document, path.join(installed, 'agents/comprehension.md')]));
    }
    for (const { message } of entries) {
      if (message?.role === 'assistant') {
        assert(!message.content.some((part) => part.type === 'toolCall'
          && ['write', 'edit', 'delegate', 'agent_runs'].includes(part.name)));
      }
    }
  }
  assert.deepEqual(covered, new Set(expected), 'Missing independent reviewer assignments');
}

async function runSkill(client, events, installed, skill, request) {
  const start = events.length;
  const done = Promise.withResolvers();
  const unsubscribe = client.onEvent((event) => {
    if (event.type !== 'agent_settled') return;
    try {
      assert(!event.aborted);
      const phase = events.slice(start);
      const receipts = launches(phase);
      const notices = completions(phase);
      if (receipts.some((receipt) => !notices.has(receipt.id))) return;
      const messages = phase.filter((entry) => entry.type === 'message_end');
      const user = messages.find((entry) => entry.message.role === 'user')?.message;
      assert(user && text(user).startsWith(`<skill name="${skill}" location="${installed}/skills/${skill}/SKILL.md">`),
        'Native installed skill expansion was not observed');
      const last = messages.findLast((entry) => entry.message.role === 'assistant');
      assert.equal(last?.message.stopReason, 'stop');
      const lastNotice = messages.findLastIndex((entry) => entry.message.customType === 'agent-completion');
      if (messages.indexOf(last) < lastNotice) return;
      done.resolve(phase);
    } catch (error) { done.reject(error); }
  });
  const timer = setTimeout(() => done.reject(new Error(`Timed out running ${skill}`)), 1_200_000);
  try {
    assert.equal(await client.prompt(`/skill:${skill} ${request}`), 'started');
    return await done.promise;
  } finally {
    clearTimeout(timer);
    unsubscribe();
  }
}

test('native Pi package', { skip: !target, timeout: 2_400_000 }, async (t) => {
  const { RpcClient } = await import(pathToFileURL(path.join(target, 'dist/index.js')).href);
  const manifest = await json(path.join(target, 'package.json'));
  const cli = path.join(target, manifest.bin.pi);
  const root = await mkdtemp(path.join(tmpdir(), 'egm-pi-'));
  const workspace = path.join(root, 'workspace');
  const agent = path.join(root, 'agent');
  const installed = path.join(root, 'package');
  const home = path.join(root, 'home');
  await Promise.all([workspace, agent, home].map((directory) => mkdir(directory)));
  const env = { ...process.env, HOME: home, BASH_ENV: '/dev/null' };
  for (const key of Object.keys(env)) if (key.startsWith('PI_')) env[key] = undefined;
  env.PI_CODING_AGENT_DIR = agent;
  const run = (binary, args, cwd = workspace) => execFileSync(binary, args, { cwd, env, encoding: 'utf8' });
  const events = [];
  let client;
  t.after(async () => {
    try { await client?.stop(); }
    finally {
      await Promise.all(['auth.json', 'models.json'].map((name) => rm(path.join(agent, name), { force: true })));
      await writeFile(path.join(root, 'events.json'), JSON.stringify(events));
    }
  });
  console.log(`Pi ${manifest.version}; private evidence: ${root}`);
  const packed = JSON.parse(run('npm', ['pack', '--ignore-scripts', '--json', '--pack-destination', root], repository));
  run('tar', ['-xzf', path.join(root, packed[0].filename), '-C', root]);
  run(process.execPath, [cli, 'install', installed]);
  if (live) {
    const auth = process.env.EGM_PI_AUTH_AGENT_DIR || path.join(homedir(), '.pi/agent');
    for (const name of ['auth.json', 'models.json']) {
      try { await copyFile(path.join(auth, name), path.join(agent, name)); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    run(process.execPath, [cli, 'install', 'npm:@fitchmultz/pi-subagents']);
    run(process.execPath, [cli, 'install', 'npm:pi-posthorse']);
  }
  client = new RpcClient({ cliPath: cli, cwd: workspace, env,
    model: process.env.EGM_PI_MODEL,
    args: ['--approve', '--no-context-files', '--session-dir', path.join(root, 'sessions'), ...(!live ? ['--offline'] : [])] });
  client.onEvent((event) => events.push(event));
  await client.start();

  await t.test('packed install discovers all seven native commands and resources', async () => {
    const commands = (await client.getCommands()).filter((command) => command.name.startsWith('skill:egm-'));
    assert.deepEqual(commands.map((command) => command.name).sort(), skills.map((name) => `skill:egm-${name}`).sort());
    for (const command of commands) {
      const source = command.sourceInfo;
      assert.equal(source.origin, 'package');
      assert.equal(path.resolve(source.baseDir), installed);
      assert.equal(path.resolve(source.path), path.join(installed, 'skills', command.name.slice(6), 'SKILL.md'));
    }
    for (const file of ['agents/comprehension.md', 'agents/design_critic.md', 'docs/pi.md',
      'skills/egm-draft/references/design_doc.md', 'resources/document_standards/zoom-out-standard.md']) {
      assert.deepEqual(await readFile(path.join(installed, file)), await readFile(path.join(repository, file)));
    }
  });

  await t.test('brainstorm, draft, and five review stages use fresh native sessions', { skip: !live }, async () => {
    const source = path.join(workspace, 'line_count.py');
    const original = "import pathlib\nimport sys\n\ntext = pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')\nprint(len(text.splitlines()))\n";
    await writeFile(source, original);
    const brief = path.join(workspace, 'concepts_brief.md');
    const draft = path.join(workspace, 'design_draft.md');
    const brainstorm = await runSkill(client, events, installed, 'egm-brainstorm', `Add an optional --nonblank flag to ${source}.
Without it preserve counting all UTF-8 lines; with it count only lines whose stripped text is nonempty.
Never change input files or use network/dependencies. Success prints decimal count and newline, exit0.
Missing input, read errors, and invalid UTF-8 produce stderr and exit1. Audience: local CLI users.
Author: EGM Pi workflow fixture. These are all requirements. The user preanswers readiness: Review with Goldfishes.
Run all seven brainstorm lenses in independent fresh sessions; reviewers return findings without writes.
The main writes ${brief}, then stops at handoff. Do not implement code or start other stages.`);
    const briefWrite = writes(brainstorm, workspace).get(brief);
    assert.notEqual(briefWrite, undefined, 'Main did not successfully write the brief');
    await reviewers(brainstorm.slice(0, briefWrite), ['design_critic', 'codebase_details', 'zoom_out', 'comprehension',
      'implementation_readiness', 'structural', 'inventive_catalyst'], installed, workspace);
    assert((await readFile(brief, 'utf8')).length);

    const drafting = await runSkill(client, events, installed, 'egm-draft', `Use ${brief} and ${source}. Author: EGM Pi workflow fixture.
Write ${draft} with an implementation plan and tests. For this noninteractive fixture the user preapproves
all drafting sections from recorded requirements and accepts the agent's section proposals.
Stop after saving the complete draft. Do not start review, approve the design, or implement code.`);
    assert(writes(drafting, workspace).has(draft));
    const beforeReview = await readFile(draft, 'utf8');
    assert(beforeReview.length);
    const review = await runSkill(client, events, installed, 'egm-review', `TargetFile: ${draft}
Step: uninterrupted. GoldfishContext: [${source}]. The user confirms the semantic split at the implementation-plan heading.
All setup inputs are complete; do not ask setup questions. Run all five stages in prescribed order with prescribed reviewers.
Use fresh direct native delegation; no nested driver/synthesizer. The main writes reports using native write.
Stage6 gets only its persona and target, no extra source or standard. Collect each stage before launching the next.
End turns while native children run; do not poll. Finish the consolidated report and stop.
Do not resolve findings, edit the draft, approve the design, or implement code.`);
    const stageWrites = writes(review, workspace);
    let previous = -1;
    for (const [filename, personas] of [
      ['zoom_out_report.md', ['zoom_out', 'inventive_catalyst']],
      ['comprehension_report.md', ['comprehension']],
      ['critic_report.md', ['design_critic', 'structural', 'style']],
      ['intermediate_zoom_out_report.md', ['zoom_out']],
      ['implementation_readiness_report.md', ['implementation_readiness', 'codebase_details', 'style']],
    ]) {
      const report = path.join(workspace, filename);
      const index = stageWrites.get(report);
      assert(index > previous, `Missing or out-of-order successful parent write: ${filename}`);
      await reviewers(review.slice(previous + 1, index), personas, installed, workspace, draft);
      assert((await readFile(report, 'utf8')).length);
      previous = index;
    }
    const consolidated = path.join(workspace, 'consolidated_review_report.md');
    assert(stageWrites.get(consolidated) > previous);
    assert((await readFile(consolidated, 'utf8')).length);
    assert.equal(await readFile(draft, 'utf8'), beforeReview);
    assert.equal(await readFile(source, 'utf8'), original);
  });
});
