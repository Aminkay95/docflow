async function send(url, payload) {
  const response = await fetch(url, {method: 'POST', headers: {
    'Content-Type': 'application/json',
    'X-CSRF-Token': document.querySelector('#csrf').value
  }, ...(payload ? {body: JSON.stringify(payload)} : {})});
  if (!response.ok) {
    document.querySelector('#feedback').textContent = `Request failed (${response.status}). Check the reason and reload if the item changed.`;
    return false;
  }
  return true;
}
document.addEventListener('submit', async event => {
  const form = event.target;
  if (!form.dataset.resolve) return;
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  if (data.outcome === 'corrected') {
    data.corrected_value = JSON.parse(form.dataset.field);
    data.corrected_value.value = data.value;
    data.corrected_value.agreement = 'human_corrected';
    data.corrected_value.alternatives = [];
  }
  delete data.value;
  if (data.outcome !== 'accepted') delete data.chosen_document_id;
  if (await send(form.dataset.resolve, data)) location.reload();
});
document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.post && await send(button.dataset.post)) location.href = '/review/login';
  if (button.dataset.action && await send(button.dataset.action)) location.reload();
});
function showBox(event) {
  const field = event.target.closest('[data-box]');
  if (!field) return;
  const box = JSON.parse(field.dataset.box);
  if (!box) return;
  const page = document.querySelector(`#page-${field.dataset.page}`);
  const overlay = page.querySelector('.bbox');
  overlay.hidden = false;
  overlay.style.left = `${100 * box.x0}%`;
  overlay.style.top = `${100 * box.y0}%`;
  overlay.style.width = `${100 * (box.x1 - box.x0)}%`;
  overlay.style.height = `${100 * (box.y1 - box.y0)}%`;
}
document.addEventListener('focusin', showBox);
document.addEventListener('click', showBox);
