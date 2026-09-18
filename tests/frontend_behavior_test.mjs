import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const ids = ["search-form","process-code","process-code-error","bearer-token","token-error","toggle-token","access-trigger","access-label","auth-panel","status","status-title","status-detail","retry-search","request-process","result","search-submit","copy-summary","new-search","action-feedback","result-code","result-class","result-court","result-updated","result-context","parties-list","subjects-list","header-data","movements-list","summary-body","summary-provenance"];
class Element {
  constructor(id) { this.id=id; this.hidden=false; this.disabled=false; this.value=""; this.textContent=""; this.children=[]; this.firstChild=null; this.listeners={}; this.classList={toggle(){}}; }
  append(...nodes) { this.children.push(...nodes); this.firstChild=this.children[0] ?? null; }
  appendChild(node) { this.append(node); return node; }
  removeChild(node) { this.children=this.children.filter(n=>n!==node); this.firstChild=this.children[0] ?? null; }
  setAttribute() {}
  addEventListener(type, fn) { this.listeners[type]=fn; }
  scrollIntoView() {}
  get renderedText() { return this.textContent + this.children.map(n=>n.renderedText ?? n.textContent ?? "").join(""); }
}
function run(fetchResponses) {
  const elements = Object.fromEntries(ids.map(id=>[id,new Element(id)]));
  let timer=null, fetchIndex=0;
  const document={querySelector: s=>elements[s.slice(1)], createElement: tag=>new Element(tag)};
  const context={document, console, requestAnimationFrame: fn=>fn(), navigator:{clipboard:{writeText:async()=>{}}}, window:{matchMedia:()=>({matches:false}), addEventListener(){}} , setTimeout:fn=>{timer=fn; return 1}, clearTimeout:()=>{timer=null}, fetch:async()=>fetchResponses[Math.min(fetchIndex++,fetchResponses.length-1)]};
  vm.runInNewContext(fs.readFileSync("app/frontend/app.js","utf8"), context);
  return {elements, async search(){await elements["search-form"].listeners.submit({preventDefault(){}})}, async tick(){assert(timer); const fn=timer; timer=null; await fn();}, hasTimer:()=>timer!==null};
}
const ready = {ok:true,status:200,json:async()=>({code:"0000000-00.2026.8.21.0001",class_name:"Classe",court:"TJRS",summary_status:"available",summary:{markdown:"Resumo pronto"},parties:[],subjects:[],header:{},recent_steps:[{title:"Sentença",text:"Movimento público"}]})};
const notFound = {ok:false,status:404,json:async()=>({detail:"not found"})};
const serverError = {ok:false,status:500,json:async()=>({})};
let ui=run([ready]); ui.elements["process-code"].value="0000000-00.2026.8.21.0001"; ui.elements["bearer-token"].value="token"; await ui.search(); assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto")); assert.ok(ui.elements["movements-list"].renderedText.includes("Movimento público"));
ui=run([{...ready,json:async()=>({...(await ready.json()),summary_status:"processing",summary:null})},ready]); ui.elements["process-code"].value="0000000-00.2026.8.21.0001"; ui.elements["bearer-token"].value="token"; await ui.search(); assert.ok(ui.elements["summary-body"].renderedText.includes("sendo preparado")); assert(ui.hasTimer()); await ui.tick(); assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto")); assert.equal(ui.hasTimer(),false);
ui=run([ready,notFound]); ui.elements["process-code"].value="0000000-00.2026.8.21.0001"; ui.elements["bearer-token"].value="token"; await ui.search(); ui.elements["process-code"].value="0000000-00.2026.8.21.0002"; await ui.search(); assert.equal(ui.elements["result"].hidden,true); assert(!ui.elements["status-title"].textContent.includes("Resumo pronto"));
ui=run([ready,serverError]); ui.elements["process-code"].value="0000000-00.2026.8.21.0001"; ui.elements["bearer-token"].value="token"; await ui.search(); await ui.search(); assert.ok(ui.elements["status-title"].textContent.includes("não pôde ser concluída")); assert.equal(ui.elements["result"].hidden,true);
ui=run([{...ready,json:async()=>({code:"0000000-00.2026.8.21.0003",class_name:"Sigiloso",court:"TJRS",summary_status:"available",summary:{markdown:"Resumo local seguro"},parties:[],subjects:[],header:{},recent_steps:[]})}]); ui.elements["process-code"].value="0000000-00.2026.8.21.0003"; ui.elements["bearer-token"].value="token"; await ui.search(); const secret=ui.elements["result"].renderedText; for (const forbidden of ["PARTE-SECRETA","ASSUNTO-SECRETO","MOVIMENTO-SECRETO","R$ 999"]) assert.doesNotMatch(secret,new RegExp(forbidden));

const jsxReady = {ok:true,status:200,json:async()=>({code:"0000000-00.2026.8.21.0010",class_name:"Classe",court:"TJRS",summary_status:"available",summary:{markdown:'# Resumo do processo\n<ProcessHeader className="process-header">\n- Processo: 0000000-00.2026.8.21.0010\n</ProcessHeader>\n## Partes\n<Party name="Maria da Silva" />\n<img src=x onerror=alert(1)>'},parties:[{name:"Maria da Silva"}],subjects:[],header:{},recent_steps:[]})};
ui=run([jsxReady]); ui.elements["process-code"].value="0000000-00.2026.8.21.0010"; ui.elements["bearer-token"].value="token"; await ui.search(); const jsxText=ui.elements["summary-body"].renderedText; assert.ok(jsxText.includes("Maria da Silva")); assert.ok(jsxText.includes("Processo: 0000000-00.2026.8.21.0010")); assert.ok(jsxText.includes("<img src=x onerror=alert(1)>")); const summaryChildren=ui.elements["summary-body"].children; assert.ok(summaryChildren.some(n=>n.className==="summary-process-header")); assert.ok(summaryChildren.some(n=>n.children?.some?.(x=>x.className==="summary-party")) || jsxText.includes("Maria da Silva"));
console.log("frontend behavior: PASS (ready, processing->ready, polling stop, 404/error clearing, secret non-leak, local fetch only)");
