(() => {
  const board = document.getElementById("board");
  const durationInput = document.getElementById("id_slot_duration");
  const totalForms = document.getElementById("id_form-TOTAL_FORMS");
  const template = document.getElementById("day-template");
  const addButton = document.getElementById("add-day");
  if (!board || !durationInput || !totalForms || !template || !addButton)
    return;

  const addColumn = addButton.closest(".day--add");
  const form = board.closest("form");
  const { labelSlots, msgTooShort, msgEndBeforeStart, msgIncomplete } =
    board.dataset;

  const pad = (n) => String(n).padStart(2, "0");
  const format = (minutes) =>
    `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
  const toMinutes = (value) => {
    const match = /^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$/.exec(value);
    if (!match) return null;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    return hours < 24 && minutes < 60 ? hours * 60 + minutes : null;
  };

  // The server recomputes everything on Save: this is only a preview
  const updatePreview = (row) => {
    const list = row.querySelector("[data-preview]");
    const summary = row.querySelector("[data-summary]");
    const start = toMinutes(row.querySelector('input[name$="-start"]').value);
    const end = toMinutes(row.querySelector('input[name$="-end"]').value);
    const duration = Number(durationInput.value);

    list.replaceChildren();
    summary.textContent = "";
    if (start === null || end === null || !(duration > 0)) {
      summary.textContent = msgIncomplete;
      return;
    }
    if (end <= start) {
      summary.textContent = msgEndBeforeStart;
      return;
    }

    const count = Math.floor((end - start) / duration);
    if (count === 0) {
      summary.textContent = msgTooShort;
      return;
    }

    summary.textContent = `${labelSlots}: ${count} (${format(start)}–${format(start + count * duration)})`;
    for (let i = 0; i < count; i++) {
      const item = document.createElement("li");
      item.className = "slot slot--preview";
      const time = document.createElement("time");
      time.className = "slot__time";
      time.textContent = format(start + i * duration);
      item.append(time);
      list.append(item);
    }
  };

  const addRow = () => {
    const index = Number(totalForms.value);
    const holder = document.createElement("div");
    holder.innerHTML = template.innerHTML
      .replaceAll("__prefix__", String(index))
      .trim();
    const row = holder.firstElementChild;
    addColumn.before(row);
    totalForms.value = String(index + 1);
    row.querySelector("input").focus();
    updatePreview(row);
    refreshSave();
  };

  addButton.addEventListener("click", addRow);

  ["input", "change"].forEach((type) => {
    board.addEventListener(type, (event) => {
      const row = event.target.closest("[data-draft-row]");

      if (row) updatePreview(row);
    });
    durationInput.addEventListener(type, () => {
      board.querySelectorAll("[data-draft-row]").forEach(updatePreview);
    });
  });

  // Removing a row only ticks its DELETE box and hides it: Django ignores such rows
  board.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-row]");
    if (!button) return;
    const row = button.closest("[data-draft-row]");
    row.querySelector('input[name$="-DELETE"]').checked = true;
    row.hidden = true;
    refreshSave();
  });

  // Enter inside a field must not submit the whole editor by accident
  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.matches("input"))
      event.preventDefault();
  });

  durationInput.addEventListener("input", () => {
    board.querySelectorAll("[data-draft-row]").forEach(updatePreview);
  });

  board.querySelectorAll("[data-draft-row]").forEach(updatePreview);

  const saveButton = document.getElementById("save-button");
  const snapshot = () => new URLSearchParams(new FormData(form)).toString();
  const initialState = snapshot();
  const startDirty = form.dataset.startDirty === "true";

  function refreshSave() {
    if (saveButton)
      saveButton.disabled = !startDirty && snapshot() === initialState;
  }

  ["input", "change"].forEach((type) =>
    form.addEventListener(type, refreshSave),
  );
  refreshSave();
})();

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