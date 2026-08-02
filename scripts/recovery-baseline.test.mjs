import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';

const cliPath = path.resolve('scripts/recovery-baseline.mjs');

const runCli = (configPath, outputPath) => spawnSync(
  process.execPath,
  [cliPath, 'freeze', '--config', configPath, '--output', outputPath],
  { encoding: 'utf8' }
);

const fixtureConfig = (sourceRoot, candidateRoot) => ({
  schema_version: 1,
  frontend: {
    shared_root: sourceRoot,
    candidate_root: candidateRoot,
    include_paths: ['index.html'],
    excluded_paths: ['vworld.local.js']
  }
});

const git = (root, args) => {
  const result = spawnSync('git', ['-C', root, ...args], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  return result.stdout.trim();
};

const createDashboardFixture = async (fixtureRoot) => {
  const sharedRoot = path.join(fixtureRoot, 'dashboard-shared');
  const candidateRoot = path.join(fixtureRoot, 'dashboard-candidate');
  await mkdir(sharedRoot);
  git(sharedRoot, ['init', '-b', 'main']);
  git(sharedRoot, ['config', 'user.name', 'Recovery Test']);
  git(sharedRoot, ['config', 'user.email', 'recovery@example.invalid']);
  await writeFile(path.join(sharedRoot, 'baseline.txt'), 'baseline\n');
  git(sharedRoot, ['add', 'baseline.txt']);
  git(sharedRoot, ['commit', '-m', 'baseline']);
  const baselineHead = git(sharedRoot, ['rev-parse', 'HEAD']);
  git(sharedRoot, ['worktree', 'add', '-b', 'recovery', candidateRoot, baselineHead]);
  await writeFile(path.join(candidateRoot, 'baseline.txt'), 'candidate\n');
  git(candidateRoot, ['add', 'baseline.txt']);
  git(candidateRoot, ['commit', '-m', 'candidate']);
  return { sharedRoot, candidateRoot, baselineHead, currentHead: git(candidateRoot, ['rev-parse', 'HEAD']) };
};

const dashboardConfig = (fixture) => ({
  shared_root: fixture.sharedRoot,
  candidate_root: fixture.candidateRoot,
  baseline_head: fixture.baselineHead,
  expected_branch: 'recovery',
  quarantine: []
});

test('Given a shared root as candidate, when freeze runs, then it fails before evidence write', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-shared-root-'));
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await writeFile(path.join(fixtureRoot, 'index.html'), 'safe fixture\n');
    await writeFile(configPath, JSON.stringify(fixtureConfig(fixtureRoot, fixtureRoot)));

    const result = runCli(configPath, outputPath);

    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /candidate root must differ from shared root/);
    await assert.rejects(readFile(outputPath), { code: 'ENOENT' });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given a known excluded include, when freeze runs, then it fails before candidate or evidence write', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-excluded-path-'));
  const sourceRoot = path.join(fixtureRoot, 'source');
  const candidateRoot = path.join(fixtureRoot, 'candidate');
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await import('node:fs/promises').then(({ mkdir }) => mkdir(sourceRoot));
    await writeFile(path.join(sourceRoot, 'vworld.local.js'), 'fixture only\n');
    const config = fixtureConfig(sourceRoot, candidateRoot);
    config.frontend.include_paths = ['vworld.local.js'];
    await writeFile(configPath, JSON.stringify(config));

    const result = runCli(configPath, outputPath);

    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /include path is excluded/);
    await assert.rejects(readFile(outputPath), { code: 'ENOENT' });
    await assert.rejects(readFile(path.join(candidateRoot, 'vworld.local.js')), { code: 'ENOENT' });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given isolated roots, when freeze runs twice, then copy hashes match and immutable IDs stay distinct', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-happy-path-'));
  const sourceRoot = path.join(fixtureRoot, 'source');
  const candidateRoot = path.join(fixtureRoot, 'candidate');
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await import('node:fs/promises').then(({ mkdir }) => mkdir(sourceRoot));
    await writeFile(path.join(sourceRoot, 'index.html'), 'safe fixture\n');
    await writeFile(configPath, JSON.stringify(fixtureConfig(sourceRoot, candidateRoot)));

    const first = runCli(configPath, outputPath);
    assert.equal(first.status, 0, first.stderr);
    const firstEvidence = JSON.parse(await readFile(outputPath, 'utf8'));

    const second = runCli(configPath, outputPath);
    assert.equal(second.status, 0, second.stderr);
    const secondEvidence = JSON.parse(await readFile(outputPath, 'utf8'));

    assert.notEqual(firstEvidence.frontend.shared.source_id, firstEvidence.frontend.candidate.source_id);
    assert.equal(firstEvidence.frontend.shared.content_sha256, firstEvidence.frontend.candidate.content_sha256);
    assert.equal(firstEvidence.frontend.shared.source_id, secondEvidence.frontend.shared.source_id);
    assert.equal(firstEvidence.frontend.candidate.source_id, secondEvidence.frontend.candidate.source_id);
    assert.equal(firstEvidence.frontend.path_differs, true);
    assert.equal(firstEvidence.status, 'PASS');
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given malformed input, when freeze runs, then it exits nonzero without evidence', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-malformed-'));
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await writeFile(configPath, '{not-json');
    const result = runCli(configPath, outputPath);

    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /invalid config JSON/);
    await assert.rejects(readFile(outputPath), { code: 'ENOENT' });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given a stale candidate file, when freeze runs, then it refuses misleading success', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-stale-'));
  const sourceRoot = path.join(fixtureRoot, 'source');
  const candidateRoot = path.join(fixtureRoot, 'candidate');
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    const { mkdir } = await import('node:fs/promises');
    await mkdir(sourceRoot);
    await mkdir(candidateRoot);
    await writeFile(path.join(sourceRoot, 'index.html'), 'source\n');
    await writeFile(path.join(candidateRoot, 'index.html'), 'stale\n');
    await writeFile(configPath, JSON.stringify(fixtureConfig(sourceRoot, candidateRoot)));

    const result = runCli(configPath, outputPath);

    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /candidate content does not match source manifest/);
    await assert.rejects(readFile(outputPath), { code: 'ENOENT' });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given a clean descendant candidate, when freeze runs, then the immutable baseline remains pinned', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-descendant-head-'));
  const frontendSource = path.join(fixtureRoot, 'frontend-source');
  const frontendCandidate = path.join(fixtureRoot, 'frontend-candidate');
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await mkdir(frontendSource);
    await writeFile(path.join(frontendSource, 'index.html'), 'safe fixture\n');
    const dashboard = await createDashboardFixture(fixtureRoot);
    assert.notEqual(dashboard.currentHead, dashboard.baselineHead);
    const config = fixtureConfig(frontendSource, frontendCandidate);
    config.dashboard = dashboardConfig(dashboard);
    await writeFile(configPath, JSON.stringify(config));

    const result = runCli(configPath, outputPath);
    assert.equal(result.status, 0, result.stderr);
    const evidence = JSON.parse(await readFile(outputPath, 'utf8'));
    assert.equal(evidence.dashboard.baseline_head, dashboard.baselineHead);
    assert.equal(evidence.dashboard.candidate.head, dashboard.currentHead);
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});

test('Given a dirty dashboard candidate, when freeze runs, then it fails before evidence write', async () => {
  const fixtureRoot = await mkdtemp(path.join(tmpdir(), 'recovery-dirty-dashboard-'));
  const frontendSource = path.join(fixtureRoot, 'frontend-source');
  const frontendCandidate = path.join(fixtureRoot, 'frontend-candidate');
  const configPath = path.join(fixtureRoot, 'config.json');
  const outputPath = path.join(fixtureRoot, 'evidence.json');

  try {
    await mkdir(frontendSource);
    await writeFile(path.join(frontendSource, 'index.html'), 'safe fixture\n');
    const dashboard = await createDashboardFixture(fixtureRoot);
    await writeFile(path.join(dashboard.candidateRoot, 'baseline.txt'), 'dirty candidate\n');
    const config = fixtureConfig(frontendSource, frontendCandidate);
    config.dashboard = dashboardConfig(dashboard);
    await writeFile(configPath, JSON.stringify(config));

    const result = runCli(configPath, outputPath);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /dashboard candidate worktree must be clean/);
    await assert.rejects(readFile(outputPath), { code: 'ENOENT' });
  } finally {
    await rm(fixtureRoot, { recursive: true, force: true });
  }
});
