const test = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { createApi, createSubmissions } = require('../static/js/api.js');
const { createModelSelectors } = require('../static/js/models.js');
const { createImageScreen } = require('../static/js/create.js');
const { createCatalogScreen } = require('../static/js/catalog.js');
const { createGalleryScreen } = require('../static/js/gallery.js');
const { createMemoryScreen } = require('../static/js/memory.js');
const { createAplusScreen } = require('../static/js/aplus.js');
const { createUsageScreen } = require('../static/js/usage.js');
const { createStudioApp } = require('../static/js/app.js');

class Element {
  constructor() {
    this.value = '';
    this.textContent = '';
    this.disabled = false;
    this.children = [];
    this.listeners = {};
    this.classes = new Set();
    this.isConnected = true;
    this.childNodes = {};
    this.checked = false;
    this.files = [];
    this.classList = {
      add: (x) => this.classes.add(x),
      remove: (x) => this.classes.delete(x),
      contains: (x) => this.classes.has(x),
      toggle: (x, on) => {
        if (on) this.classes.add(x);
        else this.classes.delete(x);
      },
    };
  }
  set innerHTML(value) {
    this.html = value;
    this.children = [];
    this.childNodes = {};
  }
  get innerHTML() {
    return this.html || '';
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.children = children;
    this.value = '';
  }
  querySelector(selector) {
    return this.childNodes[selector] || (this.childNodes[selector] = new Element());
  }
  querySelectorAll() {
    return this.focusable || [];
  }
  addEventListener(event, fn) {
    this.listeners[event] = fn;
  }
  setAttribute(name, value) {
    this[name] = value;
  }
  removeAttribute() {}
  getClientRects() {
    return [1];
  }
  focus() {
    if (this.document) this.document.activeElement = this;
    this.focused = true;
  }
  contains(el) {
    return el === this || this.focusable?.includes(el);
  }
}

function harness() {
  const elements = new Map();
  const document = {
    hidden: false,
    activeElement: null,
    listeners: {},
    addEventListener(name, handler) {
      this.listeners[name] = handler;
    },
    querySelectorAll: () => [],
    createElement: () => new Element(),
    getElementById: (id) => {
      if (!elements.has(id)) {
        const el = new Element();
        el.document = document;
        elements.set(id, el);
      }
      return elements.get(id);
    },
  };
  const environment = {
    document,
    crypto: webcrypto,
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    setTimeout: () => 0,
    clearTimeout() {},
    confirm: () => true,
    window: { open() {} },
    location: { reload() {} },
    FormData: class {
      constructor() {
        this.entries = [];
      }
      append(...args) {
        this.entries.push(args);
      }
    },
  };
  const ui = { $: (id) => document.getElementById(id), toast() {} },
    config = { mode: 'demo', user: 'Demo user', login_required: false, max_images: 250 };
  const client = {
    request: async (url) => (url.endsWith('/history') || url === '/api/memory/brands' ? [] : { matches: {} }),
  };
  const options = {
    environment,
    ui,
    config,
    client,
    submissions: createSubmissions(webcrypto),
    setView() {},
    loadJobs: async () => {},
    loadStats: async () => {},
    openMemory() {},
  };
  return {
    environment,
    document,
    ui,
    config,
    client,
    get: ui.$,
    create: createImageScreen(options),
    catalog: createCatalogScreen(options),
    gallery: createGalleryScreen(options),
    memory: createMemoryScreen(options),
    aplus: createAplusScreen(options),
    usage: createUsageScreen(options),
    models: createModelSelectors({ ...options, document }),
  };
}

