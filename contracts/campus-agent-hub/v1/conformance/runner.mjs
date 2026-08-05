import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { EventSchemas, RunAgentInputSchema } from '@ag-ui/core';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const MAX_RESPONSE_BYTES = 1_048_576;

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith('--')) throw new Error(`Unexpected argument: ${key}`);
    const value = argv[index + 1];
    if (!value || value.startsWith('--')) throw new Error(`Missing value for ${key}`);
    args[key.slice(2)] = value;
    index += 1;
  }
  if (!args.manifest) throw new Error('--manifest is required');
  return args;
}

async function loadJson(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

function endpointFor(original, baseUrl) {
  if (!baseUrl) return original;
  const source = new URL(original);
  const base = new URL(baseUrl);
  return new URL(`${source.pathname}${source.search}`, base).toString();
}

function check(name, status, startedAt, errorCode = null, safeDetail = null) {
  return {
    name,
    status,
    duration_ms: Date.now() - startedAt,
    error_code: errorCode,
    safe_detail: safeDetail,
  };
}

async function timedFetch(url, options = {}, timeoutMs = 10_000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal, redirect: 'error' });
  } finally {
    clearTimeout(timeout);
  }
}

function parseSseFrames(text) {
  const events = [];
  for (const frame of text.replaceAll('\r\n', '\n').split('\n\n')) {
    if (!frame.trim()) continue;
    const data = frame
      .split('\n')
      .filter(line => line.startsWith('data:'))
      .map(line => line.slice(5).trimStart())
      .join('\n');
    if (!data) continue;
    events.push(JSON.parse(data));
  }
  return events;
}

function validateAguiSequence(events) {
  if (!events.length || events[0].type !== 'RUN_STARTED') {
    throw new Error('RUN_STARTED must be the first event');
  }
  const terminal = events
    .map((event, index) => ({ type: event.type, index }))
    .filter(item => item.type === 'RUN_FINISHED' || item.type === 'RUN_ERROR');
  if (terminal.length !== 1 || terminal[0].index !== events.length - 1) {
    throw new Error('Exactly one terminal event must be last');
  }
  const openMessages = new Set();
  const knownTools = new Set();
  for (const event of events) {
    if (event.type === 'TEXT_MESSAGE_START') openMessages.add(event.messageId);
    if (event.type === 'TEXT_MESSAGE_CONTENT' && !openMessages.has(event.messageId)) {
      throw new Error('TEXT_MESSAGE_CONTENT references an unopened message');
    }
    if (event.type === 'TEXT_MESSAGE_END') {
      if (!openMessages.delete(event.messageId)) throw new Error('TEXT_MESSAGE_END references an unopened message');
    }
    if (event.type === 'TOOL_CALL_START') knownTools.add(event.toolCallId);
    if (['TOOL_CALL_ARGS', 'TOOL_CALL_END', 'TOOL_CALL_RESULT'].includes(event.type) && !knownTools.has(event.toolCallId)) {
      throw new Error(`${event.type} references an unknown tool call`);
    }
  }
  if (openMessages.size) throw new Error('A message stream ended before TEXT_MESSAGE_END');
}

async function responseTextBounded(response) {
  const advertised = Number(response.headers.get('content-length') || 0);
  if (advertised > MAX_RESPONSE_BYTES) throw new Error('Response is too large');
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.byteLength > MAX_RESPONSE_BYTES) throw new Error('Response is too large');
  return new TextDecoder().decode(bytes);
}

