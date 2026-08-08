(function (root, factory) {
  const api = factory(root);
  root.CourseAgentStreaming = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(typeof globalThis !== 'undefined' ? globalThis : window, function (root) {
  const KNOWN_EVENTS = new Set(['start', 'delta', 'reasoning', 'complete', 'error']);

  class StreamApiError extends Error {
    constructor(message, options = {}) {
      super(message || '流式请求失败');
      this.name = 'StreamApiError';
      this.code = options.code || 'stream_error';
      this.retryable = Boolean(options.retryable);
      this.partial = Boolean(options.partial);
      this.payload = options.payload || null;
    }
  }

  function isAbortError(error) {
    return error?.name === 'AbortError' || error?.code === 20;
  }

  function normalizeErrorPayload(payload, fallbackMessage = '流式请求失败') {
    if (payload && typeof payload === 'object') {
      return {
        code: String(payload.code || 'stream_error'),
        message: String(payload.message || fallbackMessage),
        retryable: Boolean(payload.retryable),
        partial: Boolean(payload.partial),
        payload,
      };
    }
    return {
      code: 'stream_error',
      message: fallbackMessage,
      retryable: false,
      partial: false,
      payload: null,
    };
  }

  async function parseHttpError(response) {
    let text = '';
    try { text = await response.text(); } catch {}
    let payload = null;
    try { payload = text ? JSON.parse(text) : null; } catch { payload = { error: { message: text } }; }
    const detailError = payload?.detail?.error;
    const detailMessage = typeof payload?.detail === 'string' ? payload.detail : '';
    const message =
      payload?.error?.message ||
      detailError?.message ||
      (Array.isArray(payload?.detail)
        ? payload.detail.map((item) => item?.msg || item?.message || String(item)).join('；')
        : detailMessage) ||
      `请求失败（HTTP ${response.status}）`;
    return new StreamApiError(message, {
      code: payload?.error?.code || detailError?.code || `http_${response.status}`,
      retryable: Boolean(payload?.error?.retryable || detailError?.retryable) || response.status >= 500,
      partial: false,
      payload,
    });
  }

  function createSseParser(onEvent) {
    let buffer = '';
    let eventName = '';
    let dataLines = [];

    const resetEvent = () => {
      eventName = '';
      dataLines = [];
    };

    const dispatch = () => {
      if (!dataLines.length) {
        resetEvent();
        return;
      }
      const type = eventName || 'message';
      const rawData = dataLines.join('\n');
      resetEvent();
      if (!KNOWN_EVENTS.has(type)) return;
      let data;
      try {
        data = rawData ? JSON.parse(rawData) : null;
      } catch (error) {
        throw new StreamApiError('流式响应格式错误', {
          code: 'stream_parse_error',
          retryable: false,
          partial: true,
          payload: { raw: rawData, error: String(error?.message || error) },
        });
      }
      onEvent({ type, data });
    };

    const consumeLine = (line) => {
      if (line.endsWith('\r')) line = line.slice(0, -1);
      if (line === '') {
        dispatch();
        return;
      }
      if (line.startsWith(':')) return;
      const colonIndex = line.indexOf(':');
      const field = colonIndex === -1 ? line : line.slice(0, colonIndex);
      let value = colonIndex === -1 ? '' : line.slice(colonIndex + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') eventName = value;
      else if (field === 'data') dataLines.push(value);
    };

    return {
      feed(chunk) {
        buffer += String(chunk || '');
        for (;;) {
          const lf = buffer.indexOf('\n');
          if (lf === -1) break;
          const line = buffer.slice(0, lf);
          buffer = buffer.slice(lf + 1);
          consumeLine(line);
        }
      },
      end() {
        if (buffer.length) {
          consumeLine(buffer);
          buffer = '';
        }
        if (dataLines.length) dispatch();
      },
    };
  }

  function parseSseText(text) {
    const events = [];
    const parser = createSseParser(event => events.push(event));
    parser.feed(text);
    parser.end();
    return events;
  }

  async function streamApi(path, options = {}) {
    const {
      payload,
      signal,
      onEvent,
      fetchImpl = root.fetch?.bind(root),
      credentials = 'same-origin',
      headers = {},
    } = options;
    if (typeof fetchImpl !== 'function') {
      throw new StreamApiError('当前浏览器不支持 fetch 流式读取', {
        code: 'stream_unsupported',
        retryable: false,
      });
    }

    const response = await fetchImpl(path, {
      method: 'POST',
      credentials,
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify(payload || {}),
      signal,
    });
    if (!response.ok) throw await parseHttpError(response);
    if (!response.body?.getReader) {
      throw new StreamApiError('当前浏览器不支持响应流读取', {
        code: 'stream_unsupported',
        retryable: false,
      });
    }

    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let terminal = null;
    let receivedText = false;
    let streamEnded = false;
    const parser = createSseParser(event => {
      if (terminal) return;
      if (event.type === 'delta' && event.data?.text) receivedText = true;
      if (event.type === 'complete' || event.type === 'error') terminal = event;
      if (typeof onEvent === 'function') onEvent(event);
    });

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) {
          streamEnded = true;
          break;
        }
        parser.feed(decoder.decode(value, { stream: true }));
        if (terminal) break;
      }
      const tail = decoder.decode();
      if (tail) parser.feed(tail);
      parser.end();
    } catch (error) {
      if (isAbortError(error)) throw error;
      if (error instanceof StreamApiError) throw error;
      throw new StreamApiError(error?.message || '流式读取失败', {
        code: 'stream_read_error',
        retryable: true,
        partial: receivedText,
      });
    } finally {
      if (terminal && !streamEnded) {
        try { await reader.cancel(); } catch {}
      }
      try { reader.releaseLock(); } catch {}
    }

    if (terminal?.type === 'complete') return terminal.data || {};
    if (terminal?.type === 'error') {
      const normalized = normalizeErrorPayload(terminal.data, '模型生成失败');
      throw new StreamApiError(normalized.message, normalized);
    }
    throw new StreamApiError('回答中断，可重试', {
      code: 'stream_incomplete',
      retryable: true,
      partial: receivedText,
    });
  }

  return {
    StreamApiError,
    createSseParser,
    parseSseText,
    streamApi,
    isAbortError,
  };
});
