import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const APP_PATH = "app/frontend/app.js";
const VALID_CODE = "0000000-00.2026.8.21.0001";
const TOKEN = "test-token";

const ids = [
  "search-form","process-code","process-code-error","bearer-token","token-error",
  "toggle-token","access-trigger","access-label","auth-panel","status","status-title",
  "status-detail","retry-search","request-process","result","search-submit",
  "copy-summary","new-search","action-feedback","result-code","result-class",
  "result-court","result-updated","result-context","parties-list","subjects-list",
  "header-data","movements-list","summary-body","summary-provenance",
];

class ClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class Element {
  constructor(id) {
    this.id = id;
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.textContent = "";
    this.type = id === "bearer-token" ? "password" : "";
    this.children = [];
    this.firstChild = null;
    this.listeners = {};
    this.attributes = new Map();
    this.classList = new ClassList();
    this.focused = false;
    this.scrolled = false;
  }

  append(...nodes) {
    this.children.push(...nodes);
    this.firstChild = this.children[0] ?? null;
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  removeChild(node) {
    this.children = this.children.filter(candidate => candidate !== node);
    this.firstChild = this.children[0] ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  focus() {
    this.focused = true;
  }

  scrollIntoView() {
    this.scrolled = true;
  }

  get renderedText() {
    return this.textContent + this.children
      .map(node => node.renderedText ?? node.textContent ?? "")
      .join("");
  }
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function readyPayload(overrides = {}) {
  return {
    code: VALID_CODE,
    class_name: "Classe",
    court: "TJRS",
    updated_at: "2026-09-18T12:00:00Z",
    summary_status: "available",
    summary: { markdown: "Resumo pronto", created_at: "2026-09-18T12:00:00Z" },
    parties: [],
    subjects: [],
    header: {},
    recent_steps: [{ title: "Sentença", text: "Movimento público", occurred_at: "2026-09-18T10:00:00Z" }],
    ...overrides,
  };
}

function createHarness(fetchResponses) {
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const requests = [];
  const timers = new Map();
  const windowListeners = {};
  let fetchIndex = 0;
  let timerId = 0;
  let clipboardText = "";

  const document = {
    querySelector(selector) {
      assert.match(selector, /^#[a-z0-9-]+$/i, `unexpected selector: ${selector}`);
      return elements[selector.slice(1)];
    },
    createElement(tag) {
      return new Element(tag);
    },
  };

  const context = {
    document,
    console,
    TypeError,
    requestAnimationFrame: callback => callback(),
    navigator: {
      clipboard: {
        writeText: async value => {
          clipboardText = value;
        },
      },
    },
    window: {
      matchMedia: () => ({ matches: false }),
      addEventListener: (type, handler) => {
        windowListeners[type] = handler;
      },
    },
    setTimeout: callback => {
      const id = ++timerId;
      timers.set(id, callback);
      return id;
    },
    clearTimeout: id => {
      timers.delete(id);
    },
    fetch: async (url, options = {}) => {
      requests.push({ url, options });
      const response = fetchResponses[Math.min(fetchIndex, fetchResponses.length - 1)];
      fetchIndex += 1;
      if (response instanceof Error) throw response;
      return response;
    },
  };

  vm.runInNewContext(fs.readFileSync(APP_PATH, "utf8"), context);

  return {
    elements,
    requests,
    clipboard: () => clipboardText,
    hasTimer: () => timers.size > 0,
    async tick() {
      assert.ok(timers.size > 0, "expected a scheduled timer");
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      await callback();
    },
    async search({ code = VALID_CODE, token = TOKEN } = {}) {
      elements["process-code"].value = code;
      elements["bearer-token"].value = token;
      await elements["search-form"].listeners.submit({ preventDefault() {} });
    },
    async click(id) {
      assert.equal(typeof elements[id].listeners.click, "function", `${id} has no click listener`);
      await elements[id].listeners.click();
    },
    pagehide() {
      windowListeners.pagehide?.();
    },
  };
}

async function scenario(name, body) {
  try {
    await body();
    console.log(`ok - ${name}`);
  } catch (error) {
    error.message = `${name}: ${error.message}`;
    throw error;
  }
}

await scenario("successful lookup renders public data, focuses result and uses bearer header", async () => {
  const ui = createHarness([jsonResponse(200, readyPayload())]);

  await ui.search();

  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, `/processes/${encodeURIComponent(VALID_CODE)}`);
  assert.equal(ui.requests[0].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(ui.requests[0].options.headers.Accept, "application/json");
  assert.doesNotMatch(ui.requests[0].url, /test-token/);
  assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto"));
  assert.ok(ui.elements["movements-list"].renderedText.includes("Movimento público"));
  assert.equal(ui.elements["result"].hidden, false);
  assert.equal(ui.elements["result-code"].focused, true);
  assert.equal(ui.elements["result"].scrolled, true);
});

await scenario("processing summary remains readable and transitions to published summary", async () => {
  const processing = readyPayload({ summary_status: "processing", summary: null });
  const ui = createHarness([
    jsonResponse(200, processing),
    jsonResponse(200, readyPayload()),
  ]);

  await ui.search();

  assert.ok(ui.elements["summary-body"].renderedText.includes("sendo preparado"));
  assert.equal(ui.elements["result"].hidden, false);
  assert.equal(ui.hasTimer(), true);

  await ui.tick();

  assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto"));
  assert.equal(ui.hasTimer(), false);
  assert.ok(ui.elements["status-title"].textContent.includes("Resumo publicado"));
});

await scenario("a new lookup clears stale result before a 404 outcome", async () => {
  const ui = createHarness([
    jsonResponse(200, readyPayload()),
    jsonResponse(404, { detail: "not found" }),
  ]);

  await ui.search();
  assert.equal(ui.elements["result"].hidden, false);

  await ui.search({ code: "0000000-00.2026.8.21.0002" });

  assert.equal(ui.elements["result"].hidden, true);
  assert.ok(ui.elements["status-title"].textContent.includes("ainda não disponível"));
  assert.equal(ui.elements["request-process"].hidden, false);
});

await scenario("missing process acquisition starts only after explicit user action", async () => {
  const ui = createHarness([
    jsonResponse(404, { detail: "not found" }),
    jsonResponse(202, { status: "pending", created: true }),
  ]);

  await ui.search();

  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.method, undefined);

  await ui.click("request-process");

  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}/request`);
  assert.equal(ui.requests[1].options.method, "POST");
  assert.equal(ui.requests[1].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(ui.hasTimer(), true);
});

await scenario("availability polling is bounded and stops after the documented attempt limit", async () => {
  const responses = [
    jsonResponse(404, { detail: "not found" }),
    jsonResponse(202, { status: "pending", created: true }),
    ...Array.from({ length: 20 }, () => jsonResponse(404, { detail: "not found" })),
  ];
  const ui = createHarness(responses);

  await ui.search();
  await ui.click("request-process");
  for (let attempt = 0; attempt < 20; attempt += 1) {
    assert.equal(ui.hasTimer(), true);
    await ui.tick();
  }

  assert.equal(ui.hasTimer(), false);
  assert.ok(ui.elements["status-detail"].textContent.includes("não é necessário manter esta página aberta"));
  assert.equal(ui.requests.length, 22);
});

await scenario("invalid CNJ and missing credential fail locally before network I/O", async () => {
  const invalid = createHarness([jsonResponse(500, {})]);
  await invalid.search({ code: "123", token: TOKEN });
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.elements["process-code"].getAttribute("aria-invalid"), "true");
  assert.equal(invalid.elements["process-code"].focused, true);

  const missingCredential = createHarness([jsonResponse(500, {})]);
  await missingCredential.search({ token: "" });
  assert.equal(missingCredential.requests.length, 0);
  assert.equal(missingCredential.elements["bearer-token"].getAttribute("aria-invalid"), "true");
  assert.equal(missingCredential.elements["bearer-token"].focused, true);
  assert.equal(missingCredential.elements["auth-panel"].hidden, false);
});

await scenario("unauthorized response does not reveal process existence and returns focus to credential", async () => {
  const ui = createHarness([jsonResponse(401, { detail: "unauthorized" })]);

  await ui.search();

  assert.equal(ui.elements["result"].hidden, true);
  assert.ok(ui.elements["status-title"].textContent.includes("autorizar"));
  assert.equal(ui.elements["bearer-token"].focused, true);
  assert.equal(ui.elements["bearer-token"].getAttribute("aria-invalid"), "true");
  assert.doesNotMatch(ui.elements["status-detail"].textContent, /existe|não existe/i);
});

await scenario("network failure is distinguishable from an application error", async () => {
  const network = createHarness([new TypeError("network unavailable")]);
  await network.search();
  assert.ok(network.elements["status-title"].textContent.includes("Sem conexão"));
  assert.equal(network.elements["retry-search"].hidden, false);

  const server = createHarness([jsonResponse(500, {})]);
  await server.search();
  assert.ok(server.elements["status-title"].textContent.includes("não pôde ser concluída"));
  assert.equal(server.elements["retry-search"].hidden, false);
});

await scenario("allowlisted JSX renders as inert DOM and unsupported markup stays literal", async () => {
  const markdown = [
    "# Resumo do processo",
    '<ProcessHeader className="process-header">',
    "- Processo: 0000000-00.2026.8.21.0010",
    "</ProcessHeader>",
    "## Partes",
    '<Party name="Maria da Silva" />',
    '<img src=x onerror=alert(1)>',
  ].join("\n");
  const ui = createHarness([
    jsonResponse(200, readyPayload({
      code: "0000000-00.2026.8.21.0010",
      summary: { markdown },
      parties: [{ name: "Maria da Silva" }],
      recent_steps: [],
    })),
  ]);

  await ui.search({ code: "0000000-00.2026.8.21.0010" });

  const text = ui.elements["summary-body"].renderedText;
  assert.ok(text.includes("Maria da Silva"));
  assert.ok(text.includes("Processo: 0000000-00.2026.8.21.0010"));
  assert.ok(text.includes("<img src=x onerror=alert(1)>"));
  assert.ok(ui.elements["summary-body"].children.some(node => node.className === "summary-process-header"));

  await ui.click("copy-summary");
  assert.ok(ui.clipboard().includes("Resumo do processo"));
  assert.ok(ui.clipboard().includes("Maria da Silva"));
  assert.doesNotMatch(ui.clipboard(), /<ProcessHeader|<Party/);
});

await scenario("new search and pagehide cancel polling without erasing the in-memory credential", async () => {
  const processing = readyPayload({ summary_status: "processing", summary: null });
  const ui = createHarness([jsonResponse(200, processing)]);

  await ui.search();
  assert.equal(ui.hasTimer(), true);

  await ui.click("new-search");
  assert.equal(ui.hasTimer(), false);
  assert.equal(ui.elements["result"].hidden, true);
  assert.equal(ui.elements["process-code"].value, "");
  assert.equal(ui.elements["bearer-token"].value, TOKEN);

  await ui.search();
  assert.equal(ui.hasTimer(), true);
  ui.pagehide();
  assert.equal(ui.hasTimer(), false);
});

console.log("frontend behavior harness: PASS");