async function run() {
  const args = parseArgs(process.argv.slice(2));
  const manifestPath = resolve(args.manifest);
  const startedAt = new Date().toISOString();
  const checks = [];
  let manifest = null;

  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const manifestSchema = await loadJson(resolve(ROOT, 'manifest.schema.json'));
  const healthSchema = await loadJson(resolve(ROOT, 'health.schema.json'));
  const simpleChatSchema = await loadJson(resolve(ROOT, 'simple-chat.schema.json'));
  const validateManifest = ajv.compile(manifestSchema);
  const validateHealth = ajv.compile(healthSchema);
  ajv.addSchema(simpleChatSchema, simpleChatSchema.$id);
  const validateSimpleResponse = ajv.compile({ $ref: `${simpleChatSchema.$id}#/$defs/response` });

  let started = Date.now();
  try {
    manifest = await loadJson(manifestPath);
    if (!validateManifest(manifest)) {
      throw new Error(ajv.errorsText(validateManifest.errors, { separator: '; ' }));
    }
    checks.push(check('manifest_schema', 'passed', started));
  } catch (error) {
    checks.push(check('manifest_schema', 'failed', started, 'manifest_invalid', String(error.message)));
  }

  if (manifest) {
    started = Date.now();
    try {
      const launchUrl = endpointFor(manifest.integration.launch_url, args['base-url']);
      const launch = await timedFetch(launchUrl, { method: 'GET' }, 5_000);
      if (!launch.ok) throw new Error(`HTTP ${launch.status}`);
      checks.push(check('launch_url', 'passed', started));
    } catch (error) {
      checks.push(check('launch_url', 'failed', started, 'launch_unavailable', String(error.message)));
    }

    if (manifest.integration.mode === 'connected') {
      started = Date.now();
      try {
        const healthUrl = endpointFor(manifest.integration.health_endpoint, args['base-url']);
        const response = await timedFetch(healthUrl, { method: 'GET' }, 5_000);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const health = await response.json();
        if (!validateHealth(health)) {
          throw new Error(ajv.errorsText(validateHealth.errors, { separator: '; ' }));
        }
        checks.push(check('health_schema', 'passed', started));
      } catch (error) {
        checks.push(check('health_schema', 'failed', started, 'health_invalid', String(error.message)));
      }

      const headers = { 'content-type': 'application/json' };
      if (args.token) headers.authorization = `Bearer ${args.token}`;
      const chatUrl = endpointFor(manifest.integration.chat_endpoint, args['base-url']);

      if (args.token) {
        started = Date.now();
        try {
          const unauthorized = await timedFetch(chatUrl, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(manifest.integration.protocol === 'ag-ui' ? {
              threadId: `identity-${randomUUID()}`,
              runId: randomUUID(),
              state: {},
              messages: [{ id: randomUUID(), role: 'user', content: 'identity check' }],
              tools: [],
              context: [],
              forwardedProps: {},
            } : {
              thread_id: `identity-${randomUUID()}`,
              run_id: randomUUID(),
              messages: [{ id: randomUUID(), role: 'user', content: 'identity check' }],
              context: {},
            }),
          }, 5_000);
          if (![401, 403].includes(unauthorized.status)) {
            throw new Error(`Expected 401/403, received HTTP ${unauthorized.status}`);
          }
          checks.push(check('identity_rejection', 'passed', started));
        } catch (error) {
          checks.push(check('identity_rejection', 'failed', started, 'identity_not_enforced', String(error.message)));
        }
      } else {
        checks.push(check('identity_rejection', 'skipped', Date.now(), null, 'Provide --token to verify rejection and acceptance'));
      }

      if (manifest.integration.protocol === 'ag-ui') {
        started = Date.now();
        try {
          const messageId = randomUUID();
          const input = RunAgentInputSchema.parse({
            threadId: `conformance-${randomUUID()}`,
            runId: randomUUID(),
            state: {},
            messages: [{ id: messageId, role: 'user', content: '请简短回复：协议测试通过。' }],
            tools: [],
            context: [{ description: 'Conformance test marker', value: 'campus-agent-hub-v1' }],
            forwardedProps: { conformance: true },
          });
          const response = await timedFetch(chatUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(input),
          }, 30_000);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const contentType = response.headers.get('content-type') || '';
          if (!contentType.toLowerCase().startsWith('text/event-stream')) {
            throw new Error(`Unexpected content-type: ${contentType}`);
          }
          const events = parseSseFrames(await responseTextBounded(response));
          if (!events.length) throw new Error('Empty SSE stream');
          for (const event of events) EventSchemas.parse(event);
          validateAguiSequence(events);
          checks.push(check('ag_ui_stream', 'passed', started, null, `${events.length} events`));
        } catch (error) {
          checks.push(check('ag_ui_stream', 'failed', started, 'protocol_error', String(error.message)));
        }
      } else {
        started = Date.now();
        try {
          const request = {
            thread_id: `conformance-${randomUUID()}`,
            run_id: randomUUID(),
            messages: [{ id: randomUUID(), role: 'user', content: '请简短回复：协议测试通过。' }],
            context: { conformance: true },
          };
          const response = await timedFetch(chatUrl, {
            method: 'POST',
            headers,
            body: JSON.stringify(request),
          }, 30_000);
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const payload = JSON.parse(await responseTextBounded(response));
          if (!validateSimpleResponse(payload)) {
            throw new Error(ajv.errorsText(validateSimpleResponse.errors, { separator: '; ' }));
          }
          checks.push(check('simple_chat', 'passed', started));
        } catch (error) {
          checks.push(check('simple_chat', 'failed', started, 'protocol_error', String(error.message)));
        }
      }
    }
  }

  const result = {
    contract_version: '1.0',
    manifest_hash: manifest ? await import('node:crypto').then(({ createHash }) =>
      createHash('sha256').update(JSON.stringify(manifest)).digest('hex')) : null,
    agent_id: manifest?.id || null,
    agent_version: manifest?.version || null,
    started_at: startedAt,
    completed_at: new Date().toISOString(),
    checks,
    overall_status: checks.length > 0 && checks.every(item => item.status === 'passed' || item.status === 'skipped') ? 'passed' : 'failed',
  };

  const output = args.output ? resolve(args.output) : null;
  if (output) {
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = result.overall_status === 'passed' ? 0 : 1;
}

run().catch(error => {
  process.stderr.write(`Conformance runner failed: ${error.message}\n`);
  process.exitCode = 2;
});
