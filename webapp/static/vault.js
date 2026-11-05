/* Plaintext exists only following a deliberate, reauthenticated operation. */
document.addEventListener('DOMContentLoaded', () => {
  const dialog = document.getElementById('vault-auth');
  if (!dialog) return;
  const form = document.getElementById('vault-auth-form');
  const authInput = document.getElementById('vault-reauth-password');
  const error = document.getElementById('vault-auth-error');
  const feedback = document.getElementById('vault-feedback');
  let selected = null;
  let controller = null;
  let pending = false;
  const timers = new Map();

  function mask(record) {
    if (timers.has(record)) clearTimeout(timers.get(record));
    timers.delete(record);
    record.querySelector('[data-vault-value]').textContent = '••••••••';
    const eye = record.querySelector('[data-vault-action="reveal"]');
    if (eye) eye.setAttribute('aria-label', 'Visualizar senha');
  }
  function reset() {
    if (controller) controller.abort();
    controller = null;
    authInput.value = '';
    selected = null;
    if (dialog.open) dialog.close();
    document.querySelectorAll('[data-vault-record]').forEach((record) => {
      if (record.querySelector('[data-vault-action]')) mask(record);
    });
  }
  async function copySecret(secret) {
    if (!window.isSecureContext || !navigator.clipboard) throw new Error('clipboard');
    await navigator.clipboard.writeText(secret);
    feedback.textContent = 'Senha copiada. A limpeza em 30 segundos depende da permissão de leitura do navegador. O histórico da área de transferência pode manter cópias.';
    // Never request clipboard-read permission automatically or erase unrelated content.
    setTimeout(async () => {
      try {
        const permission = await navigator.permissions.query({name: 'clipboard-read'});
        if (permission.state === 'granted' && await navigator.clipboard.readText() === secret) {
          await navigator.clipboard.writeText('');
        }
      } catch (_) { /* Browser does not permit conditional cleanup. */ }
      secret = null;
    }, 30000);
  }

  document.querySelectorAll('[data-vault-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const record = button.closest('[data-vault-record]');
      if (button.dataset.vaultAction === 'reveal' && timers.has(record)) {
        mask(record);
        return;
      }
      selected = button;
      error.textContent = '';
      authInput.value = '';
      dialog.showModal();
      authInput.focus();
    });
  });
  document.getElementById('vault-auth-cancel').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    authInput.value = '';
    selected = null;
    if (controller) controller.abort();
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) reset(); });
  window.addEventListener('pagehide', reset);
  window.addEventListener('pagehide', () => {
    document.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ''; });
  });
  window.addEventListener('pageshow', reset);

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!selected || pending) return;
    const button = selected;
    const record = button.closest('[data-vault-record]');
    const action = button.dataset.vaultAction;
    controller = new AbortController();
    pending = true;
    form.querySelector('button[type="submit"]').disabled = true;
    try {
      const response = await fetch(button.dataset.secretUrl, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': dialog.dataset.csrf},
        body: JSON.stringify({action, reauth_password: authInput.value}), signal: controller.signal
      });
      authInput.value = '';
      let data = await response.json();
      if (!response.ok || typeof data.password !== 'string') {
        error.textContent = data.error || 'Não foi possível acessar a senha. Entre novamente se a sessão expirou.';
        return;
      }
      if (document.hidden || !dialog.open) return;
      if (action === 'copy') await copySecret(data.password);
      else {
        mask(record);
        record.querySelector('[data-vault-value]').textContent = data.password;
        button.setAttribute('aria-label', 'Ocultar senha');
        timers.set(record, setTimeout(() => mask(record), 30000));
      }
      data.password = null;
      data = null;
      dialog.close();
    } catch (_) {
      error.textContent = 'Operação não concluída. Para copiar, use HTTPS e permita a área de transferência no navegador.';
    } finally {
      authInput.value = '';
      pending = false;
      form.querySelector('button[type="submit"]').disabled = false;
      controller = null;
    }
  });
});
