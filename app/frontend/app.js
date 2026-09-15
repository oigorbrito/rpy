const form = document.querySelector('#search-form');
const codeInput = document.querySelector('#process-code');
const codeError = document.querySelector('#process-code-error');
const tokenInput = document.querySelector('#bearer-token');
const tokenError = document.querySelector('#token-error');
const toggleToken = document.querySelector('#toggle-token');
const accessTrigger = document.querySelector('#access-trigger');
const accessLabel = document.querySelector('#access-label');
const authPanel = document.querySelector('#auth-panel');
const statusBox = document.querySelector('#status');
const statusTitle = document.querySelector('#status-title');
const statusDetail = document.querySelector('#status-detail');
const retryButton = document.querySelector('#retry-search');
const result = document.querySelector('#result');
const submit = document.querySelector('#search-submit');
const copyButton = document.querySelector('#copy-summary');
const newSearchButton = document.querySelector('#new-search');
let currentSummary = '';

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function setFieldError(input, node, message = '') {
  node.textContent = message;
  node.hidden = !message;
  input.setAttribute('aria-invalid', message ? 'true' : 'false');
}

function setStatus(title = '', detail = '', { error = false, retry = false } = {}) {
  statusTitle.textContent = title;
  statusDetail.textContent = detail;
  statusBox.classList.toggle('error', error);
  retryButton.hidden = !retry;
  statusBox.hidden = !title;
}

function setLoading(loading) {
  submit.disabled = loading;
  submit.textContent = loading ? 'Consultando…' : 'Consultar processo';
  result.setAttribute('aria-busy', loading ? 'true' : 'false');
}

function updateAccessState() {
  const configured = Boolean(tokenInput.value.trim());
  accessTrigger.classList.toggle('configured', configured);
  accessLabel.textContent = configured ? 'Acesso configurado' : 'Configurar acesso';
}

// Minimal Markdown renderer: headings, unordered lists and paragraphs only.
// Model-generated content is inserted as text nodes, never executable markup.
function renderMarkdown(markdown, target) {
  clear(target);
  let list = null;
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) { list = null; continue; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      list = null;
      const el = document.createElement(`h${Math.min(heading[1].length + 1, 4)}`);
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

function formatCreatedAt(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function showProcess(data) {
  document.querySelector('#result-code').textContent = data.code || '—';
  document.querySelector('#result-class').textContent = data.class_name || 'Não informado';
  document.querySelector('#result-court').textContent = data.court || 'Não informado';
  document.querySelector('#result-context').textContent = 'Versão mais recente disponível para esta credencial.';
  const body = document.querySelector('#summary-body');
  const provenance = document.querySelector('#summary-provenance');

  currentSummary = data.summary?.markdown || '';
  copyButton.hidden = !currentSummary;
  if (currentSummary) {
    renderMarkdown(currentSummary, body);
    const created = formatCreatedAt(data.summary.created_at);
    provenance.textContent = created ? `Resumo publicado em ${created}.` : 'Resumo publicado para a versão atual.';
  } else {
    clear(body);
    const p = document.createElement('p');
    p.className = 'summary-empty';
    p.textContent = 'O processo está disponível, mas ainda não há um resumo publicado para a versão atual. A API não informa se existe geração em andamento; por isso este estado não é apresentado como processamento.';
    body.appendChild(p);
    provenance.textContent = '';
  }

  result.hidden = false;
  setStatus();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  result.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
}

function validCnj(value) {
  return /^\d{20}$/.test(value) || /^\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}$/.test(value);
}

async function searchProcess() {
  result.hidden = true;
  setFieldError(codeInput, codeError);
  setFieldError(tokenInput, tokenError);
  const code = codeInput.value.trim();
  const token = tokenInput.value.trim();

  if (!validCnj(code)) {
    setFieldError(codeInput, codeError, 'Informe um número CNJ válido, formatado ou com 20 dígitos.');
    codeInput.focus();
    return;
  }
  if (!token) {
    setFieldError(tokenInput, tokenError, 'Informe a credencial de acesso antes de consultar.');
    authPanel.hidden = false;
    accessTrigger.setAttribute('aria-expanded', 'true');
    tokenInput.focus();
    return;
  }

  setLoading(true);
  setStatus('Consultando processo', 'Buscando a versão mais recente disponível para esta credencial.');
  try {
    const response = await fetch(`/processes/${encodeURIComponent(code)}`, {
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' }
    });
    if (response.status === 401) {
      setFieldError(tokenInput, tokenError, 'A credencial foi recusada. Confira o token e tente novamente.');
      throw new Error('AUTH');
    }
    if (response.status === 404) throw new Error('NOT_FOUND');
    if (response.status === 400) throw new Error('INVALID_CODE');
    if (!response.ok) throw new Error('SERVER');
    showProcess(await response.json());
  } catch (error) {
    if (error instanceof Error && error.message === 'AUTH') {
      setStatus('Não foi possível autorizar a consulta', 'Confira a credencial do ambiente. Por segurança, não informamos se o processo existe.', { error: true });
      authPanel.hidden = false;
      accessTrigger.setAttribute('aria-expanded', 'true');
      tokenInput.focus();
    } else if (error instanceof Error && error.message === 'NOT_FOUND') {
      setStatus('Processo indisponível para esta credencial', 'O processo pode não estar cadastrado ou esta credencial pode não ter acesso. A API preserva essa distinção por segurança.', { error: true });
    } else if (error instanceof Error && error.message === 'INVALID_CODE') {
      setFieldError(codeInput, codeError, 'A API recusou o número informado. Revise o CNJ e tente novamente.');
      setStatus('Número de processo inválido', 'Corrija o CNJ informado para continuar.', { error: true });
      codeInput.focus();
    } else if (error instanceof TypeError) {
      setStatus('Sem conexão com o serviço', 'Não foi possível alcançar a API. Verifique a conexão e tente novamente.', { error: true, retry: true });
    } else {
      setStatus('A consulta não pôde ser concluída', 'O serviço respondeu com um erro inesperado. Tente novamente em instantes.', { error: true, retry: true });
    }
  } finally {
    setLoading(false);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  await searchProcess();
});

retryButton.addEventListener('click', searchProcess);

toggleToken.addEventListener('click', () => {
  const showing = tokenInput.type === 'text';
  tokenInput.type = showing ? 'password' : 'text';
  toggleToken.textContent = showing ? 'Mostrar' : 'Ocultar';
  toggleToken.setAttribute('aria-pressed', showing ? 'false' : 'true');
});

tokenInput.addEventListener('input', () => {
  setFieldError(tokenInput, tokenError);
  updateAccessState();
});

codeInput.addEventListener('input', () => setFieldError(codeInput, codeError));

accessTrigger.addEventListener('click', () => {
  const opening = authPanel.hidden;
  authPanel.hidden = !opening;
  accessTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
  if (opening) tokenInput.focus();
});

copyButton.addEventListener('click', async () => {
  if (!currentSummary) return;
  try {
    await navigator.clipboard.writeText(currentSummary);
    const previous = copyButton.textContent;
    copyButton.textContent = 'Resumo copiado';
    setTimeout(() => { copyButton.textContent = previous; }, 1800);
  } catch {
    setStatus('Não foi possível copiar automaticamente', 'Selecione o texto do resumo e use o comando de copiar do navegador.', { error: true });
  }
});

newSearchButton.addEventListener('click', () => {
  result.hidden = true;
  setStatus();
  codeInput.value = '';
  codeInput.focus();
});

updateAccessState();
