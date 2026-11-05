import { createUi } from './dom.js';
import { createApi, createSubmissions } from './api.js';
import { createModelSelectors } from './models.js';
import { createImageScreen } from './create.js';
import { createCatalogScreen } from './catalog.js';
import { createJobsScreen } from './jobs.js';
import { createGalleryScreen } from './gallery.js';
import { createMemoryScreen } from './memory.js';
import { createAplusScreen } from './aplus.js';
import { createUsageScreen } from './usage.js';

/** Compose screen controllers; each owns its edits and in-flight requests. */
export function createStudioApp(environment = globalThis) {
  const { document, setTimeout, location } = environment;
  const ui = createUi(environment),
    { $, toast } = ui;
  const config = { mode: 'demo', user: 'Demo user', login_required: false };
  const client = createApi({ config, ui, fetch: environment.fetch.bind(environment) });
  const submissions = createSubmissions(environment.crypto);
  const { syncModelChoices } = createModelSelectors({ config, ui, document });
  const common = {
    ui,
    client,
    config,
    submissions,
    environment,
    setView,
    loadStats,
    loadJobs: () => jobs.loadJobs(),
  };
  const jobs = createJobsScreen(common);
  const memory = createMemoryScreen(common);
  const create = createImageScreen({
    ...common,
    openMemory: (sku) => {
      setView('memory');
      if (sku) memory.openProductMemory(sku);
    },
  });
  const catalog = createCatalogScreen(common);
  const gallery = createGalleryScreen(common);
  const aplus = createAplusScreen(common);
  const usage = createUsageScreen(common);
  let visible = 'create';

  function setView(name) {
    visible = name;
    for (const view of ['create', 'catalog', 'gallery', 'jobs', 'memory', 'aplus', 'usage']) {
      $(`${view}-view`)?.classList.toggle('hidden', name !== view);
      $(`nav-${view}`)?.classList.toggle('active', name === view);
    }
    const loaders = {
      catalog: catalog.loadCatalog,
      gallery: gallery.loadRenders,
      jobs: jobs.loadJobs,
      memory: memory.loadMemoryHome,
      aplus: aplus.load,
      usage: usage.load,
    };
    loaders[name]?.();
  }

  async function loadConfig() {
    Object.assign(config, await client.request('/api/config'));
    syncModelChoices('model', 'quality', 'gpt-image-2.5-flare', 'high');
    syncModelChoices('revision-model', 'revision-quality', 'gpt-image-2.5-sunburst', 'high');
    syncModelChoices('aplus-model', 'aplus-quality', 'gpt-image-2.5-sunburst', 'high');
    for (const feature of ['aplus', 'usage'])
      $(`nav-${feature}`).classList.toggle('hidden', config.features?.[feature] === false);
    $('mode-label').textContent =
      config.mode === 'demo' ? 'Demo mode · no API charges' : 'Live Image API · paid usage';
    $('mode-dot').classList.toggle('demo', config.mode === 'demo');
    $('account-name').textContent = config.user || 'Not signed in';
    $('account-label').textContent = config.mode === 'demo' ? 'Preview workspace' : 'Team workspace';
    if (config.catalog_count !== undefined) $('catalog-count').textContent = config.catalog_count;
    $('login-overlay').classList.toggle('hidden', !config.login_required || !!config.user);
    create.recalc();
  }

  async function loadStats() {
    try {
      const stats = await client.request('/api/stats');
      for (const key of ['generated', 'approved', 'pending', 'jobs'])
        $(`metric-${key}`).textContent = stats[key].toLocaleString('en-IN');
      $('library-count').textContent = stats.generated;
      if (stats.catalog !== undefined) $('catalog-count').textContent = stats.catalog;
    } catch (error) {
      if (!config.login_required) toast(error.message, true);
    }
  }

  async function refresh() {
    await Promise.allSettled([loadStats(), jobs.loadJobs(), gallery.loadRenders()]);
    if (visible === 'usage') await usage.load();
  }

  function bind() {
    for (const screen of [create, catalog, jobs, gallery, memory, aplus, usage]) screen.bind();
    for (const view of ['create', 'catalog', 'gallery', 'jobs', 'memory', 'aplus', 'usage']) {
      $(`nav-${view}`).addEventListener('click', () => setView(view));
    }
    $('see-queue').addEventListener('click', () => setView('jobs'));
    $('refresh-btn').addEventListener('click', refresh);
    $('login-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      $('login-error').textContent = '';
      try {
        const result = await client.request('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: $('login-user').value, password: $('login-pass').value }),
        });
        config.user = result.user;
        $('login-overlay').classList.add('hidden');
        $('account-name').textContent = result.user;
        toast('Signed in');
        refresh();
      } catch (error) {
        $('login-error').textContent = error.message;
      }
    });
    $('logout-btn').addEventListener('click', async () => {
      await client.request('/api/logout', { method: 'POST' });
      location.reload();
    });
  }

  async function init() {
    bind();
    try {
      await loadConfig();
      if (!config.login_required || config.user) await refresh();
    } catch (error) {
      toast('Could not connect to app server: ' + error.message, true);
    }
    const poll = async () => {
      try {
        if (document.hidden || (config.login_required && !config.user)) return;
        const requests = [loadStats()];
        if (visible === 'jobs') requests.push(jobs.loadJobs());
        if (visible === 'gallery') requests.push(gallery.loadRenders());
        if (visible === 'usage') requests.push(usage.load());
        await Promise.allSettled(requests);
      } finally {
        setTimeout(poll, 5000);
      }
    };
    setTimeout(poll, 5000);
  }
  return { init };
}

// The entry module is also importable in Node without creating a browser session.
if (typeof document !== 'undefined') createStudioApp().init();
