document.addEventListener('DOMContentLoaded', () => {
  const menuButton = document.querySelector('.mobile-menu');
  const sidebar = document.querySelector('.sidebar');
  if (menuButton && sidebar) {
    menuButton.addEventListener('click', () => {
      const open = sidebar.classList.toggle('open');
      menuButton.setAttribute('aria-expanded', String(open));
    });
  }

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll('[data-dialog-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.querySelector(button.dataset.dialogOpen);
      if (dialog && !dialog.open) dialog.showModal();
    });
  });

  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  document.querySelectorAll('dialog[data-open="true"]').forEach((dialog) => {
    if (!dialog.open) dialog.showModal();
  });

  document.querySelectorAll('[data-export-period]').forEach((select) => {
    const form = select.form;
    const updateDates = () => {
      const custom = select.value === 'custom';
      form.querySelectorAll('[data-export-date]').forEach((input) => {
        input.disabled = !custom;
        input.required = custom;
      });
    };
    select.addEventListener('change', updateDates);
    updateDates();
  });
});
