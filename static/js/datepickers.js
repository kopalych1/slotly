(() => {
  if (typeof flatpickr === "undefined") return;

  const lang = document.documentElement.lang;
  const locale = {
    ...(flatpickr.l10ns[lang] ?? flatpickr.l10ns.default),
    firstDayOfWeek: 1,
  };

  const base = { locale, allowInput: true, altInput: true };
  const kinds = {
    date: { dateFormat: "Y-m-d", altFormat: "d/m/Y" },
    datetime: {
      dateFormat: "Y-m-d\\TH:i",
      altFormat: "d/m/Y H:i",
      enableTime: true,
      time_24hr: true,
      minuteIncrement: 5,
    },
  };

  // Also called for rows added later (see exam_edit.js)
  window.initPickers = (root = document) => {
    root
      .querySelectorAll("input[data-picker]:not(.flatpickr-input)")
      .forEach((input) => {
        flatpickr(input, { ...base, ...kinds[input.dataset.picker] });
      });
  };

  window.initPickers();
})();
