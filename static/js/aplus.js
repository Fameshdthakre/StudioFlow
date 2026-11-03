export function createAplusScreen({
  ui,
  client,
  config,
  submissions,
  setView,
  loadJobs,
  loadStats,
  environment = globalThis,
}) {
  const { $, toast } = ui;
  const { document, confirm, FormData } = environment;
  const api = (...args) => client.request(...args);
  const { requestKey, fileId } = submissions;

  let profiles = [],
    files = [],
    canEdit = false,
    busy = false,
    saving = false,
    dirty = false,
    loading = false,
    loadVersion = 0;
  const fields = {
    name: 'aplus-name',
    master_instructions: 'aplus-master',
    branding_notes: 'aplus-brand-notes',
    heading_font: 'aplus-heading-font',
    body_font: 'aplus-body-font',
    primary_color: 'aplus-primary',
    secondary_color: 'aplus-secondary',
    accent_color: 'aplus-accent',
  };
  let defaults = {
    name: '',
    master_instructions: '',
    branding_notes: '',
    heading_font: 'Cinzel',
    body_font: 'Roboto',
    primary_color: '#283F44',
    secondary_color: '#F4E7D9',
    accent_color: '#9E7655',
  };
  const lines = () =>
    $('aplus-scenes')
      .value.split('\n')
      .map((x) => x.trim())
      .filter(Boolean);
  const batchInstruction = () =>
    $('aplus-extra')
      .value.trim()
      .replace(/\s*\r?\n\s*/g, ' ');
  function recalc() {
    const count = files.length * lines().length;
    let error = '';
    if (loading) error = 'Loading A+ profiles…';
    else if (saving || dirty) error = 'Save profile changes before generating';
    else if (!$('aplus-profile').value) error = 'Select a saved A+ profile';
    else if (!files.length) error = 'Add at least one product photo';
    else if (!lines().length || lines().length > (config.max_scenes || 12))
      error = `Enter 1–${config.max_scenes || 12} scenes`;
    else if (
      lines().some((scene) => (scene + (batchInstruction() ? ' | ' + batchInstruction() : '')).length > 3500)
    )
      error = 'Keep each scene plus batch instruction under 3,500 characters';
    else if (count > (config.max_images || 250)) error = `Maximum ${config.max_images || 250} images per job`;
    else if (files.reduce((sum, p) => sum + p.file.size, 0) > 100 * 1024 * 1024)
      error = 'Use at most 100 MB of photos per job';
    $('aplus-count').textContent = count.toLocaleString();
    $('aplus-generate').disabled = busy || !!error;
    $('aplus-hint').textContent = busy
      ? 'Submitting your A+ job…'
      : error ||
        `${files.length} products · ${count} outputs · ${config.mode === 'live' ? 'Paid API requests' : 'Demo previews'}`;
    return error;
  }
  function controls() {
    for (const id of [
      ...Object.values(fields),
      'aplus-save',
      'aplus-new',
      'aplus-logo-file',
      'aplus-upload-logo',
    ])
      $(id).disabled = !canEdit || saving || loading;
    $('aplus-profile').disabled = saving || loading;
  }
  function showProfile() {
    const p = profiles.find((p) => p.id === $('aplus-profile').value) || defaults;
    for (const [key, id] of Object.entries(fields)) $(id).value = p[key] || '';
    controls();
    $('aplus-edit-note').textContent = canEdit
      ? 'Profile changes apply to new jobs. Revisions retain their original A+ profile.'
      : 'Profile settings are managed by a workspace owner.';
    $('aplus-logo').classList.toggle('hidden', !p.logo_url);
    if (p.logo_url) $('aplus-logo').src = p.logo_url + '?v=' + encodeURIComponent(p.updated_at);
    dirty = false;
    recalc();
  }
  async function load(focus = '') {
    if (dirty || saving) return;
    const token = ++loadVersion;
    loading = true;
    controls();
    recalc();
    try {
      const result = await api('/api/aplus/profiles');
      if (token !== loadVersion || dirty || saving) return;
      profiles = result.profiles;
      canEdit = result.can_edit;
      defaults.master_instructions = result.default_master;
      const prior = focus || $('aplus-profile').value;
      $('aplus-profile').replaceChildren();
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = 'Select an A+ profile';
      $('aplus-profile').append(blank);
      for (const profile of profiles) {
        const option = document.createElement('option');
        option.value = profile.id;
        option.textContent = profile.name;
        $('aplus-profile').append(option);
      }
      $('aplus-profile').value = profiles.some((p) => p.id === prior) ? prior : profiles[0]?.id || '';
      showProfile();
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (token === loadVersion) {
        loading = false;
        controls();
        recalc();
      }
    }
  }
  async function save() {
    if (!canEdit || saving || loading) return;
    const id = $('aplus-profile').value,
      body = {};
    for (const [key, input] of Object.entries(fields)) body[key] = $(input).value.trim();
    saving = true;
    controls();
    recalc();
    try {
      const result = await api('/api/aplus/profiles' + (id ? '/' + encodeURIComponent(id) : ''), {
        method: id ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      dirty = false;
      saving = false;
      toast('A+ profile saved');
      await load(result.id);
    } catch (error) {
      toast(error.message, true);
    } finally {
      saving = false;
      controls();
      recalc();
    }
  }
  async function uploadLogo() {
    const id = $('aplus-profile').value,
      file = $('aplus-logo-file').files[0];
    if (!canEdit || saving || loading) return;
    if (!id || !file) return toast('Save a profile and choose its logo first', true);
    if (file.size > 5 * 1024 * 1024) return toast('Logo must be under 5 MB', true);
    const data = new FormData();
    data.append('file', file);
    saving = true;
    controls();
    recalc();
    try {
      await api('/api/aplus/profiles/' + encodeURIComponent(id) + '/logo', { method: 'POST', body: data });
      toast('Logo reference saved');
      saving = false;
      if (!dirty && $('aplus-profile').value === id) await load(id);
    } catch (error) {
      toast(error.message, true);
    } finally {
      saving = false;
      controls();
      recalc();
    }
  }
  function drawFiles() {
    const box = $('aplus-file-list');
    box.replaceChildren();
    for (const item of files) {
      const row = document.createElement('div');
      row.className = 'aplus-photo-line';
      const label = document.createElement('span');
      label.textContent = item.file.name;
      const input = document.createElement('input');
      input.value = item.sku;
      input.setAttribute('aria-label', 'SKU for ' + item.file.name);
      input.addEventListener('input', (event) => {
        item.sku = event.target.value;
        item.edited = true;
      });
      const match = document.createElement('small');
      match.textContent = item.matched ? 'Catalog matched' : 'Enter SKU / ASIN / item code';
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'secondary-btn';
      remove.textContent = 'Remove';
      remove.addEventListener('click', () => {
        files = files.filter((p) => p !== item);
        drawFiles();
      });
      row.append(label, input, match, remove);
      box.append(row);
    }
    recalc();
  }
  async function addFiles(incoming) {
    const added = [];
    for (const file of incoming) {
      if (files.length >= (config.max_products || 50)) {
        toast(`Maximum ${config.max_products || 50} photos`, true);
        break;
      }
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 18 * 1024 * 1024) {
        toast('Use PNG, JPG or WEBP photos under 18 MB', true);
        continue;
      }
      const item = {
        file,
        sku: file.name
          .replace(/\.[^.]+$/, '')
          .replace(/[^A-Za-z0-9_-]/g, '_')
          .slice(0, 60),
        edited: false,
        matched: false,
      };
      files.push(item);
      added.push(item);
    }
    drawFiles();
    if (!added.length) return;
    try {
      const result = await api('/api/catalog/match-files', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filenames: added.map((p) => p.file.name) }),
      });
      for (const item of added) {
        const match = result.matches?.[item.file.name];
        if (files.includes(item) && !item.edited && match?.matched) {
          item.sku = match.sku;
          item.matched = true;
        }
      }
      drawFiles();
    } catch (error) {
      toast('Catalog matching: ' + error.message, true);
    }
  }
  async function generate() {
    if (busy) return;
    const error = recalc();
    if (error) return toast(error, true);
    const extra = batchInstruction();
    const snapshot = {
      kind: 'aplus',
      aplus_profile_id: $('aplus-profile').value,
      scenes: lines()
        .map((s) => (extra ? s + ' | ' + extra : s))
        .join('\n'),
      model: $('aplus-model').value,
      quality: $('aplus-quality').value,
      canvas: $('aplus-canvas').value,
      products: files.map((p) => ({ file: p.file, sku: p.sku })),
    };
    if (
      config.mode === 'live' &&
      !confirm(`Submit ${snapshot.products.length * lines().length} PAID A+ generations?`)
    )
      return;
    const fingerprint = JSON.stringify({
      ...snapshot,
      products: snapshot.products.map((p) => {
        return { file: fileId(p.file), sku: p.sku };
      }),
    });
    const key = requestKey('aplus', fingerprint),
      data = new FormData();
    for (const p of snapshot.products) data.append('files', p.file, p.file.name);
    data.append('skus', JSON.stringify(snapshot.products.map((p) => p.sku)));
    for (const field of ['kind', 'aplus_profile_id', 'scenes', 'model', 'quality', 'canvas'])
      data.append(field, snapshot[field]);
    busy = true;
    recalc();
    try {
      const result = await api('/api/generate', {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: data,
      });
      submissions.clear('aplus');
      toast(`${result.images_queued} A+ backgrounds queued`);
      setView('jobs');
      await Promise.all([loadJobs(), loadStats()]);
    } catch (error) {
      toast(error.message, true);
    } finally {
      busy = false;
      recalc();
    }
  }
  function bind() {
    $('aplus-profile').addEventListener('change', () => {
      if (!saving && !loading) showProfile();
    });
    $('aplus-new').addEventListener('click', () => {
      if (!canEdit || saving || loading) return;
      $('aplus-profile').value = '';
      showProfile();
      dirty = true;
      recalc();
      $('aplus-name').focus();
    });
    for (const id of Object.values(fields))
      $(id).addEventListener('input', () => {
        dirty = true;
        recalc();
      });
    $('aplus-save').addEventListener('click', save);
    $('aplus-upload-logo').addEventListener('click', uploadLogo);
    $('aplus-files').addEventListener('change', (event) => {
      addFiles([...event.target.files]);
      event.target.value = '';
    });
    const zone = $('aplus-drop');
    for (const name of ['dragenter', 'dragover'])
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.add('dragging');
      });
    for (const name of ['dragleave', 'drop'])
      zone.addEventListener(name, (event) => {
        event.preventDefault();
        zone.classList.remove('dragging');
      });
    zone.addEventListener('drop', (event) => addFiles([...event.dataTransfer.files]));
    $('aplus-scenes').value =
      'Hero banner with the exact product and clear space for a headline\nClose detail showing genuine materials, with room for feature callouts';
    $('aplus-scenes').addEventListener('input', recalc);
    $('aplus-extra').addEventListener('input', recalc);
    $('aplus-generate').addEventListener('click', generate);
    recalc();
  }
  return { bind, load, generate, recalc, addFiles };
}
