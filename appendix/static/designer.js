"use strict";
const $ = id => document.getElementById(id);
const state = { model:null, project:null, suggestions:[], diagnostics:[], history:[], future:[], selected:null, selectedNet:null, multi:new Set(), suggestController:null, zoom:1 };
const ROW_H = 38, WINDOW = 70;

async function api(path, options={}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
async function post(path, body, extra={}) { return api(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),...extra}); }
function toast(message, bad=false) { const node=$("toast"); node.textContent=message; node.style.borderColor=bad?"var(--red)":"var(--blue)"; node.classList.remove("hidden"); clearTimeout(toast.timer); toast.timer=setTimeout(()=>node.classList.add("hidden"),5000); }
async function dialog(mode,kind) { return (await api(`/api/dialog?mode=${mode}&kind=${kind}`)).path; }
function roleValues(){ return {top:$("topRole").value,child_a:$("childARole").value,child_b:$("childBRole").value}; }
function chosenModules(){ if(!state.project)return []; const r=state.project.roles; return [r.top,r.child_a,r.child_b].map(name=>state.model.modules.find(m=>m.name===name)); }
function portIndex(){ const map={}; (state.model?.modules||[]).forEach(m=>m.ports.forEach(p=>map[p.id]=p)); return map; }
function snapshot(){ if(!state.project)return; state.history.push(JSON.stringify({networks:state.project.networks,confirmed_unconnected:state.project.confirmed_unconnected,rejected_suggestions:state.project.rejected_suggestions})); if(state.history.length>100)state.history.shift(); state.future=[]; }
function autosave(){ if(state.project)localStorage.setItem("xvlink-autosave",JSON.stringify(state.project)); }
function restore(raw){ if(!raw)return; const value=JSON.parse(raw); Object.assign(state.project,value); autosave();renderAll(); validate(); }
function mutate(fn){ snapshot(); fn(); autosave();renderAll(); validate(); }

async function loadWorkbook(path) {
  if(!path)return;
  const data=await api(`/api/workbook?path=${encodeURIComponent(path)}`); state.model=data.model; state.project=null; state.suggestions=[]; state.diagnostics=data.model.diagnostics;
  const names=data.model.modules.map(m=>m.name); ["topRole","childARole","childBRole"].forEach((id,index)=>{$(id).innerHTML=names.map((name,i)=>`<option ${i===index?"selected":""}>${escapeHtml(name)}</option>`).join("");});
  $("roles").classList.remove("hidden"); $("parseSummary").textContent=`识别到 ${names.length} 个模块，${data.model.modules.reduce((n,m)=>n+m.port_count,0)} 个端口；${data.model.diagnostics.length} 条解析诊断`;
  renderDiagnostics();
}
function escapeHtml(text){ const d=document.createElement("div"); d.textContent=String(text??""); return d.innerHTML; }

