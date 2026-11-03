import { escapeHtml } from './dom.js';

export function createGalleryScreen({
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
  const { document, confirm, window } = environment;
  const api = (...args) => client.request(...args);
  const { requestKey } = submissions;
  let renderRecords = [],
    selected = null,
    isRevising = false,
    historyRequest = 0,
    modalReturnFocus = null;

  async function loadRenders() {
    try {
      renderRecords = await api('/api/renders');
      drawGallery();
    } catch (e) {
      if (!config.login_required) toast(e.message, true);
    }
  }

  function drawGallery() {
    const filter = $('filter-status').value;
    const sku = $('filter-sku').value.toLowerCase();
    const records = renderRecords.filter(
      (r) =>
        (filter === 'all' || (filter === 'approved' && r.approved) || r.status === filter) &&
        `${r.sku} ${r.prompt}`.toLowerCase().includes(sku),
    );
    const container = $('gallery-content');
    container.innerHTML = '';
    if (!records.length) {
      container.innerHTML =
        '<div class="placeholder">No images match your filters. Create your first production job to populate the gallery.</div>';
      return;
    }
    for (const r of records) {
      const card = document.createElement('div');
      card.className = 'image-card';
      const statusLabel =
        r.status === 'completed'
          ? r.approved
            ? 'Approved'
            : 'Needs review'
          : r.status === 'failed'
            ? 'Failed'
            : r.status;
      const modelDisplay = r.model.replace('gpt-image-2.5-', '');
      card.innerHTML = `<div class="image-photo">${r.url ? `<img src="${r.url}" loading="lazy" alt="${escapeHtml(r.sku)} generated image">` : `<span>${r.status === 'failed' ? '⚠' : '◷'}</span>`}<span class="photo-status ${r.approved ? 'good' : r.status === 'failed' ? 'fail' : ''}">${escapeHtml(statusLabel)}</span></div>
        <div class="image-meta">
          <h3>${escapeHtml(r.sku)} · ${escapeHtml(r.kind.toUpperCase())}</h3>
          <div class="scene" title="${escapeHtml(r.prompt)}">${escapeHtml(r.prompt)}</div>
          <div class="details">${escapeHtml(r.user)} · ${escapeHtml(modelDisplay)} · ${escapeHtml(r.quality)}</div>
          <div class="card-buttons">${r.status === 'completed' ? `<button class="review-btn">Review / Revise</button><button class="approve-btn">${r.approved ? '✓ Approved' : '✓ Approve'}</button>` : `<small>${escapeHtml(r.error || 'Processing...')}</small>`}</div>
        </div>`;
      const im = card.querySelector('.image-photo img');
      if (im) im.addEventListener('click', () => openModal(r));
      card.querySelector('.review-btn')?.addEventListener('click', () => openModal(r));
      card.querySelector('.approve-btn')?.addEventListener('click', () => setApproval(r, !r.approved));
      container.append(card);
    }
  }

  async function setApproval(r, approved) {
    try {
      await api(`/api/renders/${r.id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved }),
      });
      r.approved = approved;
      toast(approved ? 'Image approved' : 'Approval removed');
      drawGallery();
      loadStats();
      if (selected?.id === r.id) showModalButton();
    } catch (e) {
      toast(e.message, true);
    }
  }

  function showModalButton() {
    $('approve-modal-btn').textContent = selected?.approved
      ? '✓ Approved (click to undo)'
      : '✓ Approve image';
  }

  function closeModal() {
    $('modal').classList.add('hidden');
    historyRequest++;
    if (modalReturnFocus?.isConnected) modalReturnFocus.focus();
  }

  function modalKeydown(e) {
    if ($('modal').classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeModal();
      return;
    }
    if (e.key !== 'Tab') return;
    const focusable = [
      ...$('modal').querySelectorAll(
        'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
      ),
    ].filter((el) => el.getClientRects().length);
    if (!focusable.length) {
      e.preventDefault();
      return;
    }
    const first = focusable[0],
      last = focusable[focusable.length - 1];
    if (e.shiftKey && (document.activeElement === first || !$('modal').contains(document.activeElement))) {
      e.preventDefault();
      last.focus();
    } else if (
      !e.shiftKey &&
      (document.activeElement === last || !$('modal').contains(document.activeElement))
    ) {
      e.preventDefault();
      first.focus();
    }
  }

  function openModal(r) {
    if ($('modal').classList.contains('hidden')) modalReturnFocus = document.activeElement;
    selected = r;
    $('remember-revision').checked = false;
    loadRevisionHistory(r.id);
    $('modal-title').textContent = `${r.sku} · ${r.kind.toUpperCase()}`;
    $('modal-img').src = r.url;
    $('modal-status').textContent =
      `${r.model} · ${r.quality} quality · ${r.approved ? 'Approved' : 'Not approved'}`;
    $('revision-prompt').value = '';
    $('download-modal').href = r.url;
    $('download-modal').download = `${r.sku}_${r.id.slice(0, 6)}.jpg`;
    showModalButton();
    $('modal').classList.remove('hidden');
    $('close-modal').focus();
  }

  async function revise() {
    if (!selected || isRevising) return;
    const text = $('revision-prompt').value.trim();
    if (text.length < 5) return toast('Please explain the correction first.', true);
    if (config.mode === 'live' && !confirm('Send a new PAID image edit request?')) return;
    const snapshot = {
      render_id: selected.id,
      instructions: text,
      model: $('revision-model').value,
      quality: $('revision-quality').value,
      remember_rule: $('remember-revision').checked,
    };
    const key = requestKey('revise', JSON.stringify(snapshot));
    isRevising = true;
    $('revise-btn').disabled = true;
    try {
      const result = await api('/api/revise', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify(snapshot),
      });
      submissions.clear('revise');
      if (selected?.id === snapshot.render_id) closeModal();
      toast(
        result.pending_rule
          ? 'Revision queued. The correction will be saved when you approve the result.'
          : result.saved_rule
            ? 'Revision queued and saved as permanent product rule'
            : result.mode === 'demo'
              ? 'Demo revision created'
              : 'Revision queued for generation',
      );
      setView('jobs');
      await loadJobs();
    } catch (e) {
      toast(e.message, true);
    } finally {
      isRevising = false;
      $('revise-btn').disabled = false;
    }
  }

  async function loadRevisionHistory(id) {
    const request = ++historyRequest;
    const el = $('revision-history');
    el.textContent = 'Loading version history...';
    try {
      const hist = await api(`/api/renders/${encodeURIComponent(id)}/history`);
      if (request !== historyRequest || selected?.id !== id) return;
      el.innerHTML = '<b>Revision history · ' + hist.length + ' version(s)</b>';
      for (const [i, v] of hist.entries()) {
        const row = document.createElement('div');
        row.className = 'revision-item';
        row.innerHTML = `<span>v${i + 1} · ${escapeHtml(v.user)} · ${new Date(v.created_at).toLocaleString()}</span><small>${escapeHtml(v.prompt)}</small>`;
        const rules = v.memory_snapshot?.rules || [];
        const tag = document.createElement('small');
        tag.textContent = `Memory applied: ${rules.length} saved SKU correction(s) · Brand: ${v.memory_snapshot?.branding?.name || 'none'}`;
        row.append(tag);
        if (v.url && v.id !== id) {
          const btn = document.createElement('button');
          btn.className = 'revision-link';
          btn.textContent = 'View this version';
          btn.addEventListener('click', async () => {
            await loadRenders();
            const rec = renderRecords.find((x) => x.id === v.id);
            if (rec) openModal(rec);
            else window.open(v.url, '_blank');
          });
          row.append(btn);
        }
        el.append(row);
      }
    } catch (e) {
      if (request === historyRequest) el.textContent = 'Could not load revision history: ' + e.message;
    }
  }

  function bind() {
    $('gallery-refresh').addEventListener('click', loadRenders);
    $('filter-status').addEventListener('change', drawGallery);
    $('filter-sku').addEventListener('input', drawGallery);
    $('close-modal').addEventListener('click', closeModal);
    $('modal').addEventListener('click', (e) => {
      if (e.target === $('modal')) closeModal();
    });
    document.addEventListener('keydown', modalKeydown);
    $('revise-btn').addEventListener('click', revise);
    $('approve-modal-btn').addEventListener(
      'click',
      () => selected && setApproval(selected, !selected.approved),
    );
  }

  return { bind, loadRenders, openModal, revise };
}
