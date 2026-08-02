import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


export class ContractViolation extends Error {
  constructor(endpoint, reason) {
    super(`${endpoint}: ${reason}`);
    this.name = 'ContractViolation';
    this.endpoint = endpoint;
    this.reason = reason;
  }
}

const valueAt = (payload, dottedPath) => {
  if (!dottedPath) return undefined;
  return dottedPath.split('.').reduce((value, key) => value?.[key], payload);
};

const assertJsonResponse = async ({ response, expectedHost, endpoint }) => {
  if (new URL(response.url).host !== expectedHost) {
    throw new ContractViolation(endpoint, 'unintended_host');
  }
  const contentType = String(response.headers.get('content-type') || '').toLowerCase();
  if (!contentType.startsWith('application/json')) {
    throw new ContractViolation(endpoint, `non_json_content_type:${contentType || 'missing'}`);
  }
  try {
    return await response.json();
  } catch (error) {
    throw new ContractViolation(endpoint, `malformed_json:${error instanceof Error ? error.name : 'unknown'}`);
  }
};

const requestJson = async ({ baseUrl, expectedHost, endpoint, method = 'GET', body }) => {
  const response = await fetch(new URL(endpoint, baseUrl), {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    redirect: 'follow',
    signal: AbortSignal.timeout(5_000),
  });
  const payload = await assertJsonResponse({ response, expectedHost, endpoint });
  return { response, payload };
};

const validateSourceChain = (endpoint, payload, sourceChainPath) => {
  const sourceChain = valueAt(payload, sourceChainPath);
  if (!Array.isArray(sourceChain) || sourceChain.length === 0 || sourceChain.some((item) => typeof item !== 'string' || !item)) {
    throw new ContractViolation(endpoint, 'invalid_source_chain');
  }
};

const validateEndpoint = ({ definition, response, payload, selectionId }) => {
  const expectedHttpStatus = definition.expected_http_status ?? 200;
  if (response.status !== expectedHttpStatus) {
    throw new ContractViolation(definition.id, `unexpected_http_status:${response.status}`);
  }
  validateSourceChain(definition.id, payload, definition.source_chain_path);
  const officialAvailable = valueAt(payload, definition.official_available_path);
  if (typeof officialAvailable !== 'boolean') {
    throw new ContractViolation(definition.id, 'official_available_not_boolean');
  }
  if (definition.authoritative_weather_required && !officialAvailable) {
    throw new ContractViolation(definition.id, 'non_authoritative_weather');
  }
  if (definition.selection_id_path && valueAt(payload, definition.selection_id_path) !== selectionId) {
    throw new ContractViolation(definition.id, 'selection_id_mismatch');
  }
  const correlationId = valueAt(payload, definition.correlation_id_path || 'correlation_id');
  if (definition.correlation_id === 'required' && (typeof correlationId !== 'string' || !correlationId)) {
    throw new ContractViolation(definition.id, 'backend_correlation_id_missing');
  }
  if (definition.correlation_id === 'forbidden' && correlationId !== undefined) {
    throw new ContractViolation(definition.id, 'correlation_id_outside_evaluate');
  }
  const status = valueAt(payload, definition.status_path);
  if (typeof status !== 'string' || !status) {
    throw new ContractViolation(definition.id, 'typed_status_missing');
  }
  if (definition.expected_reason && valueAt(payload, definition.unavailability_reason_path) !== definition.expected_reason) {
    throw new ContractViolation(definition.id, 'unavailability_reason_mismatch');
  }
};

const validateFailureProbe = ({ definition, response, payload }) => {
  if (response.status !== 200) {
    throw new ContractViolation(definition.id, `failure_not_typed_json_200:${response.status}`);
  }
  validateSourceChain(definition.id, payload, 'source_chain');
  const status = payload.final_judgment ?? payload.status;
  if (status !== definition.expected_status) {
    throw new ContractViolation(definition.id, `failure_status_mismatch:${String(status)}`);
  }
  const reasons = payload.input_quality?.reasons;
  const reasonMatches = payload.reason === definition.expected_reason
    || (Array.isArray(reasons) && reasons.includes(definition.expected_reason));
  if (!reasonMatches) {
    throw new ContractViolation(definition.id, 'failure_reason_mismatch');
  }
  if (definition.expected_status === 'HOLD' && payload.official_available !== false) {
    throw new ContractViolation(definition.id, 'hold_promoted_to_official');
  }
};

