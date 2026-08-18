import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { EventSchemas, RunAgentInputSchema } from '@ag-ui/core';

const ROOT = resolve(import.meta.dirname, '..', '..');

async function json(relativePath) {
  return JSON.parse(await readFile(resolve(ROOT, relativePath), 'utf8'));
}

test('all packaged manifest examples satisfy Manifest v1', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const validate = ajv.compile(await json('manifest.schema.json'));
  for (const name of ['hanhai-connected.json', 'demo-link-app.json', 'demo-connected.json']) {
    const payload = await json(`examples/${name}`);
    assert.equal(validate(payload), true, `${name}: ${ajv.errorsText(validate.errors)}`);
  }
});

test('connected manifests require protocol and endpoints', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const validate = ajv.compile(await json('manifest.schema.json'));
  const payload = await json('examples/demo-connected.json');
  delete payload.integration.chat_endpoint;
  assert.equal(validate(payload), false);
});

test('manifest model_runtime extension is optional and validates platform gateway modes', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const validate = ajv.compile(await json('manifest.schema.json'));
  const payload = await json('examples/demo-connected.json');
  payload.capabilities.push('platform-model-gateway');
  payload.model_runtime = {
    mode: 'platform_optional',
    gateway_contract: 'campus-model-gateway-v1',
    supported_api_styles: ['responses', 'chat_completions'],
  };
  assert.equal(validate(payload), true, ajv.errorsText(validate.errors));

  payload.model_runtime.supported_api_styles.push('responses');
  assert.equal(validate(payload), false);
});

test('health fixture satisfies Health v1', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const validate = ajv.compile(await json('health.schema.json'));
  assert.equal(validate({
    status: 'ok',
    version: '1.1.0',
    contract_version: '1.0',
    capabilities: ['streaming'],
    checked_at: new Date().toISOString(),
  }), true, ajv.errorsText(validate.errors));
});

test('official AG-UI schemas accept the contract minimal run', () => {
  const input = RunAgentInputSchema.parse({
    threadId: 'thread-1',
    runId: 'run-1',
    state: {},
    messages: [{ id: 'message-1', role: 'user', content: 'hello' }],
    tools: [],
    context: [],
    forwardedProps: {},
  });
  assert.equal(input.threadId, 'thread-1');

  for (const event of [
    { type: 'RUN_STARTED', threadId: 'thread-1', runId: 'run-1' },
    { type: 'TEXT_MESSAGE_START', messageId: 'assistant-1', role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'assistant-1', delta: 'hello' },
    { type: 'TEXT_MESSAGE_END', messageId: 'assistant-1' },
    { type: 'RUN_FINISHED', threadId: 'thread-1', runId: 'run-1' },
    { type: 'RUN_ERROR', message: 'agent_unavailable', code: 'agent_unavailable' },
  ]) {
    assert.equal(EventSchemas.safeParse(event).success, true, JSON.stringify(event));
  }
});

test('simple-chat request and assistant response satisfy their schemas', async () => {
  const ajv = new Ajv2020({ allErrors: true, strict: true });
  addFormats(ajv);
  const schema = await json('simple-chat.schema.json');
  ajv.addSchema(schema, schema.$id);
  const validateRequest = ajv.compile({ $ref: `${schema.$id}#/$defs/request` });
  const validateResponse = ajv.compile({ $ref: `${schema.$id}#/$defs/response` });

  assert.equal(validateRequest({
    thread_id: 'thread-1',
    run_id: 'run-1',
    messages: [{ id: 'message-1', role: 'user', content: 'hello' }],
    context: { locale: 'zh-CN' },
  }), true, ajv.errorsText(validateRequest.errors));

  assert.equal(validateResponse({
    message: { id: 'assistant-1', role: 'assistant', content: 'hello' },
    citations: [{ label: 'S1', title: 'Demo source', url: 'https://example.com/source' }],
    usage: { input_tokens: 8, output_tokens: 3 },
  }), true, ajv.errorsText(validateResponse.errors));

  assert.equal(validateResponse({
    message: { id: 'user-2', role: 'user', content: 'not an assistant response' },
  }), false);
});