const photo = (name = 'a.png') => ({ name, type: 'image/png', size: 100 });
function editSku(h, index, value) {
  const input = h.get('product-list').children[index].querySelector('input');
  input.value = value;
  input.listeners.input({ target: input });
}
async function imageHarness() {
  const h = harness();
  h.create.bind();
  h.get('scenes').value = 'Scene one';
  h.get('model').value = 'gpt-image-2.5-flare';
  h.get('quality').value = 'medium';
  h.get('canvas').value = 'square';
  await h.create.addFiles([photo()]);
  editSku(h, 0, 'SKU-A');
  return h;
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
const parts = (options) => Object.fromEntries(options.body.entries.map(([key, value]) => [key, value]));

test('scene edits cannot re-enable or duplicate an in-flight generation; request keeps its snapshot', async () => {
  const h = await imageHarness(),
    pending = deferred(),
    calls = [];
  h.client.request = async (url, options) => {
    calls.push({ url, options });
    return pending.promise;
  };
  const submission = h.create.generate();
  editSku(h, 0, 'CHANGED');
  h.get('scenes').value = 'Changed scene';
  h.create.recalc();
  h.create.generate();
  assert.equal(h.get('generate-btn').disabled, true);
  assert.equal(calls.length, 1);
  const sent = parts(calls[0].options);
  assert.equal(sent.skus, '["SKU-A"]');
  assert.equal(sent.scenes, 'Scene one');
  assert.match(calls[0].options.headers['Idempotency-Key'], /^[\da-f-]{36}$/);
  pending.resolve({ images_queued: 1, mode: 'demo' });
  await submission;
  assert.equal(h.get('generate-btn').disabled, false);
});

test('a lost response reuses the submission key; successful or changed submissions get a fresh key', async () => {
  const h = await imageHarness(),
    keys = [];
  h.client.request = async (url, options) => {
    keys.push(options.headers['Idempotency-Key']);
    if (keys.length === 1 || keys.length === 4) throw new Error('Connection lost');
    return { images_queued: 1, mode: 'demo' };
  };
  await h.create.generate();
  await h.create.generate();
  await h.create.generate();
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
  await h.create.generate();
  h.get('scenes').value = 'Different prompt';
  await h.create.generate();
  assert.notEqual(keys[3], keys[4]);
});

test('job-wide limit disables submission and explains how to fix it', async () => {
  const h = harness();
  h.create.bind();
  await h.create.addFiles(Array.from({ length: 50 }, () => photo()));
  h.get('scenes').value = Array(12).fill('Scene').join('\n');
  h.create.recalc();
  let calls = 0;
  h.client.request = () => {
    calls++;
    throw Error('Unexpected request');
  };
  assert.equal(h.get('generate-btn').disabled, true);
  assert.match(h.get('generation-hint').textContent, /Maximum 250 images/);
  await h.create.generate();
  assert.equal(calls, 0);
});

test('catalog matching preserves manually corrected SKUs, including edits while matching is pending', async () => {
  const h = await imageHarness(),
    pending = deferred(),
    requests = [];
  editSku(h, 0, 'MANUAL');
  h.client.request = (url, options) => {
    requests.push(JSON.parse(options.body));
    return pending.promise;
  };
  const matching = h.create.addFiles([photo('new.png')]);
  assert.deepEqual(requests[0].filenames, ['new.png']);
  editSku(h, 1, 'EDITED-WHILE-WAITING');
  pending.resolve({
    matches: { 'a.png': { matched: true, sku: 'CAT-A' }, 'new.png': { matched: true, sku: 'CAT-NEW' } },
  });
  await matching;
  assert.match(h.get('product-list').children[0].innerHTML, /value="MANUAL"/);
  assert.match(h.get('product-list').children[1].innerHTML, /value="EDITED-WHILE-WAITING"/);
});

const memoryRecord = (sku) => ({
  sku,
  facts: 'Manual',
  catalog_facts: 'Imported',
  rules: [],
  can_manage_branding: true,
});
test('brand creation assigns the product selected at submission even after switching products', async () => {
  const h = harness(),
    pending = deferred(),
    calls = [];
  h.memory.bind();
  h.client.request = (url, options) => {
    if (options) {
      calls.push({ url, options });
      return url === '/api/memory/brands' ? pending.promise : Promise.resolve({ ok: true });
    }
    if (url === '/api/memory/brands' || url === '/api/memory/products') return Promise.resolve([]);
    if (url === '/api/memory/company')
      return Promise.resolve({ instructions: 'Shared rules', can_edit: true });
    return Promise.resolve(memoryRecord(url.split('/').at(-1)));
  };
  await h.memory.openProductMemory('OLD');
  h.get('memory-brand-new').listeners.click();
  h.get('brand-name').value = 'Brand';
  const saving = h.get('brand-save-profile').listeners.click();
  await h.memory.openProductMemory('NEW');
  pending.resolve({ id: 'brand-id' });
  await saving;
  assert.equal(calls[1].url, '/api/memory/products/OLD/brand');
  assert.equal(h.get('memory-current-sku').textContent, 'NEW');
  assert.equal(h.get('brand-assigned-status').textContent, 'No brand assigned');
});

test('stale product-memory responses cannot replace the newer product', async () => {
  const h = harness(),
    old = deferred(),
    recent = deferred();
  h.client.request = (url) =>
    url === '/api/memory/brands' ? Promise.resolve([]) : url.endsWith('/OLD') ? old.promise : recent.promise;
  const older = h.memory.openProductMemory('OLD'),
    newer = h.memory.openProductMemory('NEW');
  recent.resolve(memoryRecord('NEW'));
  await newer;
  old.resolve({ ...memoryRecord('OLD'), facts: 'Stale' });
  await older;
  assert.equal(h.get('memory-current-sku').textContent, 'NEW');
  assert.equal(h.get('memory-facts').value, 'Manual');
  assert.equal(h.get('catalog-facts').textContent, 'Imported');
});

const render = {
  id: 'render-a',
  sku: 'A',
  kind: 'main',
  url: '/image.jpg',
  model: 'flare',
  quality: 'medium',
};
test('revision retries are idempotent and concurrent revision clicks submit once', async () => {
  const h = harness(),
    pending = deferred(),
    keys = [];
  h.gallery.openModal(render);
  h.get('revision-prompt').value = 'Correct the background';
  h.get('revision-model').value = 'gpt-image-2.5-sunburst';
  h.get('revision-quality').value = 'high';
  h.client.request = (url, options) => {
    keys.push(options.headers['Idempotency-Key']);
    return keys.length === 1 ? pending.promise : Promise.resolve({ mode: 'demo' });
  };
  const revision = h.gallery.revise();
  h.gallery.revise();
  assert.equal(keys.length, 1);
  pending.reject(new Error('Connection lost'));
  await revision;
  await h.gallery.revise();
  assert.equal(keys[0], keys[1]);
});

test('review dialog traps Tab and Escape restores the invoking control', () => {
  const h = harness(),
    first = new Element(),
    last = new Element(),
    trigger = new Element();
  h.gallery.bind();
  h.get('modal').focusable = [first, last];
  h.get('modal').classList.add('hidden');
  h.document.activeElement = trigger;
  h.gallery.openModal(render);
  assert.equal(h.get('close-modal').focused, true);
  h.document.activeElement = last;
  let prevented = false;
  h.document.listeners.keydown({
    key: 'Tab',
    shiftKey: false,
    preventDefault: () => {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  assert.equal(first.focused, true);
  h.document.listeners.keydown({ key: 'Escape', preventDefault() {} });
  assert.equal(h.get('modal').classList.contains('hidden'), true);
  assert.equal(trigger.focused, true);
});

const profile = {
  id: 'profile-1',
  name: 'Saved profile',
  master_instructions: 'Keep the exact product',
  branding_notes: 'Warm background',
  heading_font: 'Cinzel',
  body_font: 'Roboto',
  primary_color: '#283f44',
  secondary_color: '#f4e7d9',
  accent_color: '#9e7655',
  logo_url: '',
};
const profileResult = { profiles: [profile], can_edit: true, default_master: 'Default A+ instructions' };
async function aplusHarness() {
  const h = harness();
  h.aplus.bind();
  h.client.request = async (url) => (url === '/api/aplus/profiles' ? profileResult : { matches: {} });
  h.get('aplus-model').value = 'gpt-image-2.5-sunburst';
  h.get('aplus-quality').value = 'high';
  h.get('aplus-canvas').value = 'banner';
  await h.aplus.load();
  await h.aplus.addFiles([photo('A.png')]);
  return h;
}

test('A+ generation keeps its snapshot, blocks double clicks and retries with the same key', async () => {
  const h = await aplusHarness(),
    pending = deferred(),
    calls = [];
  h.client.request = (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? pending.promise : Promise.resolve({ images_queued: 2 });
  };
  const submission = h.aplus.generate();
  h.aplus.generate();
  h.get('aplus-scenes').value = 'Changed while waiting';
  h.aplus.recalc();
  assert.equal(h.get('aplus-generate').disabled, true);
  assert.equal(calls.length, 1);
  const sent = parts(calls[0].options);
  assert.equal(sent.aplus_profile_id, 'profile-1');
  assert.match(sent.scenes, /Hero banner/);
  h.get('aplus-scenes').value = sent.scenes;
  pending.reject(new Error('Connection lost'));
  await submission;
  await h.aplus.generate();
  assert.equal(calls[0].options.headers['Idempotency-Key'], calls[1].options.headers['Idempotency-Key']);
  await h.aplus.generate();
  assert.notEqual(calls[1].options.headers['Idempotency-Key'], calls[2].options.headers['Idempotency-Key']);
});

test('A+ catalog responses preserve SKU edits made while matching is pending', async () => {
  const h = await aplusHarness(),
    pending = deferred();
  h.client.request = () => pending.promise;
  const matching = h.aplus.addFiles([photo('new.png')]);
  const input = h.get('aplus-file-list').children[1].children[1];
  input.listeners.input({ target: { value: 'MANUAL-SKU' } });
  pending.resolve({ matches: { 'new.png': { matched: true, sku: 'AUTO-SKU' } } });
  await matching;
  assert.equal(h.get('aplus-file-list').children[1].children[1].value, 'MANUAL-SKU');
});

test('A+ save locks the editor and profile selection until the saved profile is loaded', async () => {
  const h = await aplusHarness(),
    pending = deferred();
  h.get('aplus-name').value = 'Updated profile';
  h.get('aplus-name').listeners.input();
  h.client.request = (url, options) =>
    options?.method === 'PUT'
      ? pending.promise
      : Promise.resolve({ ...profileResult, profiles: [{ ...profile, name: 'Updated profile' }] });
  const saving = h.get('aplus-save').listeners.click();
  for (const id of [
    'aplus-save',
    'aplus-profile',
    'aplus-new',
    'aplus-name',
    'aplus-upload-logo',
    'aplus-generate',
  ])
    assert.equal(h.get(id).disabled, true, id);
  pending.resolve({ id: 'profile-1' });
  await saving;
  assert.equal(h.get('aplus-name').value, 'Updated profile');
  assert.equal(h.get('aplus-profile').disabled, false);
  assert.equal(h.get('aplus-generate').disabled, false);
});

test('A+ prompt and batch limits reject oversized work before submission', async () => {
  const h = await aplusHarness();
  h.get('aplus-extra').value = 'x'.repeat(2000);
  h.get('aplus-scenes').value = 's'.repeat(1600);
  h.aplus.recalc();
  assert.equal(h.get('aplus-generate').disabled, true);
  assert.match(h.get('aplus-hint').textContent, /3,500/);
});

test('multiline A+ batch instructions cannot silently create extra paid scenes', async () => {
  const h = await aplusHarness(),
    calls = [];
  h.get('aplus-extra').value = 'Keep the exact product\nLeave room for copy';
  h.client.request = (url, options) => {
    calls.push(options);
    return Promise.resolve({ images_queued: 2 });
  };
  await h.aplus.generate();
  const sent = parts(calls[0]);
  assert.equal(sent.scenes.split('\n').length, 2);
  assert.match(sent.scenes, /Keep the exact product Leave room for copy/);
});

test('quality choices follow the shared model capabilities in every selector', () => {
  const h = harness();
  h.config.models = [
    { id: 'gpt-image-2.5-flare', qualities: ['low', 'medium', 'high', 'xhigh', 'max', 'auto'] },
    { id: 'gpt-image-1-mini', qualities: ['low', 'medium', 'high', 'auto'] },
    { id: 'gpt-image-2.6-flare', qualities: ['low', 'medium', 'high', 'xhigh', 'max', 'auto'] },
    { id: 'gpt-image-2.6-sunburst', qualities: ['low', 'medium', 'high', 'xhigh', 'max', 'auto'] },
  ];
  for (const prefix of ['', 'aplus-', 'revision-']) {
    h.models.syncModelChoices(prefix + 'model', prefix + 'quality', 'gpt-image-2.5-flare', 'high');
    assert.deepEqual(
      h.get(prefix + 'model').children.map((x) => x.value),
      h.config.models.map((x) => x.id),
    );
    for (const model of ['gpt-image-2.6-flare', 'gpt-image-2.6-sunburst']) {
      h.get(prefix + 'model').value = model;
      h.get(prefix + 'model').onchange();
      assert.ok(h.get(prefix + 'quality').children.some((x) => x.value === 'max'));
    }
    h.get(prefix + 'quality').value = 'max';
    h.get(prefix + 'model').value = 'gpt-image-1-mini';
    h.get(prefix + 'model').onchange();
    assert.equal(h.get(prefix + 'quality').value, 'high');
    assert.deepEqual(
      h.get(prefix + 'quality').children.map((x) => x.value),
      ['low', 'medium', 'high', 'auto'],
    );
  }
});

function usageData(balance = 10, canEdit = true) {
  return {
    tracked_spend_usd: 0.01,
    requests: 2,
    unreported: 1,
    pending: 0,
    image_input_tokens: 80,
    text_input_tokens: 20,
    image_output_tokens: 200,
    text_output_tokens: 0,
    notice: 'Unknown costs are not free',
    rate_card_version: 'test',
    tracking_since: null,
    can_edit_budget: canEdit,
    by_user: [],
    by_model: [],
    balance_estimate: {
      balance_checkpoint_usd: balance,
      estimated_remaining_usd: balance,
      alert_percent: 20,
      incomplete: true,
      unreported_since_checkpoint: 1,
      low: false,
    },
  };
}

test('usage polling displays unknown costs and preserves unsaved checkpoint edits', async () => {
  const h = harness();
  h.usage.bind();
  h.client.request = async () => usageData();
  await h.usage.load();
  assert.equal(h.get('usage-unreported').textContent, '1');
  assert.match(h.get('usage-alert').textContent, /incomplete/);
  h.get('usage-checkpoint').value = '42.123456';
  h.get('usage-checkpoint').listeners.input();
  await h.usage.load();
  assert.equal(h.get('usage-checkpoint').value, '42.123456');
});

test('a delayed usage poll cannot overwrite a newly saved balance checkpoint', async () => {
  const h = harness(),
    stale = deferred();
  h.usage.bind();
  h.client.request = async () => usageData();
  await h.usage.load();
  let gets = 0;
  h.client.request = (url, options) =>
    options?.method === 'PUT'
      ? Promise.resolve({ ok: true })
      : ++gets === 1
        ? stale.promise
        : Promise.resolve(usageData(42));
  const polling = h.usage.load();
  h.get('usage-checkpoint').value = '42';
  h.get('usage-threshold').value = '20';
  h.get('usage-checkpoint').listeners.input();
  await h.usage.save();
  stale.resolve(usageData(10));
  await polling;
  assert.equal(Number(h.get('usage-checkpoint').value), 42);
  assert.match(h.get('usage-balance').textContent, /42/);
});

test('nonowners cannot submit a checkpoint even when the handler is invoked directly', async () => {
  const h = harness();
  h.client.request = async () => usageData(10, false);
  await h.usage.load();
  let writes = 0;
  h.client.request = () => {
    writes++;
    throw Error('Unexpected write');
  };
  await h.usage.save();
  assert.equal(writes, 0);
  assert.equal(h.get('usage-save').disabled, true);
});

test('an expired session shows the login form and preserves the server error', async () => {
  const h = harness();
  h.config.login_required = true;
  h.get('login-overlay').classList.add('hidden');
  const client = createApi({
    config: h.config,
    ui: h.ui,
    fetch: async () => ({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Session expired. Sign in again.' }),
    }),
  });
  await assert.rejects(client.request('/api/stats'), /Session expired/);
  assert.equal(h.get('login-overlay').classList.contains('hidden'), false);
});

test('screen instances isolate products and retry keys', async () => {
  const a = await imageHarness(),
    b = await imageHarness(),
    keys = [];
  for (const h of [a, b])
    h.client.request = async (url, options) => {
      keys.push(options.headers['Idempotency-Key']);
      throw Error('Connection lost');
    };
  await a.create.generate();
  await b.create.generate();
  await a.create.generate();
  assert.notEqual(keys[0], keys[1]);
  assert.equal(keys[0], keys[2]);
  a.get('product-list').children[0].querySelector('button').listeners.click();
  a.create.recalc();
  b.create.recalc();
  assert.equal(a.get('generate-btn').disabled, true);
  assert.equal(b.get('generate-btn').disabled, false);
});

test('bootstrap links all screen modules and navigation to the shared API', async () => {
  const h = harness(),
    urls = [];
  h.environment.fetch = async (url) => {
    urls.push(url);
    const data =
      url === '/api/config'
        ? {
            ...h.config,
            features: { aplus: false, usage: true },
            models: [{ id: 'test', qualities: ['high'] }],
          }
        : url === '/api/stats'
          ? { generated: 0, approved: 0, pending: 0, jobs: 0, catalog: 0 }
          : url === '/api/usage'
            ? usageData()
            : [];
    return { ok: true, status: 200, json: async () => data };
  };
  await createStudioApp(h.environment).init();
  assert.equal(h.get('nav-aplus').classList.contains('hidden'), true);
  assert.equal(h.get('model').value, 'test');
  assert.equal(h.get('revision-model').value, 'test');
  h.get('nav-usage').listeners.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.get('usage-view').classList.contains('hidden'), false);
  assert.equal(h.get('create-view').classList.contains('hidden'), true);
  assert.ok(urls.includes('/api/usage'));
});

test('a delayed catalog search cannot overwrite the latest results', async () => {
  const h = harness(),
    old = deferred(),
    recent = deferred();
  h.client.request = (url) => (url.endsWith('old') ? old.promise : recent.promise);
  const older = h.catalog.loadCatalog('old'),
    newer = h.catalog.loadCatalog('new');
  recent.resolve({ products: [{ id: 'new', sku: 'NEW', title: 'Current result' }], total: 1 });
  await newer;
  old.resolve({ products: [{ id: 'old', sku: 'OLD', title: 'Stale result' }], total: 1 });
  await older;
  assert.match(h.get('catalog-table-wrap').innerHTML, /Current result/);
  assert.doesNotMatch(h.get('catalog-table-wrap').innerHTML, /Stale result/);
});
