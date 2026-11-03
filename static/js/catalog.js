import { escapeHtml } from './dom.js';

export function createCatalogScreen({ ui, client, loadStats, environment = globalThis }) {
  const { $, toast } = ui;
  const { FormData, confirm, setTimeout, clearTimeout } = environment;
  const api = (...args) => client.request(...args);
  let catalogProducts = [],
    catalogRequest = 0;

  async function loadCatalog(q = '') {
    const request = ++catalogRequest;
    try {
      const data = await api('/api/catalog?q=' + encodeURIComponent(q));
      if (request !== catalogRequest) return;
      catalogProducts = data.products || [];
      $('catalog-total-badge').textContent = data.total || 0;
      $('catalog-count').textContent = data.total || 0;
      const wrap = $('catalog-table-wrap');
      if (!catalogProducts.length) {
        wrap.innerHTML =
          '<div class="empty-sm">No products in catalog yet. Download the CSV template above and upload your sheet.</div>';
        return;
      }
      let html =
        '<table class="cat-table"><thead><tr><th>SKU / ASIN</th><th>Title</th><th>Specs & Variation</th><th>Bullets & Highlight</th><th>Action</th></tr></thead><tbody>';
      for (const p of catalogProducts) {
        const bullets = [p.bullet_1, p.bullet_2, p.bullet_3, p.bullet_4, p.bullet_5].filter(Boolean);
        html += `<tr>
          <td>
            <strong>${escapeHtml(p.sku)}</strong>
            ${p.asin ? `<br><small class="cat-badge">ASIN: ${escapeHtml(p.asin)}</small>` : ''}
            ${p.item_code ? `<br><small class="cat-badge">Code: ${escapeHtml(p.item_code)}</small>` : ''}
          </td>
          <td><b>${escapeHtml(p.title || 'Untitled')}</b></td>
          <td>
            ${p.colour ? `<span class="cat-badge">Colour: ${escapeHtml(p.colour)}</span>` : ''}
            ${p.size ? `<span class="cat-badge">Size: ${escapeHtml(p.size)}</span>` : ''}
            ${p.variation ? `<br><small style="color:#677d8c">${escapeHtml(p.variation)}</small>` : ''}
          </td>
          <td>
            ${p.item_highlight ? `<div style="margin-bottom:4px;color:#186854"><b>Highlight:</b> ${escapeHtml(p.item_highlight)}</div>` : ''}
            ${bullets.length ? `<small style="color:#728394">• ${escapeHtml(bullets.slice(0, 2).join(' • '))}${bullets.length > 2 ? ` (+${bullets.length - 2} more)` : ''}</small>` : ''}
          </td>
          <td>
            <button class="cat-del-btn" title="Delete product" data-product-id="${escapeHtml(p.id)}">🗑</button>
          </td>
        </tr>`;
      }
      html += '</tbody></table>';
      wrap.innerHTML = html;
      for (const button of wrap.querySelectorAll('[data-product-id]'))
        button.addEventListener('click', () => deleteCatalogItem(button.dataset.productId));
    } catch (e) {
      if (request === catalogRequest) toast('Catalog: ' + e.message, true);
    }
  }

  async function deleteCatalogItem(id) {
    if (!confirm('Delete this product from catalog?')) return;
    try {
      await api('/api/catalog/' + encodeURIComponent(id), { method: 'DELETE' });
      toast('Product deleted from catalog');
      await loadCatalog($('catalog-search-input').value);
      await loadStats();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function uploadCatalogCsv(file) {
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    const statusBox = $('csv-status');
    statusBox.classList.remove('hidden');
    statusBox.textContent = 'Importing product data and updating Product Memory...';
    try {
      const res = await api('/api/catalog/import', { method: 'POST', body: form });
      statusBox.textContent = `✓ Success! Imported ${res.imported} new products, updated ${res.updated}. Total in catalog: ${res.total}.`;
      toast(`Catalog imported: ${res.imported} new, ${res.updated} updated`);
      await loadCatalog();
      await loadStats();
    } catch (e) {
      statusBox.textContent = '⚠ Import failed: ' + e.message;
      toast(e.message, true);
    }
  }

  function bind() {
    const zone = $('csv-drop-zone');
    $('csv-file').addEventListener('change', (e) => {
      if (e.target.files[0]) uploadCatalogCsv(e.target.files[0]);
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
    zone.addEventListener('drop', (e) => {
      if (e.dataTransfer.files[0]) uploadCatalogCsv(e.dataTransfer.files[0]);
    });
    let searchTimer;
    $('catalog-search-input').addEventListener('input', (e) => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => loadCatalog(e.target.value), 300);
    });
  }

  return { bind, loadCatalog, uploadCatalogCsv };
}
