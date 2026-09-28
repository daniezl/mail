'use strict';
const $ = s => document.querySelector(s);
const el = (tag, className, text) => {const n=document.createElement(tag); if(className)n.className=className;if(text!==undefined)n.textContent=text;return n;};
let state={messages:[],account:null,busy:false}, csrf='', selected=null, requestRunning=false, readerRun=0, retry=null;
let filterAction=false, promptLoaded=false;
const pendingLabels=new Map();
let hoveredRow=null, keyboardNavigation=false;
function clearMailHover(){hoveredRow?.classList.remove('pointer-hover');hoveredRow=null;}
document.addEventListener('pointermove',e=>{
  const row=e.pointerType==='mouse' && !document.body.classList.contains('reading') ? e.target.closest?.('.mail-row') : null;
  if(row===hoveredRow)return;
  clearMailHover();hoveredRow=row;hoveredRow?.classList.add('pointer-hover');
});
document.addEventListener('pointerdown',()=>{keyboardNavigation=false;clearMailHover();},true);
document.addEventListener('keydown',()=>{keyboardNavigation=true;clearMailHover();},true);
document.documentElement.addEventListener('pointerleave',clearMailHover);
document.addEventListener('pointercancel',clearMailHover);
document.addEventListener('scroll',clearMailHover,true);
window.addEventListener('blur',clearMailHover);
document.addEventListener('visibilitychange',clearMailHover);

