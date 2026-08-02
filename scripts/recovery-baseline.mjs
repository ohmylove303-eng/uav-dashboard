import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import {
  copyFile, mkdir, readFile, readdir, realpath, rename, stat, writeFile
} from 'node:fs/promises';
import path from 'node:path';

const allowedStatuses = new Set([
  'reimplement_from_reviewed_behavior',
  'reviewed_exception_with_hash',
  'excluded'
]);

const fail = (message) => {
  throw new Error(message);
};

const sha256 = (value) => createHash('sha256').update(value).digest('hex');
const hashFile = async (filePath) => sha256(await readFile(filePath));
const normalizeRelative = (value) => value.split(path.sep).join('/').replace(/^\.\//, '');

const git = (root, args) => execFileSync('git', ['-C', root, ...args], {
  encoding: 'utf8',
  maxBuffer: 16 * 1024 * 1024
});

const fileExists = async (filePath) => {
  try {
    return (await stat(filePath)).isFile();
  } catch (error) {
    if (error?.code === 'ENOENT') return false;
    throw error;
  }
};

const walkFiles = async (root, relative = '') => {
  const directory = path.join(root, relative);
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const child = normalizeRelative(path.join(relative, entry.name));
    if (entry.isSymbolicLink()) fail(`symbolic links are not allowed in manifest: ${child}`);
    if (entry.isDirectory()) files.push(...await walkFiles(root, child));
    if (entry.isFile()) files.push(child);
  }
  return files.sort();
};

const isExcluded = (relativePath, exclusions) => exclusions.some(
  (excluded) => relativePath === excluded || relativePath.startsWith(`${excluded}/`)
);

const collectIncludedFiles = async (root, includePaths, exclusions) => {
  const files = [];
  for (const rawInclude of includePaths) {
    const include = normalizeRelative(rawInclude);
    if (!include || include === '..' || path.isAbsolute(include) || include.startsWith('../')) {
      fail(`invalid include path: ${rawInclude}`);
    }
    if (isExcluded(include, exclusions)) fail(`include path is excluded: ${include}`);
    const absolute = path.join(root, include);
    let metadata;
    try {
      metadata = await stat(absolute);
    } catch (error) {
      if (error?.code === 'ENOENT') fail(`included path is missing: ${include}`);
      throw error;
    }
    if (metadata.isFile()) files.push(include);
    if (metadata.isDirectory()) {
      const children = await walkFiles(root, include);
      files.push(...children.filter((child) => !isExcluded(child, exclusions)));
    }
  }
  return [...new Set(files)].sort();
};

const manifestFor = async (root, files) => {
  const manifest = [];
  for (const relativePath of files) {
    manifest.push({ path: relativePath, sha256: await hashFile(path.join(root, relativePath)) });
  }
  const contentSha256 = sha256(manifest.map(({ path: filePath, sha256: digest }) => `${filePath}\0${digest}\n`).join(''));
  return {
    root,
    source_id: sha256(`${root}\0${contentSha256}`),
    content_sha256: contentSha256,
    file_count: manifest.length,
    files: manifest
  };
};

const validateFrontend = async (frontend) => {
  if (!frontend || !Array.isArray(frontend.include_paths) || !Array.isArray(frontend.excluded_paths)) {
    fail('frontend include_paths and excluded_paths arrays are required');
  }
  const sharedRoot = await realpath(frontend.shared_root);
  let candidateRoot = path.resolve(frontend.candidate_root);
  try {
    candidateRoot = await realpath(candidateRoot);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
    candidateRoot = path.join(await realpath(path.dirname(candidateRoot)), path.basename(candidateRoot));
  }
  if (sharedRoot === candidateRoot) fail('candidate root must differ from shared root');
  const exclusions = frontend.excluded_paths.map(normalizeRelative);
  const files = await collectIncludedFiles(sharedRoot, frontend.include_paths, exclusions);
  if (files.length === 0) fail('frontend source manifest is empty');
  const shared = await manifestFor(sharedRoot, files);

  let candidateExists = true;
  try {
    await stat(candidateRoot);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
    candidateExists = false;
  }
  if (!candidateExists) {
    await mkdir(candidateRoot, { recursive: false });
    for (const relativePath of files) {
      const destination = path.join(candidateRoot, relativePath);
      await mkdir(path.dirname(destination), { recursive: true });
      await copyFile(path.join(sharedRoot, relativePath), destination);
    }
  }

  const candidateFiles = await walkFiles(candidateRoot);
  if (JSON.stringify(candidateFiles) !== JSON.stringify(files)) {
    fail('candidate content does not match source manifest: file list differs');
  }
  const candidate = await manifestFor(candidateRoot, files);
  if (candidate.content_sha256 !== shared.content_sha256) {
    fail('candidate content does not match source manifest: SHA-256 differs');
  }
  return { path_differs: true, shared, candidate, exclusions };
};

const dashboardFileMatrix = async (dashboard) => {
  const tracked = git(dashboard.candidate_root, ['ls-files', '-z']).split('\0').filter(Boolean).sort();
  const matrix = [];
  for (const relativePath of tracked) {
    const sharedPath = path.join(dashboard.shared_root, relativePath);
    const candidatePath = path.join(dashboard.candidate_root, relativePath);
    const sharedExists = await fileExists(sharedPath);
    const candidateExists = await fileExists(candidatePath);
    matrix.push({
      path: relativePath,
      shared_sha256: sharedExists ? await hashFile(sharedPath) : null,
      candidate_sha256: candidateExists ? await hashFile(candidatePath) : null,
      matches: sharedExists && candidateExists
        ? await hashFile(sharedPath) === await hashFile(candidatePath)
        : false
    });
  }
  return matrix;
};