export const runContract = async ({ baseUrl, expectedHost, matrixPath }) => {
  const matrixText = await readFile(matrixPath, 'utf8');
  const matrix = JSON.parse(matrixText);
  const selectionId = matrix.selection_id.format;
  if (matrix.selection_id.format_kind !== 'uuid'
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(selectionId)) {
    throw new ContractViolation('matrix', 'malformed_selection_id_fixture');
  }
  const observations = [];

  for (const definition of matrix.endpoints) {
    const { response, payload } = await requestJson({
      baseUrl,
      expectedHost,
      endpoint: definition.path,
      method: definition.method,
      body: definition.body,
    });
    validateEndpoint({ definition, response, payload, selectionId });
    if (definition.id === 'evaluate'
        && !matrix.evaluation_statuses.includes(valueAt(payload, definition.status_path))) {
      throw new ContractViolation(definition.id, 'invalid_evaluation_status');
    }
    observations.push({ endpoint: definition.id, http_status: response.status, content_type: 'application/json', result: 'PASS' });
  }

  for (const definition of matrix.failure_probes) {
    const { response, payload } = await requestJson({
      baseUrl,
      expectedHost,
      endpoint: definition.path,
      method: definition.method || 'GET',
      body: definition.body,
    });
    validateFailureProbe({ definition, response, payload });
    observations.push({ endpoint: definition.id, http_status: response.status, content_type: 'application/json', result: 'PASS' });
  }

  const evaluateDefinition = matrix.endpoints.find((definition) => definition.id === 'evaluate');
  const forgedCorrelation = await requestJson({
    baseUrl,
    expectedHost,
    endpoint: evaluateDefinition.path,
    method: 'POST',
    body: { ...evaluateDefinition.body, correlation_id: 'client-forged-correlation' },
  });
  if (![400, 422].includes(forgedCorrelation.response.status)
      || forgedCorrelation.payload.reason !== 'client_correlation_id_rejected') {
    throw new ContractViolation('client_correlation_id', 'client_correlation_id_accepted');
  }
  observations.push({ endpoint: 'client_correlation_id', http_status: forgedCorrelation.response.status, content_type: 'application/json', result: 'PASS' });

  return {
    schema_version: 1,
    verdict: 'PASS',
    semantic_owner: matrix.semantic_owner,
    target_host: expectedHost,
    matrix_sha256: createHash('sha256').update(matrixText).digest('hex'),
    observations,
    secrets_recorded: false,
  };
};

const parseArguments = (argumentsList) => {
  const values = new Map();
  for (let index = 0; index < argumentsList.length; index += 2) {
    values.set(argumentsList[index], argumentsList[index + 1]);
  }
  const baseUrl = values.get('--base-url');
  const matrix = values.get('--matrix');
  if (!baseUrl || !matrix) {
    throw new ContractViolation('cli', 'usage: --base-url URL --matrix PATH [--expected-host HOST] [--output PATH]');
  }
  return {
    baseUrl,
    matrixPath: matrix,
    expectedHost: values.get('--expected-host') || new URL(baseUrl).host,
    output: values.get('--output'),
  };
};

const main = async () => {
  const options = parseArguments(process.argv.slice(2));
  const result = await runContract(options);
  const serialized = `${JSON.stringify(result, null, 2)}\n`;
  if (options.output) {
    await mkdir(path.dirname(options.output), { recursive: true });
    await writeFile(options.output, serialized, { encoding: 'utf8', mode: 0o600 });
  }
  process.stdout.write(serialized);
};

if (fileURLToPath(import.meta.url) === process.argv[1]) {
  main().catch((error) => {
    const message = error instanceof ContractViolation ? error.message : 'contract runner failed';
    process.stderr.write(`${message}\n`);
    process.exitCode = 1;
  });
}