let showFiltered=true;
try{showFiltered=localStorage.getItem('mail-show-filtered')!=='false';}catch{}
function visibleMessages(){return state.messages.filter(m=>showFiltered || (pendingLabels.has(m.id)?pendingLabels.get(m.id):m.expectedKeep) || m.decision!=='hide');}
$('#show-filtered').checked=showFiltered;
$('#show-filtered').onchange=()=>{showFiltered=$('#show-filtered').checked;try{localStorage.setItem('mail-show-filtered',String(showFiltered));}catch{}render();};
function notice(text, action=null){$('#notice-text').textContent=text;$('#notice').hidden=!text;$('#retry').hidden=!action;retry=action;}
async function api(path,data){
  const response=await fetch('/api/'+path,{method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:data===undefined?undefined:JSON.stringify(data)});
  const value=await response.json();if(!response.ok)throw new Error(value.error || 'Could not connect.');return value;
}
function apply(value){if(value.generation!==undefined && value.generation<(state.generation || 0))return;state={...state,...value};if(value.csrf)csrf=value.csrf;render();}
function dateText(m,full=false){const d=new Date(m.date*1000);return d.toLocaleString('en-US',full?{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}:d.toDateString()===new Date().toDateString()?{hour:'numeric',minute:'2-digit'}:{month:'short',day:'numeric'});}
function row(m){
  const wrap=el('div','mail-row'+(selected===m.id?' selected':''));wrap.setAttribute('role','listitem');wrap.dataset.id=m.id;
  const button=el('button','mail-item'+(m.unread?' unread':''));button.setAttribute('aria-label',`${m.unread?'Unread. ':''}${m.sender}. ${m.subject}`);button.setAttribute('aria-pressed',String(selected===m.id));button.dataset.id=m.id;
  const meta=el('span','meta'), accent=el('span','accent');accent.style.setProperty('--source',m.color);accent.title=m.mailbox || 'Unknown source';accent.setAttribute('aria-hidden','true');
  const time=el('time','',dateText(m));time.dateTime=new Date(m.date*1000).toISOString();
  meta.append(accent,el('span','sender',m.sender));
  const subject=el('span','subject',m.subject);subject.title=m.subject;
  button.append(meta,subject,time,el('span','unread-dot'));button.onclick=()=>openMessage(m.id);
  const check=el('input','expected-keep');check.type='checkbox';check.checked=pendingLabels.has(m.id)?pendingLabels.get(m.id):m.expectedKeep;
  check.setAttribute('aria-label','Keep '+m.sender+': '+m.subject);check.title='Expected to keep';check.disabled=pendingLabels.has(m.id);
  check.onchange=()=>saveExpected(m.id,check.checked);
  const correct=m.decision===(check.checked?'keep':'hide');
  const result=el('span','filter-result'+(correct?' correct':''),{keep:'✓',uncertain:'−',hide:'×'}[m.decision] || '');
  const label={keep:'Keep',uncertain:'Uncertain',hide:'Hide'}[m.decision] || 'Not evaluated';
  result.setAttribute('aria-label',label+(correct?' — correct':''));result.title=label+(correct?' — correct':'')+(m.confidence===undefined?'':` | Confidence: ${(m.confidence*100).toFixed(1)}% | P(keep): ${(m.probabilities.keep*100).toFixed(1)}%`);
  wrap.append(result,check,button);return wrap;
}
function renderRows(){
  const messages=visibleMessages(), list=$('#mail-list'), wanted=new Set(messages.map(m=>m.id));
  const existing=new Map([...list.children].map(n=>[n.dataset.id,n]));
  if([...existing.keys()].join(',')!==messages.map(m=>m.id).join(','))clearMailHover();
  for(const [id,node] of existing){if(!wanted.has(id)){if(node===hoveredRow)clearMailHover();node.remove();}}
  messages.forEach((m,i)=>{
    let node=existing.get(m.id);const updated=row(m);
    if(node && node.isConnected){node.replaceChildren(...updated.childNodes);node.className=updated.className+(node===hoveredRow?' pointer-hover':'');}
    else{
      const next=messages.slice(i+1).map(m=>existing.get(m.id)).find(n=>n?.isConnected);
      list.insertBefore(updated,next || null);
    }
  });
}
function render(){
  const focused=document.activeElement?.dataset?.id;
  $('#login').hidden=!!state.account;$('#workspace').hidden=!state.account;
  $('#connection-state').textContent=state.busy?(state.classifying?(state.progress?`Filtering ${state.progress.done} / ${state.progress.total}…`:'Filtering…'):''):'';
  renderRows();
  $('#reset-filter').hidden=!state.account || !state.filterEnabled;
  $('#run-filter').hidden=!state.account;
  $('#filtered-toggle').hidden=!state.account;
  if(!promptLoaded && state.prompt!==undefined){$('#filter-prompt').value=state.prompt;promptLoaded=true;}
  $('#filter-score').hidden=!state.account;
  const score=state.score;
  $('#filter-score').textContent=score?.evaluated?`${score.correct} / ${score.total} correct`:'—';
  $('#filter-score').title=score?`${score.evaluated} / ${score.total} evaluated; uncertain counts as incorrect`:'';
  $('#reset-filter').disabled=filterAction;$('#run-filter').disabled=filterAction || state.busy || pendingLabels.size>0;
  if(focused && keyboardNavigation){const b=[...document.querySelectorAll('.mail-item')].find(b=>b.dataset.id===focused);b?.focus({preventScroll:true});}
  $('#empty').hidden=visibleMessages().length>0 || state.busy;
  $('#empty').textContent=state.error?'Mail is temporarily unavailable':'No mail yet';
  if(state.error)notice(state.error,state.needsLogin?connect:()=>sync(false,true));else if(!requestRunning)notice('');
  if(selected && !visibleMessages().some(m=>m.id===selected))closeReader();
}
async function openMessage(id){
  clearMailHover();
  const run=++readerRun;selected=id;document.body.classList.add('reading');$('#message').hidden=false;
  const m=state.messages.find(m=>m.id===id);if(!m)return;
  $('#reader-sender').textContent=m.sender;$('#reader-accent').style.setProperty('--source',m.color);$('#reader-subject').textContent=m.subject;
  $('#reader-time').textContent=dateText(m,true);$('#reader-address').textContent=`${m.email}\nTo ${m.mailbox || 'Unknown'}`;
  $('#reader-body').textContent='';$('#attachments').replaceChildren();$('#reader').scrollTop=0;render();
  try{
    const detail=await api('read',{id});if(run!==readerRun)return;
    m.unread=false;render();
    if(detail.html){
      const frame=el('iframe','mail-html');frame.title='Email content';frame.setAttribute('sandbox','allow-popups allow-popups-to-escape-sandbox');frame.referrerPolicy='no-referrer';
      frame.srcdoc=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><style>body{font:20px/1.6 -apple-system,sans-serif;margin:12px;overflow-wrap:anywhere;color:#292a28;background:#fff}img,table{max-width:100%}pre{white-space:pre-wrap}a{color:#4175bb}</style>${detail.html}`;
      $('#reader-body').replaceChildren(frame);
    }else $('#reader-body').textContent=detail.body;
    for(const a of detail.attachments || []){const link=el('a','attachment',a.name);link.href='/api/attachment?id='+encodeURIComponent(id)+'&part='+encodeURIComponent(a.id);link.download=a.name;$('#attachments').append(link);}
  }catch(e){notice(e.message,()=>openMessage(id));}
}
function closeReader(){clearMailHover();const previous=selected;++readerRun;selected=null;document.body.classList.remove('reading');$('#message').hidden=true;document.querySelectorAll('.mail-row.selected').forEach(n=>n.classList.remove('selected'));document.querySelectorAll('.mail-item[aria-pressed=true]').forEach(n=>n.setAttribute('aria-pressed','false'));if(keyboardNavigation)[...document.querySelectorAll('.mail-item')].find(n=>n.dataset.id===previous)?.focus({preventScroll:true});}
$('#back').onclick=closeReader;document.addEventListener('keydown',e=>{if(e.key==='Escape')closeReader();});
async function connect(){
  $('#connect').disabled=true;
  try{const r=await api('connect',{});location.assign(r.url);}catch(e){notice(e.message,connect);$('#connect').disabled=false;}
}
$('#connect').onclick=connect;
$('#retry').onclick=()=>{const fn=retry;notice('');fn?.();};
async function sync(more=false,force=false){
  if(requestRunning || state.busy || !state.account)return;
  requestRunning=true;
  try{apply(await api('sync',{more,retry:force}));}catch(e){notice(e.message,()=>sync(more,force));}finally{requestRunning=false;}
}
async function poll(){if(document.hidden || !state.busy)return;try{apply(await api('state'));}catch(e){notice('Connection lost. Please retry.',boot);}}
async function saveExpected(id,keep){
  pendingLabels.set(id,keep);render();
  try{const value=await api('filter/expected',{id,keep});pendingLabels.delete(id);apply(value);}
  catch(e){pendingLabels.delete(id);render();notice(e.message,()=>saveExpected(id,keep));}
}
async function filterControl(action){
  if(filterAction)return;filterAction=true;render();
  let failure;
  try{apply(await api('filter/'+action,action==='run'?{prompt:$('#filter-prompt').value}:{}));}catch(e){failure=e;}
  finally{filterAction=false;render();}
  if(failure)notice(failure.message,()=>filterControl(action));
}
$('#reset-filter').onclick=()=>filterControl('reset');
$('#run-filter').onclick=()=>filterControl('run');
let theme='system';try{theme=localStorage.getItem('mail-theme') || 'system';}catch{}
function setTheme(value){theme=['system','light','dark'].includes(value)?value:'system';document.documentElement.dataset.theme=theme;const label='Appearance: '+theme[0].toUpperCase()+theme.slice(1);$('#appearance').setAttribute('aria-label',label);$('#appearance').title=label+' — click to change';$('#appearance use').setAttribute('href','#'+({system:'system',light:'sun',dark:'moon'}[theme]));try{localStorage.setItem('mail-theme',theme);}catch{}}
$('#appearance').onclick=()=>setTheme({system:'light',light:'dark',dark:'system'}[theme]);setTheme(theme);
async function boot(){
  try{apply(await api('state'));if(state.account)sync();}catch(e){notice('Could not connect to Mail.',boot);}
  if(new URLSearchParams(location.search).has('demo'))history.replaceState(null,'','/');
  if(new URLSearchParams(location.search).has('login')){notice('Google sign-in did not finish. Please try again.',connect);history.replaceState(null,'','/');}
}
setInterval(poll,1500);setInterval(()=>{if(!document.hidden)sync();},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){if(state.busy)poll();else sync();}});
boot();
