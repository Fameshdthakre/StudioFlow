/** HTTP errors and submission identities shared by every screen. */
export function createApi({ config, ui, fetch = globalThis.fetch }) {
  const { $ } = ui;
  async function request(path, options = {}) {
    const res = await fetch(path, { credentials: 'same-origin', ...options });
    let json;
    try {
      json = await res.json();
    } catch {
      json = { detail: 'Could not read server response' };
    }
    if (!res.ok) {
      if (res.status === 401 && config.login_required) $('login-overlay').classList.remove('hidden');
      throw new Error(typeof json.detail === 'string' ? json.detail : 'Request failed');
    }
    return json;
  }
  return { request };
}

export function createSubmissions(crypto = globalThis.crypto) {
  const pendingRequests = {},
    fileIds = new WeakMap();
  let nextFileId = 0;
  function requestKey(operation, fingerprint) {
    if (pendingRequests[operation]?.fingerprint === fingerprint) return pendingRequests[operation].key;
    let key;
    if (crypto.randomUUID) key = crypto.randomUUID();
    else {
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      bytes[6] = (bytes[6] & 15) | 64;
      bytes[8] = (bytes[8] & 63) | 128;
      const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
      key = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    pendingRequests[operation] = { fingerprint, key };
    return key;
  }
  function fileId(file) {
    if (!fileIds.has(file)) fileIds.set(file, ++nextFileId);
    return fileIds.get(file);
  }
  function clear(operation) {
    delete pendingRequests[operation];
  }
  return { requestKey, fileId, clear };
}
