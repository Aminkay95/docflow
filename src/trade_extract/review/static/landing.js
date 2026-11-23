document.querySelectorAll(".sample-panel").forEach((panel) => {
  let selected = panel.querySelector('[aria-pressed="true"]');
  let source = document.getElementById(selected.dataset.source);

  panel.addEventListener("click", (event) => {
    const button = event.target.closest("[data-source]");
    if (!button || button === selected) return;

    selected.setAttribute("aria-pressed", "false");
    source.classList.remove("is-active");

    selected = button;
    source = document.getElementById(button.dataset.source);
    selected.setAttribute("aria-pressed", "true");
    source.classList.add("is-active");
    source.scrollIntoView({ block: "nearest" });
  });
});