async function applyRoles(){
  const roles=roleValues(); if(new Set(Object.values(roles)).size!==3){toast("TOP、子模块 A、子模块 B 必须是三个不同模块",true);return;}
  state.project=(await post("/api/project/new",{model:state.model,roles})).project; state.history=[];state.future=[]; autosave();renderAll(); await suggest(); await validate();
}
function filteredPorts(module){ const q=$("search").value.trim().toLowerCase(), dir=$("directionFilter").value; return module.ports.filter(p=>(!dir||p.direction===dir)&&(!q||`${p.name} ${p.category} ${p.template_source||""}`.toLowerCase().includes(q))); }
function shapeText(p){ if(p.interface)return p.interface; const dims=[...(p.packed||[]),p.width].map(w=>w.expression).join(" × "); const arrays=(p.arrays||[]).map(w=>` [${w.expression}]`).join(""); return `${dims}${arrays}`; }
function renderCanvas(){
  if(!state.project){$("canvas").innerHTML='<div class="empty">请先导入 XLSX 并选择三个模块。</div>';return;}
  $("canvas").style.transform=`scale(${state.zoom})`; $("canvas").innerHTML="";
  chosenModules().forEach((module,index)=>{
    const role=["TOP","子模块 A","子模块 B"][index], card=document.createElement("article"); card.className="module"; card.innerHTML=`<header><strong>${escapeHtml(module.name)}</strong><span class="muted">${role} · ${module.port_count} ports · ${escapeHtml(module.sheet)}</span></header><div class="port-viewport" tabindex="0"><div class="port-spacer"><div class="port-layer"></div></div></div>`;
    $("canvas").appendChild(card); const viewport=card.querySelector(".port-viewport"); const redraw=()=>renderPortWindow(module,viewport); viewport.addEventListener("scroll",()=>{redraw();requestAnimationFrame(drawLines)}); redraw();
  }); requestAnimationFrame(drawLines);
}
function renderPortWindow(module,viewport){
  const ports=filteredPorts(module), spacer=viewport.querySelector(".port-spacer"), layer=viewport.querySelector(".port-layer"); spacer.style.height=`${ports.length*ROW_H}px`; const start=Math.max(0,Math.floor(viewport.scrollTop/ROW_H)-8), end=Math.min(ports.length,start+WINDOW); layer.style.transform=`translateY(${start*ROW_H}px)`; layer.innerHTML="";
  ports.slice(start,end).forEach(p=>{ const row=document.createElement("div"); const kind=p.interface?"interface":p.direction; row.className=`port ${kind} ${state.selected===p.id?"selected":""} ${state.multi.has(p.id)?"multi-selected":""}`; row.dataset.port=p.id; row.draggable=true; row.tabIndex=0; row.title=`${p.category}\n来源 ${p.sheet}:${p.source_row}${p.condition?`\n条件 ${p.condition}`:""}`; row.innerHTML=`<span class="dot" aria-hidden="true"></span><span>${escapeHtml(p.name)}<br><small class="muted">${escapeHtml(p.category)}</small></span><span class="shape">${escapeHtml(p.direction)} · ${escapeHtml(shapeText(p))}</span>`;
    row.addEventListener("dragstart",e=>e.dataTransfer.setData("text/x-xvlink-port",p.id)); row.addEventListener("dragover",e=>{e.preventDefault();row.classList.add("drag-target");});row.addEventListener("dragleave",()=>row.classList.remove("drag-target")); row.addEventListener("drop",e=>{e.preventDefault();row.classList.remove("drag-target");connect(e.dataTransfer.getData("text/x-xvlink-port"),p.id);}); row.addEventListener("click",e=>{if(e.ctrlKey||e.metaKey){state.multi.has(p.id)?state.multi.delete(p.id):state.multi.add(p.id);renderCanvas();return;}if(state.selected&&state.selected!==p.id)connect(state.selected,p.id);else{state.selected=state.selected===p.id?null:p.id;renderCanvas();}}); row.addEventListener("keydown",e=>{if(e.key==="Enter"){if(e.ctrlKey)state.multi.add(p.id);else state.selected=p.id;renderCanvas();}}); layer.appendChild(row);
  });
}
function connect(a,b){ if(!a||!b||a===b)return; const ports=portIndex(); if(!ports[a]||!ports[b]||ports[a].module===ports[b].module){toast("只能连接不同模块的端口",true);return;} mutate(()=>{ const na=state.project.networks.find(n=>n.endpoints.includes(a)), nb=state.project.networks.find(n=>n.endpoints.includes(b)); if(na&&nb&&na!==nb){na.endpoints=[...new Set([...na.endpoints,...nb.endpoints])];state.project.networks=state.project.networks.filter(n=>n!==nb);} else if(na){if(!na.endpoints.includes(b))na.endpoints.push(b);} else if(nb){if(!nb.endpoints.includes(a))nb.endpoints.push(a);} else state.project.networks.push({id:`net-${crypto.randomUUID()}`,endpoints:[a,b]}); state.selected=null; }); }
function drawLines(){ const svg=$("wires");svg.innerHTML=""; if(!state.project)return; const base=svg.getBoundingClientRect(); state.project.networks.forEach(net=>{const nodes=net.endpoints.map(id=>document.querySelector(`[data-port="${CSS.escape(id)}"]`)).filter(Boolean); if(nodes.length<2)return; const first=nodes[0].getBoundingClientRect(); const x1=first.left+first.width/2-base.left,y1=first.top+first.height/2-base.top; nodes.slice(1).forEach(node=>{const r=node.getBoundingClientRect(),x2=r.left+r.width/2-base.left,y2=r.top+r.height/2-base.top,mid=(x1+x2)/2;const path=document.createElementNS("http://www.w3.org/2000/svg","path");path.setAttribute("d",`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);path.dataset.net=net.id;if(state.selectedNet===net.id)path.classList.add("selected-net");path.onclick=()=>{state.selectedNet=net.id;renderAll();};svg.appendChild(path);});}); }
function renderNetworks(){ const ports=portIndex(); $("netCount").textContent=state.project?.networks.length||0; $("networks").innerHTML=(state.project?.networks||[]).map(net=>`<div class="item ${state.selectedNet===net.id?"warning":""}" data-select-net="${escapeHtml(net.id)}"><b>${escapeHtml(net.id)}</b><br><span class="muted">${net.endpoints.map(id=>escapeHtml(id)).join(" ↔ ")}</span><div class="item-actions"><button data-delete-net="${escapeHtml(net.id)}">删除</button></div></div>`).join("")||'<div class="empty">拖拽或依次点击两个端口建立连接</div>';document.querySelectorAll("[data-select-net]").forEach(n=>n.onclick=e=>{if(e.target.tagName!=="BUTTON"){state.selectedNet=n.dataset.selectNet;renderAll();}}); document.querySelectorAll("[data-delete-net]").forEach(b=>b.onclick=()=>mutate(()=>{state.project.networks=state.project.networks.filter(n=>n.id!==b.dataset.deleteNet);state.selectedNet=null;})); }
function renderUnconnected(){ if(!state.project){$("unconnected").innerHTML="";return;} const connected=new Set(state.project.networks.flatMap(n=>n.endpoints)), confirmed=new Set(state.project.confirmed_unconnected), roles=new Set([state.project.roles.child_a,state.project.roles.child_b]); const ports=chosenModules().filter(m=>roles.has(m.name)).flatMap(m=>m.ports).filter(p=>!connected.has(p.id)); $("unconnected").innerHTML=ports.slice(0,500).map(p=>`<div class="item"><span class="${p.direction}">${escapeHtml(p.id)}</span> <span class="badge">${confirmed.has(p.id)?"已确认":"待确认"}</span><br><small class="muted">${p.direction==="input"?"导出接零":"导出空连接"}</small></div>`).join("")+(ports.length>500?`<div class="item muted">另有 ${ports.length-500} 项；使用搜索过滤</div>`:""); }
function renderSuggestions(){ const rejected=new Set(state.project?.rejected_suggestions||[]); $("suggestions").innerHTML=state.suggestions.filter(s=>!rejected.has(s.id)).slice(0,250).map(s=>`<div class="item"><b class="${s.width_warning?"warning":""}">${s.confidence.toUpperCase()} · ${s.score}</b><br><span>${s.endpoints.map(escapeHtml).join(" ↔ ")}</span><br><small class="muted">${s.reasons.map(escapeHtml).join("；")}</small><div class="item-actions"><button data-accept="${s.id}">接受</button><button data-reject="${s.id}">拒绝</button></div></div>`).join("")||'<div class="empty">暂无建议</div>'; document.querySelectorAll("[data-accept]").forEach(b=>b.onclick=()=>acceptSuggestion(b.dataset.accept));document.querySelectorAll("[data-reject]").forEach(b=>b.onclick=()=>mutate(()=>state.project.rejected_suggestions.push(b.dataset.reject))); }
function acceptSuggestion(id){ const s=state.suggestions.find(x=>x.id===id); if(s)connect(s.endpoints[0],s.endpoints[1]); }
async function suggest(){
  if(!state.project)return;
  state.suggestController?.abort();state.suggestController=new AbortController();
  $("suggestBtn").textContent="建议计算中…";$("cancelSuggest").disabled=false;
  try{
    const data=await post("/api/suggestions",{model:state.model,roles:state.project.roles},{signal:state.suggestController.signal});
    state.suggestions=data.suggestions.filter(s=>!state.project.rejected_suggestions.includes(s.id));renderSuggestions();
  }catch(error){if(error.name!=="AbortError")throw error;toast("已取消建议计算");}
  finally{$("suggestBtn").textContent="生成建议";$("cancelSuggest").disabled=true;state.suggestController=null;}
}
async function validate(){ if(!state.project)return; state.diagnostics=(await post("/api/validate",{model:state.model,project:state.project})).diagnostics;renderDiagnostics(); }
function renderDiagnostics(){ const items=state.project?state.diagnostics:(state.model?.diagnostics||[]); $("diagnostics").innerHTML=items.slice(0,500).map(d=>{const level=({"错误":"error","警告":"warning","信息":"info"}[d.level]||d.level);return `<div class="item ${level}"><b>${escapeHtml(d.level)} ${escapeHtml(d.code||"")}</b><br>${escapeHtml(d.message)}</div>`;}).join("")||'<div class="empty">没有诊断</div>'; }
function renderAll(){renderCanvas();renderNetworks();renderUnconnected();renderSuggestions();renderDiagnostics();}

async function preview(){ const data=await post("/api/preview",{model:state.model,project:state.project}); $("previewTable").innerHTML=`<table>${data.preview.map(row=>`<tr>${row.map(v=>`<td>${escapeHtml(v)}</td>`).join("")}</tr>`).join("")}</table>`; $("previewDialog").showModal(); }
async function exportXlsx(){
  await validate();
  if(state.diagnostics.some(d=>d.level==="error")){
    toast("存在 error，已阻止导出",true); return;
  }
  const warnings=state.diagnostics.filter(d=>d.level==="warning").length;
  if(warnings&&!confirm(`仍有 ${warnings} 条 warning。确认继续导出？`))return;
  if(state.model.integration&&!confirm(
    `源工作簿已有集成页签“${state.model.integration.sheet}”。\n`+
    "输出副本中将替换该页签，源文件不变。是否继续？"
  ))return;
  const output=await dialog("save","xlsx"); if(!output)return;
  const result=(await post("/api/export",{
    model:state.model,project:state.project,output
  })).result;
  toast(`导出完成：${result.path}\nxlsx2verilog 校验诊断 ${result.checker_diagnostics.length} 条`);
}
async function saveProject(){ const path=await dialog("save","project");if(path)toast(`工程已保存：${(await post("/api/project/save",{path,project:state.project})).path}`); }
async function openProject(){ const path=await dialog("open","project");if(!path)return;const project=(await api(`/api/project/load?path=${encodeURIComponent(path)}`)).project;await loadWorkbook(project.source);state.project=project;["topRole","childARole","childBRole"].forEach((id,i)=>$(id).value=[project.roles.top,project.roles.child_a,project.roles.child_b][i]);renderAll();await suggest();await validate();if(project.source_changed)toast("源 XLSX 指纹已变化；无法恢复的端点会作为 MISSING_PORT 进入待修复",true); }

function batchSelected(){
  const ids=[...state.multi],ports=portIndex();
  if(ids.length<2){toast("请使用 Ctrl+点击或框选至少两个端口",true);return;}
  const modules=ids.map(id=>ports[id]?.module);
  if(modules.some(x=>!x)||new Set(modules).size!==modules.length){
    toast("批量网络在同一模块中最多选择一个端口",true);return;
  }
  mutate(()=>{
    const touching=state.project.networks.filter(n=>n.endpoints.some(id=>state.multi.has(id)));
    const endpoints=[...new Set([...ids,...touching.flatMap(n=>n.endpoints)])];
    state.project.networks=state.project.networks.filter(n=>!touching.includes(n));
    state.project.networks.push({id:touching[0]?.id||`net-${crypto.randomUUID()}`,endpoints});
    state.multi.clear();
  });
}
async function restoreAutosave(){
  const raw=localStorage.getItem("xvlink-autosave");
  if(!raw){toast("没有可恢复的自动保存",true);return;}
  const project=JSON.parse(raw);await loadWorkbook(project.source);state.project=project;
  ["topRole","childARole","childBRole"].forEach((id,i)=>$(id).value=[project.roles.top,project.roles.child_a,project.roles.child_b][i]);
  renderAll();await suggest();await validate();toast("已恢复最近自动保存");
}

let pointerMode=null;
$("canvasWrap").addEventListener("pointerdown",e=>{
  if(e.button!==0||e.target.closest(".module")||e.target.closest("path"))return;
  if(e.shiftKey){
    pointerMode={kind:"pan",x:e.clientX,y:e.clientY,left:$("canvasWrap").scrollLeft,top:$("canvasWrap").scrollTop};
    return;
  }
  const box=document.createElement("div");box.className="marquee";document.body.appendChild(box);
  pointerMode={kind:"select",x:e.clientX,y:e.clientY,box};
});
window.addEventListener("pointermove",e=>{
  if(!pointerMode)return;
  if(pointerMode.kind==="pan"){
    $("canvasWrap").scrollLeft=pointerMode.left-(e.clientX-pointerMode.x);
    $("canvasWrap").scrollTop=pointerMode.top-(e.clientY-pointerMode.y);return;
  }
  const left=Math.min(pointerMode.x,e.clientX),top=Math.min(pointerMode.y,e.clientY);
  Object.assign(pointerMode.box.style,{left:`${left}px`,top:`${top}px`,width:`${Math.abs(e.clientX-pointerMode.x)}px`,height:`${Math.abs(e.clientY-pointerMode.y)}px`});
});
window.addEventListener("pointerup",e=>{
  if(!pointerMode)return;
  if(pointerMode.kind==="select"){
    const left=Math.min(pointerMode.x,e.clientX),right=Math.max(pointerMode.x,e.clientX),top=Math.min(pointerMode.y,e.clientY),bottom=Math.max(pointerMode.y,e.clientY);
    document.querySelectorAll("[data-port]").forEach(node=>{const r=node.getBoundingClientRect();if(r.right>=left&&r.left<=right&&r.bottom>=top&&r.top<=bottom)state.multi.add(node.dataset.port);});
    pointerMode.box.remove();renderCanvas();
  }
  pointerMode=null;
});

$("pickSource").onclick=async()=>{const p=await dialog("open","xlsx");if(p)$("sourcePath").value=p;};$("loadSource").onclick=()=>loadWorkbook($("sourcePath").value).catch(e=>toast(e.message,true));$("applyRoles").onclick=()=>applyRoles().catch(e=>toast(e.message,true));$("suggestBtn").onclick=()=>suggest().catch(e=>toast(e.message,true));$("cancelSuggest").onclick=()=>state.suggestController?.abort();$("validateBtn").onclick=()=>validate().catch(e=>toast(e.message,true));$("acceptHigh").onclick=()=>mutate(()=>{state.suggestions.filter(s=>s.confidence==="high"&&!s.width_warning).forEach(s=>{const touching=state.project.networks.filter(n=>s.endpoints.some(id=>n.endpoints.includes(id)));if(touching.length){touching[0].endpoints=[...new Set([...touching.flatMap(n=>n.endpoints),...s.endpoints])];state.project.networks=state.project.networks.filter(n=>n===touching[0]||!touching.includes(n));}else state.project.networks.push({id:`net-${crypto.randomUUID()}`,endpoints:[...s.endpoints]});});});$("confirmOpen").onclick=()=>mutate(()=>{const connected=new Set(state.project.networks.flatMap(n=>n.endpoints)),roles=new Set([state.project.roles.child_a,state.project.roles.child_b]);state.project.confirmed_unconnected=state.model.modules.filter(m=>roles.has(m.name)).flatMap(m=>m.ports).filter(p=>!connected.has(p.id)).map(p=>p.id);});$("batchNet").onclick=batchSelected;$("swapChildren").onclick=()=>{if(!state.project)return;mutate(()=>{const old=state.project.roles.child_a;state.project.roles.child_a=state.project.roles.child_b;state.project.roles.child_b=old;});suggest();};$("restoreAutosave").onclick=()=>restoreAutosave().catch(e=>toast(e.message,true));$("search").oninput=renderCanvas;$("directionFilter").onchange=renderCanvas;$("zoom").oninput=e=>{state.zoom=Number(e.target.value)/100;renderCanvas();};$("previewBtn").onclick=()=>preview().catch(e=>toast(e.message,true));$("exportBtn").onclick=()=>exportXlsx().catch(e=>toast(e.message,true));$("saveProject").onclick=()=>saveProject().catch(e=>toast(e.message,true));$("loadProject").onclick=()=>openProject().catch(e=>toast(e.message,true));$("undoBtn").onclick=()=>{if(!state.history.length)return;state.future.push(JSON.stringify({networks:state.project.networks,confirmed_unconnected:state.project.confirmed_unconnected,rejected_suggestions:state.project.rejected_suggestions}));restore(state.history.pop());};$("redoBtn").onclick=()=>{if(!state.future.length)return;state.history.push(JSON.stringify({networks:state.project.networks,confirmed_unconnected:state.project.confirmed_unconnected,rejected_suggestions:state.project.rejected_suggestions}));restore(state.future.pop());};window.addEventListener("resize",drawLines);window.addEventListener("keydown",e=>{if(e.ctrlKey&&e.key.toLowerCase()==="z")$("undoBtn").click();if(e.ctrlKey&&e.key.toLowerCase()==="y")$("redoBtn").click();if((e.key==="Delete"||e.key==="Backspace")&&state.selectedNet&&!e.target.matches("input,select")){const id=state.selectedNet;mutate(()=>{state.project.networks=state.project.networks.filter(n=>n.id!==id);state.selectedNet=null;});}});
