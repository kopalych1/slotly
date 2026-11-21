
(() => {
  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    const input = button.closest('.invite__link').querySelector('[data-invite-url]');
    input.select();
    try {
      await navigator.clipboard.writeText(input.value);
      const label = button.textContent;
      button.textContent = button.dataset.done || '✓';
      setTimeout(() => (button.textContent = label), 1500);
    } catch {
      // The link stays selected, Ctrl+C works as a fallback
    }
  });
})();

(() => {
  document.addEventListener('click', (event) => {
    const open = event.target.closest('[data-qr-open]');
    if (open) {
      document.getElementById(open.dataset.qrOpen)?.showModal();
      return;
    }
    const close = event.target.closest('[data-qr-close]');
    if (close) {
      close.closest('dialog').close();
      return;
    }
    // A click on the backdrop closes the dialog
    if (event.target instanceof HTMLDialogElement) event.target.close();
  });
})();