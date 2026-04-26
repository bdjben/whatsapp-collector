#!/usr/bin/env node
import fs from 'node:fs';

class CDPClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.ws = null;
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      const onOpen = () => {
        cleanup();
        resolve();
      };
      const onError = (event) => {
        cleanup();
        reject(new Error(event?.message || 'WebSocket connection failed'));
      };
      const cleanup = () => {
        this.ws.removeEventListener('open', onOpen);
        this.ws.removeEventListener('error', onError);
      };
      this.ws.addEventListener('open', onOpen);
      this.ws.addEventListener('error', onError);
    });
    this.ws.addEventListener('message', (event) => {
      const payload = JSON.parse(event.data);
      if (!('id' in payload)) return;
      const entry = this.pending.get(payload.id);
      if (!entry) return;
      this.pending.delete(payload.id);
      if (payload.error) {
        entry.reject(new Error(payload.error.message || JSON.stringify(payload.error)));
        return;
      }
      entry.resolve(payload.result || {});
    });
    this.ws.addEventListener('close', () => {
      for (const entry of this.pending.values()) {
        entry.reject(new Error('WebSocket closed before response'));
      }
      this.pending.clear();
    });
  }

  async send(method, params = {}) {
    const id = this.nextId++;
    const payload = { id, method, params };
    const promise = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
    this.ws.send(JSON.stringify(payload));
    return await promise;
  }

  async close() {
    if (!this.ws) return;
    const ws = this.ws;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
      await new Promise((resolve) => {
        if (ws.readyState === WebSocket.CLOSED) {
          resolve();
          return;
        }
        ws.addEventListener('close', () => resolve(), { once: true });
      });
    }
  }
}

function isPageTarget(target) {
  return target?.type === 'page' && target?.webSocketDebuggerUrl;
}

function contains(haystack, needle) {
  if (!needle) return false;
  return String(haystack || '').includes(needle);
}

function matchesMarkerTarget(target, payload) {
  return contains(target.title, payload.markerTitle) || contains(target.url, payload.markerUrlSubstring);
}

function matchesUrlTarget(target, payload) {
  return contains(target.url, payload.targetUrlSubstring);
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} for ${url}`);
  }
  return await response.json();
}

async function listTargets(port) {
  return await fetchJson(`http://127.0.0.1:${port}/json/list`);
}

async function versionInfo(port) {
  return await fetchJson(`http://127.0.0.1:${port}/json/version`);
}

async function withClient(target, fn) {
  const client = new CDPClient(target.webSocketDebuggerUrl);
  await client.connect();
  try {
    return await fn(client);
  } finally {
    await client.close();
  }
}

async function getWindowId(target) {
  return await withClient(target, async (client) => {
    const result = await client.send('Browser.getWindowForTarget', { targetId: target.id });
    return result.windowId;
  });
}

async function chooseTarget(port, payload, { requireTargetUrl = false, preferMarkerWindow = false } = {}) {
  const targets = (await listTargets(port)).filter(isPageTarget);
  const markerTargets = targets.filter((target) => matchesMarkerTarget(target, payload));
  const urlTargets = targets.filter((target) => matchesUrlTarget(target, payload));

  if (preferMarkerWindow && markerTargets.length && urlTargets.length) {
    const markerWindowIds = new Set();
    for (const marker of markerTargets) {
      try {
        markerWindowIds.add(await getWindowId(marker));
      } catch {
        // ignore marker targets that do not map cleanly to a browser window
      }
    }
    for (const candidate of urlTargets) {
      try {
        const candidateWindowId = await getWindowId(candidate);
        if (markerWindowIds.has(candidateWindowId)) {
          return candidate;
        }
      } catch {
        // skip candidates that do not map to a real window
      }
    }
  }

  if (urlTargets.length) return urlTargets[0];
  if (!requireTargetUrl && markerTargets.length) return markerTargets[0];
  throw new Error('No matching Chrome DevTools target found');
}

async function evaluate(port, payload) {
  const target = await chooseTarget(port, payload, { requireTargetUrl: true, preferMarkerWindow: true });
  return await withClient(target, async (client) => {
    const result = await client.send('Runtime.evaluate', {
      expression: payload.expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || 'Runtime.evaluate failed');
    }
    const value = result.result?.value;
    if (value === undefined || value === null) return '';
    return typeof value === 'string' ? value : JSON.stringify(value);
  });
}

async function clickPoint(port, payload) {
  const target = await chooseTarget(port, payload, { requireTargetUrl: true, preferMarkerWindow: true });
  return await withClient(target, async (client) => {
    const result = await client.send('Runtime.evaluate', {
      expression: payload.expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || 'Runtime.evaluate failed while locating click point');
    }
    const point = result.result?.value;
    if (!point || typeof point.x !== 'number' || typeof point.y !== 'number') {
      throw new Error('Click expression did not return numeric {x,y}');
    }
    await client.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 0 });
    await client.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
    await client.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
    return point;
  });
}

async function placeWindow(port, payload) {
  const target = await chooseTarget(port, payload, { requireTargetUrl: false, preferMarkerWindow: false });
  return await withClient(target, async (client) => {
    const { windowId } = await client.send('Browser.getWindowForTarget', { targetId: target.id });
    await client.send('Browser.setWindowBounds', {
      windowId,
      bounds: {
        left: payload.bounds.left,
        top: payload.bounds.top,
        width: payload.bounds.width,
        height: payload.bounds.height,
        windowState: 'normal',
      },
    });
    if (payload.targetUrlSubstring) {
      const targets = (await listTargets(port)).filter(isPageTarget);
      const targetTab = targets.find((item) => matchesUrlTarget(item, payload));
      if (targetTab) {
        return {
          windowId,
          targetId: targetTab.id,
          title: targetTab.title,
          url: targetTab.url,
        };
      }
    }
    return {
      windowId,
      targetId: target.id,
      title: target.title,
      url: target.url,
    };
  });
}

async function main() {
  const input = fs.readFileSync(0, 'utf8').trim();
  const payload = input ? JSON.parse(input) : {};
  const port = Number(payload.port);
  if (!port) {
    throw new Error('Missing numeric port');
  }
  const action = payload.action;
  let result;
  if (action === 'version') {
    result = await versionInfo(port);
  } else if (action === 'list') {
    result = await listTargets(port);
  } else if (action === 'evaluate') {
    result = await evaluate(port, payload);
  } else if (action === 'click-point') {
    result = await clickPoint(port, payload);
  } else if (action === 'place-window') {
    result = await placeWindow(port, payload);
  } else {
    throw new Error(`Unsupported action: ${action}`);
  }
  process.stdout.write(JSON.stringify(result));
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
