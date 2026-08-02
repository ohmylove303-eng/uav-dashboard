import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import http from 'node:http';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { runContract } from './api_contract_runner.mjs';


const selectionId = '00000000-0000-4000-8000-000000000003';

const listen = async (handler) => {
  const server = http.createServer(handler);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert(address && typeof address === 'object');
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())),
  };
};

const sendJson = (response, statusCode, payload) => {
  response.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8' });
  response.end(JSON.stringify(payload));
};

const compliantPayload = (requestUrl, requestBody) => {
  const path = requestUrl.pathname;
  const fixture = requestUrl.searchParams.get('contract_fixture');
  if (fixture === 'registry_401') {
    return { status: 'unavailable', reason: 'official_registry_unavailable', source_chain: ['molit_registry'], official_available: false, selection_id: selectionId };
  }
  if (fixture === 'bridge_502' || fixture === 'bridge_520') {
    return { status: 'unavailable', reason: `official_gis_bridge_upstream_${fixture.slice(-3)}`, source_chain: ['official_gis_bridge'], official_available: false, selection_id: selectionId };
  }
  if (fixture === 'non_authoritative_weather') {
    return { final_judgment: 'HOLD', source_chain: ['open_meteo_surface'], official_available: false, selection_id: selectionId, correlation_id: 'backend-correlation-hold', input_quality: { reasons: ['weather:non_authoritative_weather'] } };
  }
  if (path === '/api/evaluate') {
    return { final_judgment: 'GO', source_chain: ['vworld_wfs', 'kma_surface_observation'], official_available: true, selection_id: requestBody.selection_id, correlation_id: 'backend-correlation-0001', input_quality: { reasons: [] } };
  }
  if (path === '/api/weather') {
    return { status: 'official_verified', reason: null, source_chain: ['kma_surface_observation'], official_available: true, selection_id: requestUrl.searchParams.get('selection_id') };
  }
  return { status: 'official_verified', reason: null, source_chain: ['vworld_wfs'], official_available: true, selection_id: requestUrl.searchParams.get('selection_id') };
};

const startStub = async (mode = 'compliant') => listen(async (request, response) => {
  const requestUrl = new URL(request.url, 'http://stub.test');
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  let requestBody = {};
  if (chunks.length) requestBody = JSON.parse(Buffer.concat(chunks).toString('utf8'));

  if (mode === 'spa_html') {
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end('<!doctype html><div id="app"></div>');
    return;
  }
  if (mode === 'plain_401' || mode === 'plain_502' || mode === 'plain_520') {
    const statusCode = Number(mode.slice(-3));
    response.writeHead(statusCode, { 'Content-Type': 'text/plain' });
    response.end('upstream failed');
    return;
  }
  if (requestUrl.pathname === '/api/building-footprint/cache') {
    if (mode === 'cache_write_open') {
      sendJson(response, 200, { status: 'accepted', reason: 'cache_write_recorded', source_chain: ['render_internal_cache'], official_available: false });
      return;
    }
    sendJson(response, 401, { status: 'unavailable', reason: 'cache_write_auth_required', source_chain: ['render_internal_cache'], official_available: false });
    return;
  }
  if (requestUrl.pathname === '/api/evaluate' && requestBody.correlation_id) {
    if (mode === 'client_correlation_accepted') {
      sendJson(response, 200, { ...compliantPayload(requestUrl, requestBody), correlation_id: requestBody.correlation_id });
      return;
    }
    sendJson(response, 422, { status: 'unavailable', reason: 'client_correlation_id_rejected', source_chain: ['render_validation'], official_available: false });
    return;
  }

  const payload = compliantPayload(requestUrl, requestBody);
  if (mode === 'id_mismatch' && payload.selection_id) payload.selection_id = 'wrong-selection';
  if (mode === 'non_authoritative_weather' && requestUrl.pathname === '/api/weather') {
    payload.status = 'estimated';
    payload.official_available = false;
    payload.source_chain = ['open_meteo_surface'];
  }
  sendJson(response, 200, payload);
});

const matrixPath = new URL('./uav_api_contract_matrix.json', import.meta.url);

test('accepts the authoritative JSON matrix when every owner contract matches', async () => {
  const stub = await startStub();
  try {
    const result = await runContract({ baseUrl: stub.baseUrl, expectedHost: new URL(stub.baseUrl).host, matrixPath });
    assert.equal(result.verdict, 'PASS');
    assert.equal(result.observations.length, 12);
  } finally {
    await stub.close();
  }
});

test('CLI executes the matrix against a local candidate Render stub', async () => {
  const stub = await startStub();
  try {
    const result = await new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [
        fileURLToPath(new URL('./api_contract_runner.mjs', import.meta.url)),
        '--base-url', stub.baseUrl,
        '--expected-host', new URL(stub.baseUrl).host,
        '--matrix', fileURLToPath(matrixPath),
      ]);
      const stdout = [];
      const stderr = [];
      child.stdout.on('data', (chunk) => stdout.push(chunk));
      child.stderr.on('data', (chunk) => stderr.push(chunk));
      child.on('error', reject);
      child.on('close', (code) => resolve({
        code,
        stdout: Buffer.concat(stdout).toString('utf8'),
        stderr: Buffer.concat(stderr).toString('utf8'),
      }));
    });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).verdict, 'PASS');
  } finally {
    await stub.close();
  }
});

for (const mode of ['spa_html', 'plain_401', 'plain_502', 'plain_520', 'id_mismatch', 'client_correlation_accepted', 'non_authoritative_weather', 'cache_write_open']) {
  test(`rejects ${mode}`, async () => {
    const stub = await startStub(mode);
    try {
      await assert.rejects(
        runContract({ baseUrl: stub.baseUrl, expectedHost: new URL(stub.baseUrl).host, matrixPath }),
      );
    } finally {
      await stub.close();
    }
  });
}

test('rejects a redirect to an unintended host', async () => {
  const unintended = await startStub();
  const redirect = await listen((_request, response) => {
    response.writeHead(302, { Location: `${unintended.baseUrl}/api/weather` });
    response.end();
  });
  try {
    await assert.rejects(
      runContract({ baseUrl: redirect.baseUrl, expectedHost: new URL(redirect.baseUrl).host, matrixPath }),
    );
  } finally {
    await redirect.close();
    await unintended.close();
  }
});
