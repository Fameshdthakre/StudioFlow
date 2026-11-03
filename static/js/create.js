import { escapeHtml } from './dom.js';

export function createImageScreen({
  ui,
  client,
  config,
  submissions,
  setView,
  loadJobs,
  loadStats,
  openMemory,
  environment = globalThis,
}) {
  const { $, toast } = ui;
  const { document, URL, FormData, confirm } = environment;
  const api = (...args) => client.request(...args);
  const { requestKey, fileId } = submissions;
  let products = [],
    kind = 'lifestyle',
    isSubmitting = false;
  const SCENES = {
    lifestyle: [
      'Styled naturally in a bright contemporary living room with realistic morning daylight, product clearly visible.',
      'Elegant warm-toned home interior with tasteful furnishings, a close realistic product composition.',
      'Detailed close-up showing the genuine product texture and construction in a relevant environment.',
      'Premium lifestyle setting with natural shadows, professional commercial photography and true-to-life scale.',
      'Seasonally appropriate display with uncluttered composition, accurate materials and natural depth of field.',
    ],
    main: ['Pure white background, full product visible, centered, no props or added decoration.'],
    infographic: [
      'Product centered with clean negative space on the right for adding accurate text in post-processing.',
      'Product detail close-up with clean background and generous negative space for feature callouts.',
      'Product scale and size presentation, no rendered numbers or text; leave space for exact dimensions.',
    ],
  };
  const TIP = {
    lifestyle:
      'Tip: Each line becomes a separate lifestyle setup. Describe settings relevant to this particular product.',
    main: 'MAIN images must be checked against Amazon category policies. Use the genuine product on pure white, without extra props.',
    infographic:
      'For sharp Cinzel/Roboto headings, generate the scene without words and add exact typography later in a graphic editor.',
  };

  function sceneCount() {
    return $('scenes')
      .value.split('\n')
      .filter((x) => x.trim()).length;
  }

  function assignedCount() {
    return products.reduce(
      (sum, p) =>
        sum + (p.scenes?.trim() ? p.scenes.split('\n').filter((x) => x.trim()).length : sceneCount()),
      0,
    );
  }

  function invalidScenes() {
    return products.some(
      (p) =>
        p.scenes?.trim() && p.scenes.split('\n').filter((x) => x.trim()).length > (config.max_scenes || 12),
    );
  }

  function generationError() {
    const limit = config.max_images || 250;
    if (!products.length) return 'Add at least 1 photo to begin';
    if (products.length > (config.max_products || 50))
      return `Use at most ${config.max_products || 50} photos per job`;
    if (!sceneCount() || sceneCount() > (config.max_scenes || 12) || invalidScenes())
      return `Enter 1–${config.max_scenes || 12} scenes per product`;
    if (assignedCount() > limit)
      return `Maximum ${limit} images per job. Remove photos or reduce scene counts.`;
    return '';
  }

  function recalc() {
    const n = sceneCount();
    $('scene-count').textContent = n + ' scene' + (n !== 1 ? 's' : '');
    $('estimated-count').textContent = assignedCount().toLocaleString('en-IN');
    const error = generationError();
    $('generate-btn').disabled = isSubmitting || !!error;
    $('generation-hint').textContent = isSubmitting
      ? 'Submitting your image job…'
      : error ||
        `${products.length} product(s) · ${assignedCount()} images · ${config.mode === 'live' ? 'Paid API requests' : 'Free demo previews'}`;
  }

  function showProducts() {
    const container = $('product-list');
    container.innerHTML = '';
    if (!products.length) {
      container.innerHTML = '<div class="empty-sm">No products uploaded yet</div>';
      $('match-summary-pill').classList.add('hidden');
      recalc();
      return;
    }
    for (const item of products) {
      const row = document.createElement('div');
      row.className = 'product-row';
      const m = item.matchedInfo;
      const matchBadge = m
        ? `<div class="match-badge matched" title="${escapeHtml(m.title || '')}">✓ Matched: ${escapeHtml(m.sku)} ${m.asin ? `(${escapeHtml(m.asin)})` : ''} ${m.colour ? `· ${escapeHtml(m.colour)}` : ''} <small>via ${escapeHtml(m.method || '')}</small></div>`
        : `<div class="match-badge unmatched">Filename used as SKU · not in catalog</div>`;
      row.innerHTML = `<img src="${item.url}" alt="Product photograph">
        <div>
          <div class="product-meta-row">
            <small>${escapeHtml(item.file.name)} · ${(item.file.size / 1024 / 1024).toFixed(1)} MB</small>
            ${matchBadge}
          </div>
          <input aria-label="SKU" placeholder="Enter item code / ASIN / SKU" value="${escapeHtml(item.sku)}">
          <textarea class="product-scene-override" rows="2" placeholder="Optional: custom scenes for this product, one per line. Blank uses default scenes.">${escapeHtml(item.scenes || '')}</textarea>
        </div>
        <button title="Remove photograph">×</button>`;
      row.querySelector('input').addEventListener('input', (e) => {
        item.sku = e.target.value;
        item.skuEdited = true;
        item.matchedInfo = null;
      });
      row.querySelector('textarea').addEventListener('input', (e) => {
        item.scenes = e.target.value;
        recalc();
      });
      row.querySelector('button').addEventListener('click', () => {
        URL.revokeObjectURL(item.url);
        products = products.filter((p) => p !== item);
        showProducts();
      });
      container.append(row);
    }
    recalc();
  }

  async function addFiles(files) {
    const added = [];
    for (const file of files) {
      if (products.length >= (config.max_products || 50)) {
        toast(`Maximum ${config.max_products || 50} product photos per job.`, true);
        break;
      }
      if (!file.type.startsWith('image/')) {
        toast(`Unsupported file: ${file.name}`, true);
        continue;
      }
      if (file.size > 18 * 1024 * 1024) {
        toast(`Too large: ${file.name}`, true);
        continue;
      }
      const sku = file.name
        .replace(/\.[^.]+$/, '')
        .replace(/[^\w-]/g, '_')
        .slice(0, 40);
      const product = { file, sku, url: URL.createObjectURL(file), matchedInfo: null, skuEdited: false };
      products.push(product);
      added.push(product);
    }
    showProducts();
    if (added.length) {
      await matchProductsWithCatalog(added);
    }
  }

  async function matchProductsWithCatalog(candidates = products.filter((p) => !p.skuEdited)) {
    const untouched = candidates.filter((p) => !p.skuEdited);
    if (!untouched.length) return;
    const filenames = untouched.map((p) => p.file.name);
    try {
      const res = await api('/api/catalog/match-files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filenames }),
      });
      for (const p of untouched) {
        if (!products.includes(p) || p.skuEdited) continue;
        const match = res.matches?.[p.file.name];
        if (match && match.matched) {
          p.sku = match.sku;
          p.matchedInfo = match;
        }
      }
      const matchedCount = products.filter((p) => p.matchedInfo && !p.skuEdited).length;
      const pill = $('match-summary-pill');
      if (matchedCount > 0) {
        pill.classList.remove('hidden');
        pill.innerHTML = `<span>✓ <b>${matchedCount} of ${products.length}</b> photos auto-matched to catalog specifications by filename.</span> <button class="ghost-btn" style="padding:4px 8px;margin:0;font-size:10px">View Catalog →</button>`;
        pill.querySelector('button').addEventListener('click', () => setView('catalog'));
      } else {
        pill.classList.add('hidden');
      }
      showProducts();
    } catch (e) {
      console.error('Matching error:', e);
    }
  }

  async function generate() {
    if (isSubmitting) return;
    const error = generationError();
    if (error) return toast(error, true);
    const count = assignedCount();
    if (
      config.mode === 'live' &&
      count > 10 &&
      !confirm(`Submit ${count} PAID image generations? Every generation/edit may incur API fees.`)
    )
      return;
    const snapshot = {
      products: products.map((p) => ({ file: p.file, sku: p.sku, scenes: p.scenes || '' })),
      kind,
    };
    for (const field of ['scenes', 'model', 'quality', 'canvas']) snapshot[field] = $(field).value;
    const fingerprint = JSON.stringify({
      ...snapshot,
      products: snapshot.products.map((p) => {
        return { file: fileId(p.file), sku: p.sku, scenes: p.scenes };
      }),
    });
    const key = requestKey('generate', fingerprint);
    const payload = new FormData();
    for (const p of snapshot.products) payload.append('files', p.file, p.file.name);
    payload.append('skus', JSON.stringify(snapshot.products.map((x) => x.sku)));
    payload.append('scene_sets', JSON.stringify(snapshot.products.map((x) => x.scenes)));
    for (const field of ['scenes', 'model', 'quality', 'canvas']) payload.append(field, snapshot[field]);
    payload.append('kind', snapshot.kind);
    isSubmitting = true;
    recalc();
    $('generate-btn').textContent = 'Submitting...';
    try {
      const result = await api('/api/generate', {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: payload,
      });
      submissions.clear('generate');
      toast(
        `${result.images_queued} images queued · ${result.mode === 'demo' ? 'demo previews only' : 'live API generation'}`,
      );
      setView('jobs');
      await Promise.all([loadJobs(), loadStats()]);
    } catch (e) {
      toast(e.message, true);
    } finally {
      isSubmitting = false;
      $('generate-btn').innerHTML = '✦ Generate images <span>→</span>';
      recalc();
    }
  }

  function bind() {
    const zone = $('drop-zone');
    $('photos').addEventListener('change', (e) => {
      addFiles([...e.target.files]);
      e.target.value = '';
    });
    for (const event of ['dragenter', 'dragover'])
      zone.addEventListener(event, (e) => {
        e.preventDefault();
        zone.classList.add('dragging');
      });
    for (const event of ['dragleave', 'drop'])
      zone.addEventListener(event, (e) => {
        e.preventDefault();
        zone.classList.remove('dragging');
      });
    zone.addEventListener('drop', (e) => addFiles([...e.dataTransfer.files]));
    for (const button of document.querySelectorAll('.type-option'))
      button.addEventListener('click', () => {
        kind = button.dataset.kind;
        for (const option of document.querySelectorAll('.type-option'))
          option.classList.toggle('selected', option === button);
        $('scenes').value = SCENES[kind].join('\n');
        $('task-tip').textContent = TIP[kind];
        recalc();
      });
    $('scenes').value = SCENES.lifestyle.join('\n');
    $('scenes').addEventListener('input', recalc);
    $('generate-btn').addEventListener('click', generate);
    $('open-memory').addEventListener('click', () => openMemory(products[0]?.sku));
  }

  return { bind, addFiles, matchProductsWithCatalog, generate, recalc };
}
