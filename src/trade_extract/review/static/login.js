(() => {
  document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
    const input = toggle.closest(".field-control")?.querySelector("input");
    if (!input) return;

    toggle.addEventListener("click", () => {
      const reveal = input.type === "password";
      input.type = reveal ? "text" : "password";
      toggle.classList.toggle("is-revealed", reveal);
      const label = reveal ? "Hide password" : "Show password";
      toggle.setAttribute("aria-label", label);
      toggle.setAttribute("title", label);
    });
  });
})();

/* Auth forms submit natively, so the busy state has to survive the navigation.
 * Store the idle label up front and restore it if the browser returns via the
 * back/forward cache. */
(() => {
  const forms = Array.from(document.querySelectorAll("form.login-form"));
  const idleLabels = new WeakMap();

  const submitButton = (form) => form.querySelector('button[type="submit"]');

  forms.forEach((form) => {
    const button = submitButton(form);
    const label = button?.querySelector("span");
    if (button && label) idleLabels.set(button, label.textContent);

    form.addEventListener("submit", () => {
      if (!button) return;
      const span = button.querySelector("span");
      if (span) span.textContent = button.dataset.busyLabel || "Working…";
      button.disabled = true;
      button.classList.add("is-busy");
      button.setAttribute("aria-busy", "true");
    });
  });

  window.addEventListener("pageshow", () => {
    forms.forEach((form) => {
      const button = submitButton(form);
      if (!button) return;
      button.disabled = false;
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      const span = button.querySelector("span");
      if (span && idleLabels.has(button)) span.textContent = idleLabels.get(button);
    });
  });
})();
