export function createUsageScreen({ ui, client, environment = globalThis }) {
  const { $, toast } = ui;
  const { document, confirm } = environment;
  const api = (...args) => client.request(...args);

  let loading = false,
    saving = false,
    dirty = false,
    canEdit = false,
    loadVersion = 0;
  const currency = (value) =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: 2,
      maximumFractionDigits: 6,
    }).format(value);
  function controls() {
    for (const id of ['usage-checkpoint', 'usage-threshold', 'usage-save'])
      $(id).disabled = !canEdit || saving;
  }
  async function load(force = false) {
    if ((loading && !force) || saving) return;
    loading = true;
    const token = ++loadVersion;
    try {
      const data = await api('/api/usage');
      if (token !== loadVersion) return;
      canEdit = data.can_edit_budget;
      $('usage-spent').textContent = currency(data.tracked_spend_usd);
      $('usage-requests').textContent = data.requests.toLocaleString();
      $('usage-unreported').textContent = data.unreported.toLocaleString();
      $('usage-input').textContent = (data.image_input_tokens + data.text_input_tokens).toLocaleString();
      $('usage-output').textContent = (data.image_output_tokens + data.text_output_tokens).toLocaleString();
      $('usage-notice').textContent = data.notice;
      $('usage-pricing').textContent =
        `Rate card: ${data.rate_card_version}. ${data.pending} requests in progress. ` +
        (data.tracking_since
          ? 'Tracking since ' + new Date(data.tracking_since).toLocaleString() + '.'
          : 'Tracking begins with the next live request.');
      const budget = data.balance_estimate;
      $('usage-balance').textContent = budget ? currency(budget.estimated_remaining_usd) : 'Not configured';
      $('usage-alert').classList.toggle('warning', !!budget && (budget.low || budget.incomplete));
      $('usage-alert').textContent = !budget
        ? 'Set a checkpoint after checking the actual balance in OpenAI Billing.'
        : budget.incomplete
          ? `${budget.unreported_since_checkpoint} requests have unknown costs. This estimate is incomplete; check OpenAI Billing.`
          : budget.low
            ? 'Estimated balance is below your threshold. Check OpenAI Billing before submitting more jobs.'
            : `Approximately ${budget.remaining_percent}% of the checkpoint remains based on tracked usage.`;
      if (!dirty && !saving && budget) {
        $('usage-checkpoint').value = budget.balance_checkpoint_usd;
        $('usage-threshold').value = budget.alert_percent;
      }
      controls();
      $('usage-owner-note').textContent = data.can_edit_budget
        ? 'Wait for active requests to finish, then check Billing and save a new checkpoint.'
        : 'Only a workspace owner can update the checkpoint.';
      for (const [id, rows, key] of [
        ['usage-users', data.by_user, 'user'],
        ['usage-models', data.by_model, 'model'],
      ]) {
        const body = $(id);
        body.replaceChildren();
        for (const row of rows) {
          const tr = document.createElement('tr');
          for (const value of [row[key], row.requests, currency(row.tracked_usd), row.unreported]) {
            const td = document.createElement('td');
            td.textContent = value;
            tr.append(td);
          }
          body.append(tr);
        }
        if (!rows.length) {
          const tr = document.createElement('tr'),
            td = document.createElement('td');
          td.colSpan = 4;
          td.textContent = 'No live requests recorded yet';
          tr.append(td);
          body.append(tr);
        }
      }
    } catch (error) {
      if (token === loadVersion) toast('Usage: ' + error.message, true);
    } finally {
      if (token === loadVersion) loading = false;
    }
  }
  async function save() {
    if (saving || !canEdit) return;
    const balance = $('usage-checkpoint').value.trim(),
      percent = Number($('usage-threshold').value);
    if (
      !/^(?:0|[1-9]\d*)(?:\.\d{1,6})?$/.test(balance) ||
      Number(balance) > 10000000 ||
      !Number.isInteger(percent) ||
      percent < 1 ||
      percent > 90
    )
      return toast('Enter a valid USD balance and a threshold from 1–90%', true);
    if (
      !confirm(
        'Save this manually checked balance? This updates the local estimate; it does not add API credits.',
      )
    )
      return;
    saving = true;
    ++loadVersion;
    controls();
    try {
      await api('/api/usage/checkpoint', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ balance_checkpoint_usd: balance, alert_percent: percent }),
      });
      dirty = false;
      toast('Balance checkpoint saved');
      saving = false;
      await load(true);
    } catch (error) {
      toast(error.message, true);
    } finally {
      saving = false;
      loading = false;
      controls();
    }
  }
  function bind() {
    $('usage-refresh').addEventListener('click', () => load());
    $('usage-save').addEventListener('click', save);
    for (const id of ['usage-checkpoint', 'usage-threshold'])
      $(id).addEventListener('input', () => {
        dirty = true;
      });
  }
  return { bind, load, save };
}
