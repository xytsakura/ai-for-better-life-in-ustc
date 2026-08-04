import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const {
  StreamApiError,
  createSseParser,
  parseSseText,
  streamApi,
} = require('../course_agent/web/streaming.js');

function collectWithChunks(chunks) {
  const events = [];
  const parser = createSseParser(event => events.push(event));
  for (const chunk of chunks) parser.feed(chunk);
  parser.end();
  return events;
}

function responseFromChunks(chunks, init = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    }),
    async text() {
      return new TextDecoder().decode(Buffer.concat(chunks.map(chunk => Buffer.from(chunk))));
    },
  };
}

test('parseSseText parses CRLF events and ignores unknown event names', () => {
  const events = parseSseText([
    ': keepalive\r\n',
    'event: start\r\n',
    'data: {"mode":"retrieval"}\r\n',
    '\r\n',
    'event: ignored\r\n',
    'data: {"text":"nope"}\r\n',
    '\r\n',
    'event: delta\r\n',
    'data: {"text":"hello"}\r\n',
    '\r\n',
  ].join(''));

  assert.deepEqual(events, [
    { type: 'start', data: { mode: 'retrieval' } },
    { type: 'delta', data: { text: 'hello' } },
  ]);
});

test('createSseParser handles events split across chunks and residual EOF buffer', () => {
  const events = collectWithChunks([
    'event: del',
    'ta\n',
    'data: {"text":"跨',
    '块"}\n\n',
    'event: complete\n',
    'data: {"answer":"完成"}',
  ]);

  assert.deepEqual(events, [
    { type: 'delta', data: { text: '跨块' } },
    { type: 'complete', data: { answer: '完成' } },
  ]);
});

test('createSseParser joins multi-line data fields before JSON parsing', () => {
  const events = collectWithChunks([
    'event: delta\n',
    'data: {\n',
    'data: "text": "multi-line"\n',
    'data: }\n\n',
  ]);

  assert.deepEqual(events, [
    { type: 'delta', data: { text: 'multi-line' } },
  ]);
});

test('streamApi decodes Unicode split across byte chunks', async () => {
  const encoder = new TextEncoder();
  const bytes = encoder.encode([
    'event: delta\n',
    'data: {"text":"你好🌊"}\n\n',
    'event: complete\n',
    'data: {"answer":"你好🌊","usage":{"input_tokens":1}}\n\n',
  ].join(''));
  const chunks = [
    bytes.slice(0, 24),
    bytes.slice(24, 31),
    bytes.slice(31, 40),
    bytes.slice(40),
  ];
  const events = [];

  const result = await streamApi('/api/query/stream', {
    payload: { question: 'q' },
    fetchImpl: async () => responseFromChunks(chunks),
    onEvent: event => events.push(event),
  });

  assert.equal(events[0].type, 'delta');
  assert.equal(events[0].data.text, '你好🌊');
  assert.deepEqual(result, { answer: '你好🌊', usage: { input_tokens: 1 } });
});

test('streamApi raises partial stream_incomplete on EOF before terminal event', async () => {
  await assert.rejects(
    () => streamApi('/api/query/stream', {
      fetchImpl: async () => responseFromChunks([
        new TextEncoder().encode('event: delta\ndata: {"text":"partial"}\n\n'),
      ]),
    }),
    error => {
      assert.ok(error instanceof StreamApiError);
      assert.equal(error.code, 'stream_incomplete');
      assert.equal(error.partial, true);
      return true;
    },
  );
});

test('streamApi converts SSE error events into StreamApiError', async () => {
  await assert.rejects(
    () => streamApi('/api/branch-query/stream', {
      fetchImpl: async () => responseFromChunks([
        new TextEncoder().encode('event: error\ndata: {"code":"llm_http_503","message":"模型服务暂不可用","retryable":true,"partial":false}\n\n'),
      ]),
    }),
    error => {
      assert.ok(error instanceof StreamApiError);
      assert.equal(error.code, 'llm_http_503');
      assert.equal(error.message, '模型服务暂不可用');
      assert.equal(error.retryable, true);
      assert.equal(error.partial, false);
      return true;
    },
  );
});

test('streamApi reads FastAPI detail.error from pre-stream HTTP failures', async () => {
  const payload = {
    detail: {
      error: {
        code: 'not_authenticated',
        message: '请先选择演示身份',
        retryable: false,
      },
    },
  };

  await assert.rejects(
    () => streamApi('/api/query/stream', {
      fetchImpl: async () => responseFromChunks(
        [new TextEncoder().encode(JSON.stringify(payload))],
        { ok: false, status: 401 },
      ),
    }),
    error => {
      assert.ok(error instanceof StreamApiError);
      assert.equal(error.code, 'not_authenticated');
      assert.equal(error.message, '请先选择演示身份');
      assert.equal(error.retryable, false);
      return true;
    },
  );
});

test('streamApi cancels the reader after terminal event without waiting for EOF', async () => {
  let canceled = false;
  const terminalChunk = new TextEncoder().encode('event: complete\ndata: {"answer":"done"}\n\n');
  const response = {
    ok: true,
    status: 200,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(terminalChunk);
      },
      cancel() {
        canceled = true;
      },
    }),
    async text() {
      return '';
    },
  };

  const result = await streamApi('/api/query/stream', {
    fetchImpl: async () => response,
  });

  assert.deepEqual(result, { answer: 'done' });
  assert.equal(canceled, true);
});
