'use strict';
const $ = s => document.querySelector(s);
const el = (tag, className, text) => {const n=document.createElement(tag); if(className)n.className=className;if(text!==undefined)n.textContent=text;return n;};
let state={messages:[],account:null,busy:false}, csrf='', selected=null, requestRunning=false, readerRun=0, retry=null;
let filterAction=false, promptLoaded=false;
const pendingLabels=new Map();
const copiedCodes=new Set();
const copyTimers=new Map();
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

let searchActive=false, searchMessages=[], searchQuery='', searchNext=null, searchBusy=false, searchError='', searchRun=0, inboxScroll=0;
let showFiltered=true;
try{showFiltered=localStorage.getItem('mail-show-filtered')!=='false';}catch{}
function visibleMessages(){if(searchActive)return searchMessages;return state.messages.filter(m=>showFiltered || (pendingLabels.has(m.id)?pendingLabels.get(m.id):m.expectedKeep) || m.decision!=='hide');}
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
  const main=el('div','mail-main');main.append(button);
  if(m.otp?.code && /^\d{4,8}$/.test(m.otp.code)){
    main.classList.add('has-code');
    const copy=el('button','code-copy');copy.type='button';copy.title='Copy verification code';copy.setAttribute('aria-label','Copy verification code');
    copy.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V4H4v12h4"/></svg>';
    if(copiedCodes.has(m.id)){copy.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';copy.title='Copied';copy.setAttribute('aria-label','Copied');}
    copy.onclick=async event=>{
      event.stopPropagation();
      try{await navigator.clipboard.writeText(m.otp.code);}catch{notice('Could not copy. Please try again.');return;}
      copiedCodes.add(m.id);clearTimeout(copyTimers.get(m.id));
      copyTimers.set(m.id,setTimeout(()=>{copiedCodes.delete(m.id);copyTimers.delete(m.id);render();},2000));
      render();await markCopiedRead(m.id);
    };
    main.append(copy);
  }
  if(searchActive){wrap.append(main);return wrap;}
  const check=el('input','expected-keep');check.type='checkbox';check.checked=pendingLabels.has(m.id)?pendingLabels.get(m.id):m.expectedKeep;
  check.setAttribute('aria-label','Keep '+m.sender+': '+m.subject);check.title='Expected to keep';check.disabled=pendingLabels.has(m.id);
  check.onchange=()=>saveExpected(m.id,check.checked);
  const correct=m.decision===(check.checked?'keep':'hide');
  const result=el('span','filter-result'+(correct?' correct':''),{keep:'✓',uncertain:'−',hide:'×'}[m.decision] || '');
  const label={keep:'Keep',uncertain:'Uncertain',hide:'Hide'}[m.decision] || 'Not evaluated';
  result.setAttribute('aria-label',label+(correct?' — correct':''));result.title=label+(correct?' — correct':'')+(m.confidence===undefined?'':` | Confidence: ${(m.confidence*100).toFixed(1)}% | P(keep): ${(m.probabilities.keep*100).toFixed(1)}%`);
  wrap.append(result,check,main);return wrap;
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
  $('#search-toggle').hidden=!state.account;
  $('#search-form').hidden=!searchActive;
  $('#prompt-editor').hidden=searchActive;
  $('#search-status').hidden=!searchActive;
  $('#search-status').textContent=searchBusy?'Searching…':searchError || (searchQuery?`${searchMessages.length} results`:'');
  $('#search-more').hidden=!searchActive || !searchNext;$('#search-more').disabled=searchBusy;
  $('#reset-filter').hidden=searchActive || !state.account || !state.filterEnabled;
  $('#run-filter').hidden=searchActive || !state.account;
  $('#filtered-toggle').hidden=searchActive || !state.account;
  if(!promptLoaded && state.prompt!==undefined){$('#filter-prompt').value=state.prompt;promptLoaded=true;}
  $('#filter-score').hidden=searchActive || !state.account;
  const score=state.score;
  $('#filter-score').textContent=score?.evaluated?`${score.correct} / ${score.total} correct`:'—';
  $('#filter-score').title=score?`${score.evaluated} / ${score.total} evaluated; uncertain counts as incorrect`:'';
  $('#reset-filter').disabled=filterAction;$('#run-filter').disabled=filterAction || state.busy || pendingLabels.size>0;
  if(focused && keyboardNavigation){const b=[...document.querySelectorAll('.mail-item')].find(b=>b.dataset.id===focused);b?.focus({preventScroll:true});}
  $('#empty').hidden=visibleMessages().length>0 || (searchActive?searchBusy || !searchQuery || !!searchError:state.busy);
  $('#empty').textContent=searchActive?'No results':state.error?'Mail is temporarily unavailable':'No mail yet';
  if(state.error)notice(state.error,state.needsLogin?connect:()=>sync(false,true));else if(!requestRunning)notice('');
  if(selected && !visibleMessages().some(m=>m.id===selected))closeReader();
}
async function markCopiedRead(id){
  try{
    await api('read',{id});
    for(const m of [...state.messages,...searchMessages])if(m.id===id)m.unread=false;
    render();
  }catch{notice('Copied, but could not mark as read.',()=>markCopiedRead(id));}
}
function partTime(sent){
  return (sent || '').replace(/^[A-Za-z]+,\s*/,'').replace(/\s*\(UTC.*$/,'').replace(/\s+at\s+/,' ').replace(/(\d{1,2}:\d{2}):\d{2}/,'$1').replace(/\b(January|February|March|April|June|July|August|September|October|November|December)\b/g,m=>m.slice(0,3));
}
function updateReaderTheme(){
  const style=getComputedStyle(document.documentElement);
  for(const frame of document.querySelectorAll('.conversation-html')){
    const root=frame.contentDocument?.documentElement;if(!root)continue;
    for(const name of ['bg','ink','blue'])root.style.setProperty('--mail-'+name,style.getPropertyValue(name==='bg' && frame.closest('.quoted-message')?'--sidebar':'--'+name));
  }
}
function mailContent(part){
  const content=el('div','part-content');
  if(!part.html){content.textContent=part.body || '';return content;}
  const frame=el('iframe','mail-html conversation-html');frame.title='Email content';
  // Scripts and forms remain forbidden. Same-origin lets the parent size this
  // sanitized document without injecting or enabling any email scripts.
  frame.setAttribute('sandbox','allow-same-origin allow-popups allow-popups-to-escape-sandbox');frame.referrerPolicy='no-referrer';
  frame.onload=()=>{
    const doc=frame.contentDocument;if(!doc)return;
    updateReaderTheme();
    const resize=()=>{if(frame.isConnected)frame.style.height=Math.max(60,doc.documentElement.scrollHeight,doc.body.scrollHeight)+'px';};
    resize();const observer=new ResizeObserver(resize);observer.observe(doc.body);
    frame._cleanup=()=>observer.disconnect();
    for(const img of doc.images)img.addEventListener('load',resize,{once:true});
  };
  const style=getComputedStyle(document.documentElement);
  const ink=style.getPropertyValue('--ink').trim(),bg=style.getPropertyValue('--bg').trim(),blue=style.getPropertyValue('--blue').trim();
  frame.srcdoc=`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><style>html,body{margin:0;padding:0;background:var(--mail-bg,${bg});color:var(--mail-ink,${ink});font:20px/1.65 -apple-system,sans-serif;overflow-wrap:anywhere}body{display:flow-root}body *{max-width:100%;box-sizing:border-box}p,div,span,font,td,li{color:inherit!important;background-color:transparent!important;font-family:inherit!important;font-size:inherit!important}img{height:auto}table{max-width:100%}pre{white-space:pre-wrap}a{color:var(--mail-blue,${blue})!important}hr{border:0;border-top:1px solid ${ink}22}blockquote{margin:0}p:empty,div:empty{display:none}</style>${part.html}`;
  content.append(frame);return content;
}
function addressLines(from,to){
  const addresses=value=>[...new Set(String(value || '').match(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9.-]*[A-Z0-9])?/gi) || [])];
  const recipients=addresses(to);
  const own=new Set([state.account,...(state.ownAddresses || [])].filter(Boolean).map(a=>a.toLowerCase()));
  const mine=recipients.filter(a=>own.has(a.toLowerCase()));
  return `From: ${addresses(from).join(', ') || 'Unknown'}\nTo: ${(mine.length?mine:recipients).join(', ') || 'Unknown'}`;
}
function renderConversation(detail,followupsOnly=false){
  const root=$('#reader-body');if(!followupsOnly)root.replaceChildren();
  const parts=detail.conversation?.length?detail.conversation:[detail];
  const history=parts.slice(1).reverse();
  for(const part of (followupsOnly?detail.replies:[...history,parts[0]])){
    const isCurrent=!followupsOnly && part===parts[0];
    const quote=el('details','quoted-message'),summary=el('summary','quote-summary');
    summary.append(el('span','quote-sender',part.sender || (isCurrent?detail.sender:'Previous message')),el('time','quote-time',partTime(part.sent)));
    summary.title=part.attribution || [part.email,part.subject].filter(Boolean).join(' · ');
    quote.append(summary);
    const expand=()=>{
      if(quote.open && !quote.querySelector('.part-content')){
        quote.append(el('div','quote-address',addressLines(part.email,part.to)));
        quote.append(mailContent(part));
      }
    };
    quote.addEventListener('toggle',expand);
    root.append(quote);
    if(isCurrent){quote.open=true;expand();}
  }
  if(followupsOnly)return;
  const current=parts[0];
  $('#reader-address').textContent=addressLines(detail.email,current.to || detail.mailbox);
}
const attachmentURLs=new Set();let attachmentGeneration=0,previewRun=0,previewFile=null,previewPage=0,previewPages=1,modalURL=null;
function attachmentURL(id,a,preview=false,page=0){return '/api/attachment'+(preview?'-preview':'')+'?id='+encodeURIComponent(id)+'&part='+encodeURIComponent(a.id)+(preview?'&page='+page:'');}
function clearAttachmentPreviews(){++attachmentGeneration;$('#attachment-viewer').close();for(const url of attachmentURLs)URL.revokeObjectURL(url);attachmentURLs.clear();}
function fileSize(bytes){return bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':Math.max(1,Math.round(bytes/1024))+' KB';}
async function loadAttachmentPreview(id,a,page){
  const response=await fetch(attachmentURL(id,a,true,page));
  if(!response.ok)throw new Error('Preview unavailable');
  const blob=await response.blob();const url=URL.createObjectURL(blob);attachmentURLs.add(url);
  return {url,pages:Number(response.headers.get('X-Page-Count')) || 1};
}
function renderAttachments(id,attachments){
  const generation=attachmentGeneration;
  for(const a of attachments.filter(a=>!a.inline)){
    const card=el('button','attachment-card');card.type='button';card.setAttribute('aria-label','Preview '+a.name);
    const surface=el('div','attachment-surface'+(a.preview==='pdf'?' pdf-preview':'')),footer=el('div','attachment-footer');
    surface.append(el('span','attachment-placeholder',a.preview?'Loading preview…':a.name.split('.').pop().toUpperCase().slice(0,10)));
    footer.append(el('span','attachment-name',a.name),el('span','attachment-size',fileSize(a.size || 0)));card.append(surface,footer);
    card.onclick=()=>openAttachment(id,a);$('#attachments').append(card);
    if(a.preview)loadAttachmentPreview(id,a,0).then(({url})=>{
      if(generation!==attachmentGeneration){URL.revokeObjectURL(url);attachmentURLs.delete(url);return;}
      const img=el('img');img.alt=a.name;img.src=url;surface.replaceChildren(img);
    }).catch(()=>{if(generation===attachmentGeneration)surface.textContent='Preview unavailable';});
  }
}
async function openAttachment(id,a){
  previewFile={id,a};previewPage=0;previewPages=1;$('#preview-name').textContent=a.name;
  $('#preview-download').href=attachmentURL(id,a);$('#preview-download').download=a.name;
  if(!$('#attachment-viewer').open)$('#attachment-viewer').showModal();await showAttachmentPage();
}
async function showAttachmentPage(){
  const run=++previewRun;const {id,a}=previewFile;
  $('#preview-content').textContent=a.preview?'Loading preview…':'Preview is not available for this file type.';$('#preview-pages').hidden=true;
  if(modalURL){URL.revokeObjectURL(modalURL);attachmentURLs.delete(modalURL);modalURL=null;}
  if(!a.preview)return;
  try{
    const result=await loadAttachmentPreview(id,a,previewPage);
    if(run!==previewRun){URL.revokeObjectURL(result.url);attachmentURLs.delete(result.url);return;}
    modalURL=result.url;previewPages=result.pages;
    const img=el('img');img.src=result.url;img.alt=a.name+(a.preview==='pdf'?' — page '+(previewPage+1):'');$('#preview-content').replaceChildren(img);
    $('#preview-pages').hidden=previewPages<=1;$('#preview-page').textContent=(previewPage+1)+' / '+previewPages;
    $('#preview-prev').disabled=previewPage===0;$('#preview-next').disabled=previewPage>=previewPages-1;
  }catch{if(run===previewRun)$('#preview-content').textContent='Preview unavailable. You can still download this file.';}
}
$('#preview-close').onclick=()=>$('#attachment-viewer').close();
$('#attachment-viewer').addEventListener('close',()=>{++previewRun;if(modalURL){URL.revokeObjectURL(modalURL);attachmentURLs.delete(modalURL);modalURL=null;}$('#preview-content').replaceChildren();});
$('#attachment-viewer').addEventListener('click',e=>{if(e.target===$('#attachment-viewer'))$('#attachment-viewer').close();});
$('#preview-prev').onclick=()=>{if(previewPage>0){previewPage--;showAttachmentPage();}};
$('#preview-next').onclick=()=>{if(previewPage<previewPages-1){previewPage++;showAttachmentPage();}};
async function openMessage(id){
  clearMailHover();
  const run=++readerRun;selected=id;document.body.classList.add('reading');$('#message').hidden=false;
  const m=visibleMessages().find(m=>m.id===id);if(!m)return;
  $('#reader-sender').textContent=m.sender;$('#reader-accent').style.setProperty('--source',m.color);$('#reader-subject').textContent=m.subject;
  $('#reader-time').textContent=dateText(m,true);$('#reader-address').textContent=addressLines(m.email,m.mailbox);
  $('#reader-body').querySelectorAll('iframe').forEach(f=>f._cleanup?.());
  clearAttachmentPreviews();
  $('#reader-body').textContent='';$('#attachments').replaceChildren();$('#reader').scrollTop=0;render();
  try{
    const detail=await api('read',{id});if(run!==readerRun)return;
    m.unread=false;const cached=state.messages.find(item=>item.id===id);if(cached)cached.unread=false;render();
    renderConversation(detail);
    renderAttachments(id,detail.attachments || []);
    api('replies',{id}).then(result=>{
      if(run!==readerRun)return;
      renderConversation({...detail,replies:result.replies || []},true);
      if(result.error)notice(result.error,()=>openMessage(id));
    }).catch(()=>{if(run===readerRun)notice('Could not load replies.',()=>openMessage(id));});
  }catch(e){if(run===readerRun)notice(e.message,()=>openMessage(id));}
}
function closeReader(){clearMailHover();const previous=selected;++readerRun;selected=null;document.body.classList.remove('reading');$('#message').hidden=true;document.querySelectorAll('.mail-row.selected').forEach(n=>n.classList.remove('selected'));document.querySelectorAll('.mail-item[aria-pressed=true]').forEach(n=>n.setAttribute('aria-pressed','false'));if(keyboardNavigation)[...document.querySelectorAll('.mail-item')].find(n=>n.dataset.id===previous)?.focus({preventScroll:true});}
$('#back').onclick=closeReader;document.addEventListener('keydown',e=>{if(e.key==='Escape'){if($('#attachment-viewer').open)return;if(selected)closeReader();else if(searchActive)closeSearch();}});
function closeSearch(){++searchRun;searchActive=false;searchBusy=false;searchError='';closeReader();render();$('.inbox').scrollTop=inboxScroll;$('#search-toggle').focus();}
$('#search-toggle').onclick=()=>{closeReader();if(!searchActive)inboxScroll=$('.inbox').scrollTop;searchActive=true;render();$('.inbox').scrollTop=0;$('#search-query').focus();};
$('#search-close').onclick=closeSearch;
$('#search-form').onsubmit=e=>{e.preventDefault();runSearch();};
$('#search-more').onclick=()=>runSearch(true);
async function runSearch(more=false){
  const query=more?searchQuery:$('#search-query').value.trim();if(!query)return;
  const run=++searchRun;const page=more?searchNext:null;
  if(!more){searchMessages=[];searchNext=null;}searchQuery=query;searchBusy=true;searchError='';render();
  try{
    const value=await api('search',{query,page});if(run!==searchRun || !searchActive)return;
    searchMessages=[...new Map([...searchMessages,...value.messages].map(m=>[m.id,m])).values()];searchNext=value.nextPage;
  }catch(e){if(run===searchRun)searchError=e.message;}
  finally{if(run===searchRun){searchBusy=false;render();}}
}
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
function setTheme(value){theme=['system','light','dark'].includes(value)?value:'system';document.documentElement.dataset.theme=theme;const label='Appearance: '+theme[0].toUpperCase()+theme.slice(1);$('#appearance').setAttribute('aria-label',label);$('#appearance').title=label+' — click to change';$('#appearance use').setAttribute('href','#'+({system:'system',light:'sun',dark:'moon'}[theme]));try{localStorage.setItem('mail-theme',theme);}catch{}updateReaderTheme();}
$('#appearance').onclick=()=>setTheme({system:'light',light:'dark',dark:'system'}[theme]);setTheme(theme);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',updateReaderTheme);
async function boot(){
  try{apply(await api('state'));if(state.account)sync();}catch(e){notice('Could not connect to Mail.',boot);}
  if(new URLSearchParams(location.search).has('demo'))history.replaceState(null,'','/');
  if(new URLSearchParams(location.search).has('login')){notice('Google sign-in did not finish. Please try again.',connect);history.replaceState(null,'','/');}
}
setInterval(poll,1500);setInterval(()=>{if(!document.hidden)sync();},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){if(state.busy)poll();else sync();}});
boot();