const captureDashboard = async (dashboard, preEdit) => {
  const sharedRoot = await realpath(dashboard.shared_root);
  const candidateRoot = await realpath(dashboard.candidate_root);
  if (sharedRoot === candidateRoot) fail('dashboard candidate root must differ from shared root');
  const head = git(candidateRoot, ['rev-parse', 'HEAD']).trim();
  const branch = git(candidateRoot, ['branch', '--show-current']).trim();
  const candidateStatus = git(candidateRoot, ['status', '--short']);
  if (candidateStatus) fail('dashboard candidate worktree must be clean');
  try {
    git(candidateRoot, ['merge-base', '--is-ancestor', dashboard.baseline_head, head]);
  } catch {
    fail(`dashboard candidate HEAD does not descend from baseline: ${head}`);
  }
  if (branch !== dashboard.expected_branch) fail(`dashboard candidate branch mismatch: ${branch}`);
  const quarantine = [];
  for (const item of dashboard.quarantine) {
    if (!allowedStatuses.has(item.status)) fail(`invalid quarantine status for ${item.path}`);
    const sourcePath = path.join(sharedRoot, item.path);
    const exists = await fileExists(sourcePath);
    quarantine.push({
      ...item,
      source_exists: exists,
      source_sha256: exists && item.status === 'reviewed_exception_with_hash'
        ? await hashFile(sourcePath)
        : null
    });
  }
  const matrix = await dashboardFileMatrix({ ...dashboard, shared_root: sharedRoot, candidate_root: candidateRoot });
  const digest = (key) => sha256(matrix.map((item) => `${item.path}\0${item[key] ?? 'missing'}\n`).join(''));
  return {
    path_differs: true,
    baseline_head: dashboard.baseline_head,
    baseline_is_ancestor: true,
    expected_branch: dashboard.expected_branch,
    clean_before_task_1: preEdit?.dashboard_candidate?.clean_before_task_1 === true,
    pre_edit_status_short: preEdit?.dashboard_candidate?.status_short ?? null,
    shared: {
      root: sharedRoot,
      status_short: git(sharedRoot, ['status', '--short']),
      refs: git(sharedRoot, ['show-ref']).trim().split('\n').filter(Boolean),
      source_id: sha256(`${sharedRoot}\0${digest('shared_sha256')}`)
    },
    candidate: {
      root: candidateRoot,
      head,
      branch,
      status_short_at_manifest: candidateStatus,
      source_id: sha256(`${candidateRoot}\0${digest('candidate_sha256')}`)
    },
    tracked_file_matrix: matrix,
    quarantine
  };
};

const secretScan = (serialized) => {
  const patterns = {
    private_key: /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
    jwt: /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/,
    openai_key: /\bsk-[A-Za-z0-9_-]{20,}\b/,
    aws_access_key: /\bAKIA[0-9A-Z]{16}\b/,
    credential_query: /[?&](?:key|token|secret)=[^&"\s]{8,}/i
  };
  const matches = Object.entries(patterns).filter(([, pattern]) => pattern.test(serialized)).map(([name]) => name);
  return { pass: matches.length === 0, matched_pattern_names: matches };
};

const readConfig = async (configPath) => {
  try {
    return JSON.parse(await readFile(configPath, 'utf8'));
  } catch (error) {
    if (error instanceof SyntaxError) fail('invalid config JSON');
    throw error;
  }
};

const run = async () => {
  const [command, configFlag, configPath, outputFlag, outputPath] = process.argv.slice(2);
  if (command !== 'freeze' || configFlag !== '--config' || outputFlag !== '--output' || !configPath || !outputPath) {
    fail('usage: recovery-baseline.mjs freeze --config <path> --output <path>');
  }
  const config = await readConfig(configPath);
  if (config.schema_version !== 1) fail('unsupported schema_version');
  let preEdit = null;
  try {
    const existing = JSON.parse(await readFile(outputPath, 'utf8'));
    if (existing.phase === 'pre_edit') preEdit = existing;
    if (existing.pre_edit) preEdit = existing.pre_edit;
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const dashboard = config.dashboard ? await captureDashboard(config.dashboard, preEdit) : undefined;
  const frontend = await validateFrontend(config.frontend);
  const evidence = {
    schema_version: 1,
    task: 'Todo 1 - freeze both shared projects and create a recoverable baseline matrix',
    status: 'PASS',
    captured_at: new Date().toISOString(),
    pre_edit: preEdit,
    frontend,
    dashboard,
    endpoint_semantic_owners: config.endpoint_semantic_owners ?? [],
    deployment_and_rollback_identifiers: config.deployment_and_rollback_identifiers ?? [],
    adversarial_classes: config.adversarial_classes ?? [],
    commands: config.commands ?? [],
    cleanup: config.cleanup ?? []
  };
  const provisional = `${JSON.stringify(evidence, null, 2)}\n`;
  evidence.secret_scan = secretScan(provisional);
  if (!evidence.secret_scan.pass) fail(`secret scan failed: ${evidence.secret_scan.matched_pattern_names.join(', ')}`);
  const finalJson = `${JSON.stringify(evidence, null, 2)}\n`;
  await mkdir(path.dirname(outputPath), { recursive: true });
  const temporaryPath = `${outputPath}.tmp-${process.pid}`;
  await writeFile(temporaryPath, finalJson, { mode: 0o600 });
  await rename(temporaryPath, outputPath);
  process.stdout.write(`${JSON.stringify({ status: 'PASS', output: outputPath })}\n`);
};

run().catch((error) => {
  process.stderr.write(`recovery-baseline: ${error.message}\n`);
  process.exitCode = 1;
});
