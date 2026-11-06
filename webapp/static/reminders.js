document.addEventListener('DOMContentLoaded', () => {
  const dialog = document.querySelector('#reminderDialog');
  if (!dialog) return;
  const form = dialog.querySelector('form');

  document.querySelector('[data-reminder-new]')?.addEventListener('click', () => {
    // A cancelled edit must never turn the next new reminder into an update.
    for (const name of ['reminder_id', 'title', 'due_at', 'notes']) {
      form.elements.namedItem(name).value = '';
    }
    form.elements.namedItem('priority').value = 'Normal';
    dialog.querySelector('#reminderDialogTitle').textContent = 'Novo lembrete';
    form.querySelector('[type="submit"]').textContent = 'Salvar lembrete';
    form.querySelectorAll('.field-error, .reminder-form-errors').forEach((node) => node.remove());
    form.querySelectorAll('[aria-invalid]').forEach((field) => {
      field.removeAttribute('aria-invalid');
      field.removeAttribute('aria-describedby');
    });
    form.elements.namedItem('due_at').setAttribute('aria-describedby', 'reminderTimezone');
    form.elements.namedItem('title').focus();
  });

  dialog.addEventListener('close', () => {
    const url = new URL(window.location.href);
    url.searchParams.delete('edit');
    url.searchParams.delete('new');
    window.history.replaceState(null, '', url);
  });
  if (dialog.open) form.querySelector('[aria-invalid="true"]')?.focus();
});
