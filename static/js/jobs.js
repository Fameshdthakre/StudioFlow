import { escapeHtml } from './dom.js';

export function createJobsScreen({ ui, client, config, environment = globalThis }) {
  const { $, toast } = ui;
  const { document } = environment;
  const api = (...args) => client.request(...args);
  let jobRecords = [];

  async function loadJobs() {
    try {
      jobRecords = await api('/api/jobs');
      drawJobs();
    } catch (e) {
      if (!config.login_required) toast(e.message, true);
    }
  }

  function drawJobs() {
    const el = $('queue-list');
    el.innerHTML = '';
    if (!jobRecords.length) {
      el.innerHTML =
        '<div class="placeholder">Your production queue is empty. Start by generating images.</div>';
      return;
    }
    for (const j of jobRecords) {
      const percent = Math.round(((j.completed + j.failed) / Math.max(1, j.total)) * 100);
      const card = document.createElement('div');
      card.className = 'queue-card';
      card.innerHTML = `<div class="queue-head"><div><strong>${escapeHtml(j.kind.toUpperCase())} · ${j.total} images</strong><small>Created by ${escapeHtml(j.user)} · ${new Date(j.created_at).toLocaleString()}</small></div><div><b>${j.completed}/${j.total}</b> <small>completed</small></div></div><div class="progress"><div style="width:${percent}%"></div></div><p>${j.failed ? `${j.failed} failed · ` : ''}${percent === 100 ? 'Job finished' : 'Processing in queue'} · ${percent}% done</p>`;
      el.append(card);
    }
  }

  function bind() {
    $('queue-refresh').addEventListener('click', loadJobs);
  }

  return { bind, loadJobs };
}
