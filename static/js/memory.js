import { escapeHtml } from './dom.js';

export function createMemoryScreen({ ui, client, environment = globalThis }) {
  const { $, toast } = ui;
  const { document, confirm, FormData } = environment;
  const api = (...args) => client.request(...args);
  let currentMemorySku = '',
    currentMemory = null,
    brandProfiles = [],
    brandEditorNew = false,
    memoryRequest = 0,
    brandRequest = 0;

  async function loadMemoryHome() {
    try {
      const company = await api('/api/memory/company');
      $('company-rules').value = company.instructions;
      $('company-rules').disabled = !company.can_edit;
      $('company-save').disabled = !company.can_edit;
      $('company-edit-note').textContent = company.can_edit
        ? 'Owner access: Changes apply to NEW image jobs for all team members.'
        : 'Read-only: Ask your workspace owner to change company instructions.';
      const saved = await api('/api/memory/products');
      const area = $('memory-products');
      area.innerHTML = '';
      if (!saved.length)
        area.innerHTML =
          '<div class="empty-sm">No product facts saved yet. Start with a SKU above or import catalog CSV.</div>';
      for (const product of saved) {
        const b = document.createElement('button');
        b.className = 'memory-product-btn';
        b.textContent = `${product.sku}${product.brand_name ? ' · ' + product.brand_name : ''} · ${product.rule_count} rule(s)`;
        b.onclick = () => openProductMemory(product.sku);
        area.append(b);
      }
    } catch (e) {
      toast('Memory: ' + e.message, true);
    }
  }

  async function openProductMemory(sku) {
    const key = String(sku || '')
      .trim()
      .toUpperCase();
    if (!key) return toast('Enter an SKU / ASIN', true);
    const request = ++memoryRequest;
    try {
      const record = await api('/api/memory/products/' + encodeURIComponent(key));
      if (request !== memoryRequest) return;
      currentMemorySku = record.sku;
      currentMemory = record;
      $('memory-sku').value = record.sku;
      $('memory-current-sku').textContent = record.sku;
      $('memory-facts').value = record.facts;
      $('catalog-facts').textContent = record.catalog_facts || '';
      $('catalog-facts-panel').classList.toggle('hidden', !record.catalog_facts);
      $('memory-updated').textContent = record.updated_by ? `Saved by ${record.updated_by}` : 'New product';
      $('memory-details').classList.remove('hidden');
      await refreshBrands(record.brand_id || '');
      if (request !== memoryRequest) return;
      const el = $('memory-rule-list');
      el.innerHTML = '';
      if (!record.rules.length)
        el.innerHTML = '<div class="empty-sm">No permanent corrections saved for this SKU.</div>';
      for (const rule of record.rules) {
        const row = document.createElement('div');
        row.className = 'memory-rule';
        const content = document.createElement('div');
        const t = document.createElement('strong');
        t.textContent = rule.instruction;
        const sub = document.createElement('small');
        sub.textContent = `Added by ${rule.created_by}`;
        content.append(t, sub);
        row.append(content);
        if (rule.can_delete) {
          const b = document.createElement('button');
          b.className = 'rule-delete';
          b.textContent = 'Remove';
          b.addEventListener('click', async () => {
            if (!confirm('Remove this rule for future generations?')) return;
            try {
              await api(`/api/memory/products/${encodeURIComponent(record.sku)}/rules/${rule.id}`, {
                method: 'DELETE',
              });
              if (currentMemorySku === record.sku) await openProductMemory(record.sku);
              toast('Rule removed.');
            } catch (e) {
              toast(e.message, true);
            }
          });
          row.append(b);
        }
        el.append(row);
      }
    } catch (e) {
      if (request === memoryRequest) toast(e.message, true);
    }
  }

  async function refreshBrands(focusId = '') {
    const request = ++brandRequest,
      sku = currentMemorySku,
      memoryVersion = memoryRequest;
    const profiles = await api('/api/memory/brands');
    if (request !== brandRequest || sku !== currentMemorySku || memoryVersion !== memoryRequest) return;
    brandProfiles = profiles;
    const selector = $('memory-brand-select');
    selector.innerHTML = '<option value="">— No brand selected —</option>';
    for (const b of brandProfiles) {
      const opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = b.name;
      selector.append(opt);
    }
    const assigned = currentMemory?.brand_id || '';
    selector.value = focusId || assigned;
    if (selector.value !== String(focusId || assigned)) selector.value = '';
    brandEditorNew = false;
    drawBrandProfile();
  }

  function drawBrandProfile() {
    const bid = $('memory-brand-select').value;
    const b = brandEditorNew ? null : brandProfiles.find((x) => x.id === bid);
    const editable = !!currentMemory?.can_manage_branding;
    const values = {
      name: '',
      heading_font: 'Cinzel',
      body_font: 'Roboto',
      primary_color: '#233A54',
      secondary_color: '#F2EEE8',
      accent_color: '#B98E5C',
      visual_style: '',
      brand_rules: '',
      ...(b || {}),
    };
    for (const [id, key] of Object.entries({
      'brand-name': 'name',
      'brand-heading-font': 'heading_font',
      'brand-body-font': 'body_font',
      'brand-primary-color': 'primary_color',
      'brand-secondary-color': 'secondary_color',
      'brand-accent-color': 'accent_color',
      'brand-style': 'visual_style',
      'brand-rules': 'brand_rules',
    })) {
      $(id).value = values[key] || '';
      $(id).disabled = !editable;
    }
    const url = b?.logo_url || '';
    $('brand-logo-preview').classList.toggle('hidden', !url);
    $('brand-logo-empty').classList.toggle('hidden', !!url);
    if (url) $('brand-logo-preview').src = url + '?v=' + encodeURIComponent(b.updated_at || '');
    else $('brand-logo-preview').removeAttribute('src');
    $('memory-brand-assign').disabled = !editable;
    $('memory-brand-new').disabled = !editable;
    $('brand-save-profile').disabled = !editable;
    $('brand-logo-file').disabled = !editable || !b;
    $('brand-upload-logo').disabled = !editable || !b;
    $('brand-access-note').textContent = editable
      ? brandEditorNew
        ? 'Save to create this brand and assign it to your SKU.'
        : 'Changes to this brand affect all SKUs assigned to it.'
      : 'Read-only profile. Ask your workspace owner to update brand rules.';
    const assigned = brandProfiles.find((x) => x.id === currentMemory?.brand_id);
    $('brand-assigned-status').textContent = assigned ? 'Assigned: ' + assigned.name : 'No brand assigned';
  }

  function newBrandProfile() {
    if (!currentMemory?.can_manage_branding) return;
    $('memory-brand-select').value = '';
    brandEditorNew = true;
    drawBrandProfile();
    $('brand-name').focus();
  }

  function currentBrandBody() {
    return {
      name: $('brand-name').value.trim(),
      heading_font: $('brand-heading-font').value.trim(),
      body_font: $('brand-body-font').value.trim(),
      primary_color: $('brand-primary-color').value,
      secondary_color: $('brand-secondary-color').value,
      accent_color: $('brand-accent-color').value,
      visual_style: $('brand-style').value.trim(),
      brand_rules: $('brand-rules').value.trim(),
    };
  }

  async function saveBrandProfile() {
    if (!currentMemorySku) return toast('Open a product first', true);
    const sku = currentMemorySku;
    const bid = brandEditorNew ? '' : $('memory-brand-select').value;
    const body = currentBrandBody();
    if (body.name.length < 2) return toast('Enter a brand name', true);
    try {
      const record = await api(bid ? `/api/memory/brands/${encodeURIComponent(bid)}` : '/api/memory/brands', {
        method: bid ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const savedId = record.id;
      if (!bid) {
        await api(`/api/memory/products/${encodeURIComponent(sku)}/brand`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ brand_id: savedId }),
        });
        if (currentMemorySku === sku) currentMemory.brand_id = savedId;
      }
      if (currentMemorySku === sku) await refreshBrands(savedId);
      await loadMemoryHome();
      toast(bid ? 'Brand profile updated for all assigned SKUs' : 'Brand created and assigned to ' + sku);
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function assignProductBrand() {
    if (!currentMemorySku) return toast('Open a product first', true);
    if (brandEditorNew) return toast('Save your new brand before assigning it.', true);
    const bid = $('memory-brand-select').value;
    const sku = currentMemorySku;
    try {
      await api(`/api/memory/products/${encodeURIComponent(sku)}/brand`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ brand_id: bid || null }),
      });
      if (currentMemorySku === sku) {
        currentMemory.brand_id = bid;
        drawBrandProfile();
      }
      await loadMemoryHome();
      toast(bid ? 'Brand assigned to ' + sku : 'Brand removed from SKU.');
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function uploadBrandLogo() {
    const bid = $('memory-brand-select').value;
    const sku = currentMemorySku;
    const file = $('brand-logo-file').files[0];
    if (!bid || brandEditorNew) return toast('Save a brand profile before uploading a logo', true);
    if (!file) return toast('Choose a logo file first', true);
    const form = new FormData();
    form.append('file', file);
    try {
      await api(`/api/memory/brands/${encodeURIComponent(bid)}/logo`, { method: 'POST', body: form });
      if (currentMemorySku === sku && $('memory-brand-select').value === bid) {
        $('brand-logo-file').value = '';
        await refreshBrands(bid);
      }
      toast('Logo stored in brand profile');
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function saveProductFacts() {
    if (!currentMemorySku) return toast('Open a product first', true);
    const sku = currentMemorySku,
      facts = $('memory-facts').value;
    try {
      await api('/api/memory/products/' + encodeURIComponent(sku), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ facts }),
      });
      toast('Product facts saved');
      if (currentMemorySku === sku) await openProductMemory(sku);
      await loadMemoryHome();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function saveProductRule() {
    if (!currentMemorySku) return toast('Open a product first', true);
    const v = $('memory-new-rule').value.trim();
    if (v.length < 5) return toast('Enter a meaningful correction', true);
    const sku = currentMemorySku;
    try {
      await api('/api/memory/products/' + encodeURIComponent(sku) + '/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instruction: v }),
      });
      if (currentMemorySku === sku) {
        $('memory-new-rule').value = '';
        await openProductMemory(sku);
      }
      toast('Permanent correction saved for ' + sku);
      await loadMemoryHome();
    } catch (e) {
      toast(e.message, true);
    }
  }

  async function saveCompanyRules() {
    const text = $('company-rules').value.trim();
    if (text.length < 15) return toast('Instructions are too short', true);
    if (!confirm('Apply updated master rules to all FUTURE image requests?')) return;
    try {
      await api('/api/memory/company', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ instructions: text }),
      });
      toast('Company-wide rules saved');
    } catch (e) {
      toast(e.message, true);
    }
  }

  function bind() {
    $('memory-load').addEventListener('click', () => openProductMemory($('memory-sku').value));
    $('memory-sku').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') openProductMemory(e.target.value);
    });
    $('memory-save-facts').addEventListener('click', saveProductFacts);
    $('memory-add-rule').addEventListener('click', saveProductRule);
    $('company-save').addEventListener('click', saveCompanyRules);
    $('memory-brand-select').addEventListener('change', () => {
      brandEditorNew = false;
      drawBrandProfile();
    });
    $('memory-brand-assign').addEventListener('click', assignProductBrand);
    $('memory-brand-new').addEventListener('click', newBrandProfile);
    $('brand-save-profile').addEventListener('click', saveBrandProfile);
    $('brand-upload-logo').addEventListener('click', uploadBrandLogo);
  }

  return { bind, loadMemoryHome, openProductMemory };
}
