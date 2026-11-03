export const escapeHtml = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char],
  );

export function createUi(environment = globalThis) {
  const { document, setTimeout } = environment;
  const $ = (id) => document.getElementById(id);
  function toast(message, bad = false) {
    const element = document.createElement('div');
    element.className = 'toast' + (bad ? ' bad' : '');
    element.textContent = message;
    $('toasts').append(element);
    setTimeout(() => element.remove(), 5500);
  }
  return { $, toast };
}
