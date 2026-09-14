const form = document.querySelector('#search-form');
const codeInput = document.querySelector('#process-code');
const tokenInput = document.querySelector('#bearer-token');
const toggleToken = document.querySelector('#toggle-token');
const statusBox = document.querySelector('#status');
const result = document.querySelector('#result');
const submit = form.querySelector('button[type="submit"]');

function setStatus(message, error = false) {
  statusBox.textContent = message;
  statusBox.classList.toggle('error', error);
  statusBox.hidden = !message;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

// Minimal Markdown renderer: intentionally supports only headings, unordered lists and
// paragraphs. Content is always inserted with textContent, never innerHTML, because
// summaries are model-generated and must not become executable markup.
function renderMarkdown(markdown, target) {
  clear(target);
  let list = null;
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) { list = null; continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      list = null;
      const el = document.createElement(`h${heading[1].length}`);
      el.textContent = heading[2];
      target.appendChild(el);
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (!list) { list = document.createElement('ul'); target.appendChild(list); }
      const item = document.createElement('li');
      item.textContent = bullet[1];
      list.appendChild(item);
      continue;
    }
    list = null;
    const paragraph = document.createElement('p');
    paragraph.textContent = line;
    target.appendChild(paragraph);
  }
}

function showProcess(data) {
  document.querySelector('#result-code').textContent = data.code || '—';
  document.querySelector('#result-class').textContent = data.class_name || 'Não informado';
  document.querySelector('#result-court').textContent = data.court || 'Não informado';
  const body = document.querySelector('#summary-body');
  const model = document.querySelector('#summary-model');
  if (data.summary && data.summary.markdown) {
    renderMarkdown(data.summary.markdown, body);
    model.textContent = data.summary.model ? `Modelo: ${data.summary.model}` : '';
  } else {
    clear(body);
    const p = document.createElement('p');
    p.textContent = 'Este processo está disponível, mas ainda não possui um resumo publicado.';
    body.appendChild(p);
    model.textContent = '';
  }
  result.hidden = false;
  result.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

toggleToken.addEventListener('click', () => {
  const showing = tokenInput.type === 'text';
  tokenInput.type = showing ? 'password' : 'text';
  toggleToken.textContent = showing ? 'Mostrar' : 'Ocultar';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  result.hidden = true;
  const code = codeInput.value.trim();
  const token = tokenInput.value.trim();
  if (!token) { setStatus('Informe o bearer token para consultar o processo.', true); tokenInput.focus(); return; }
  submit.disabled = true;
  setStatus('Consultando a versão mais recente do processo…');
  try {
    const response = await fetch(`/processes/${encodeURIComponent(code)}`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' }
    });
    if (response.status === 401) throw new Error('Credencial inválida. Confira o bearer token.');
    if (response.status === 404) throw new Error('Processo não encontrado ou sem acesso para esta credencial.');
    if (response.status === 400) throw new Error('Número CNJ inválido. Confira o número informado.');
    if (!response.ok) throw new Error('Não foi possível consultar o processo agora.');
    const data = await response.json();
    setStatus('');
    showProcess(data);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : 'Falha inesperada na consulta.', true);
  } finally {
    submit.disabled = false;
  }
});
