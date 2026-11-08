// Synthetic-only Chromium smoke test; no dependencies, corpus, or external requests.
// CHROME=/path/to/chrome node tests/browser_review_page.mjs [evidence-directory]
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const out=process.argv[2] || '/private/tmp/opencode/review-integrated-evidence';
fs.mkdirSync(out,{recursive:true});
const text='🧾 SYNTHETIC DOCUMENT ONLY\nProvide a signed claim form.\n<img src=x onerror="window.injected=true">\nEND OF COMPLETE SOURCE';
const quote='signed claim form', start=Array.from(text.split(quote)[0]).length;
const record={document_id:'SYNTH-001',field:'claim_checklist',document_sha256:createHash('sha256').update(text).digest('hex'),full_document_text:text,split:'train',candidate_status:'source_checks_passed',candidate_labels:['signed claim form','signed claim form - secondary'],correction_type:'array',batch:'8a',task:'claim_checklist',set:'pilot',existing_gt_fields:{claim_checklist:[]},evidence:[{start,end:start+quote.length,quote}],source_url:'javascript:alert(1)'};
let packet={schema:'mailroom-review-packet/v1',packet_id:'synthetic-packet-1',state_revision:'r1',decision_key:{NOT_REVIEWED:'Not reviewed',ACCEPT:'Accept source-checked proposal',CORRECT:'Supply replacement JSON',REJECT:'Reject',NEEDS_SOURCE:'Needs source'},records:[record,{...record,document_id:'SYNTH-002',field:'entity_name',batch:'9a',task:'corporate',set:'pilot',candidate_labels:'Example Corporation',dojo_type:'name',correction_type:'string',source_url:'https://example.invalid/source',model_outputs:[{model:'synthetic/model-a',value:'Example Corporation',status:'present',evidence:'Example Corporation',evidence_spans:[]},{model:'synthetic/model-b',value:null,status:'not_found',evidence:'',evidence_spans:[]}]}],launch_enabled:true,review_states:[],saved_decisions:[],saved_history:[],checkpoint_enabled:true,tracker:{name:'Synthetic campaign',loaded_target_fields:2,human_confirmed_fields:0,remaining_loaded_fields:2,checkpointed_fields:0,scope:'Synthetic tracker only; review is not task approval.',tasks:[{id:'A',task:'claim_checklist',status:'pending',documents_total:2,ready:1,reviewed:0,remaining:2,loaded_target_fields:1},{id:'B',task:'corporate',status:'approved',documents_total:1,ready:1,reviewed:1,remaining:0,loaded_target_fields:1}]}};
const template=fs.readFileSync(new URL('../src/mailroom_eda/labeling/review_page.html',import.meta.url),'utf8');
packet.revision_queue=[{document_id:'SYNTH-PRODUCTION',field:'entity_name',source_dataset:'synthetic/public',source_revision:'public-sha',split:'test',status:'human_confirmed',value:'Example Corporation'}];
Object.assign(packet.tracker,{private_source:{repo:'synthetic/private',revision:'private-sha',status:'connected'},campaign_file:'synthetic/campaign.yaml',approval_file:'synthetic/approvals.yaml'});
assert.equal(template.split('__REVIEW_PACKET_JSON__').length-1,1);
assert(!template.includes('innerHTML'));
let saves=0, checkpoints=0, failSave=false, posts=[];
const checks=['Unicode offsets','full source','no HTML injection','safe source URLs','native values','task filters','save contract','receipt/history hydration','localStorage resume','same-revision reload','409 retains draft','changed-revision explicit discard','checkpoint','model answers table','launch panel targets and kind labels','loud preflight checklist','api key cleared and never stored','typed-cap confirmation','status polling and reload'];
const check=(name)=>checks.push(name);
// Launch mock: the real checks live in tests/test_labeling_launch.py; this serves the shapes the page renders.
const SECRET='sk-or-v1-BROWSER-SECRET-key-4411';
const launch={preflights:[], starts:[], statusReads:0, state:'idle', reloads:0};
const launchTargets=[
  {id:'campaign:1.1',group:'Campaign',title:'1.1 · cuad / calibration',task:'cuad',set:'calibration',models:['synthetic/model-a'],reasoning:'low',cap_usd:0.3,needs:[],kind:'qualification',kind_label:'Qualification \u2014 re-labels gold documents to score models',status:'ready',blocker:'',models_resolved:true},
  {id:'campaign:7a',group:'Campaign',title:'7a · correspondence / pilot',task:'correspondence',set:'pilot',models:['synthetic/model-a'],reasoning:'off',cap_usd:0.15,needs:[],kind:'labeling',kind_label:'Labeling \u2014 documents that need labels',status:'ready',blocker:'',models_resolved:true},
  {id:'trial:correspondence',group:'Public trial',title:'correspondence · correspondence / remainder (public dataset)',task:'correspondence',set:'remainder',models:['synthetic/model-a','synthetic/model-b'],reasoning:{'synthetic/model-a':'off','synthetic/model-b':'low'},description:'DeepSeek runs at reasoning off in trials.',cap_usd:0.25,needs:[],kind:'labeling',kind_label:'Labeling \u2014 documents that need labels',status:'ready',blocker:'',models_resolved:true}];
function launchItems(body){
  const items=[{id:'launch_enabled',title:'Launching is enabled',status:'pass',detail:'enabled',fix:''}];
  if(!/^[A-Z]{2,6}$/.test(body.initials||'')) items.push({id:'initials',title:'Operator initials',status:'fail',detail:'bad initials',fix:'enter 2-6 capital letters, e.g. JB'});
  else items.push({id:'initials',title:'Operator initials',status:'pass',detail:body.initials,fix:''});
  items.push({id:'ping',title:'Route not pinged',status:'warn',detail:'older than 24h',fix:'run ping'},{id:'hf_checkpoint',title:'HF checkpoint',status:'skip',detail:'trials are not checkpointed',fix:''});
  return items;
}
const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');
  if(req.url==='/review.html') {res.setHeader('Content-Type','text/html');res.end(template.replace('__REVIEW_PACKET_JSON__',JSON.stringify(packet).replaceAll('<','\\u003c')));return;}
  if(req.url==='/axe.js') {res.setHeader('Content-Type','text/javascript');if(!process.env.AXE_PATH){res.writeHead(404);res.end();return;}res.end(fs.readFileSync(process.env.AXE_PATH));return;}
  res.setHeader('Content-Type','application/json');
  if(req.url==='/review-data.json') {launch.reloads++;res.end(JSON.stringify(packet));return;}
  if(req.url==='/api/launch/options') {res.end(JSON.stringify({enabled:true,campaign:'synthetic',targets:launchTargets,trial:{default_documents:2,max_documents:5,cap_ceiling_usd:0.25}}));return;}
  if(req.url==='/api/launch/status') {
    launch.statusReads++;
    if(launch.state==='running'&&launch.statusReads>launch.runningSince+1) launch.state='succeeded';
    res.end(JSON.stringify({state:launch.state,target:launch.state==='idle'?null:{id:'campaign:7a',cap_usd:0.15},started_utc:null,finished_utc:null,log_tail:launch.state==='idle'?[]:['chunk 7a started','1 call done'],result:launch.state==='succeeded'?{spent_usd:0.0012,packet:'7a/7a-review-data.json'}:null,error:null}));return;}
  let raw=''; for await(const chunk of req) raw+=chunk;
  const body=JSON.parse(raw || '{}');
  if(req.url==='/api/launch/preflight') {
    launch.preflights.push(body);const items=launchItems(body);const can=items.every(i=>i.status!=='fail');
    res.end(JSON.stringify({items,projection:can?{documents:2,calls:4,cost_usd:0.0123}:null,can_launch:can,target:body.target}));return;}
  if(req.url==='/api/launch/start') {
    launch.starts.push(body);
    if(body.confirm!==String(body.cap_usd)){res.writeHead(409);res.end(JSON.stringify({error:'preflight failed: confirm',items:[{id:'confirm',title:'Typed confirmation matches the cap',status:'fail',detail:'mismatch',fix:'type the cap again'}]}));return;}
    launch.state='running';launch.runningSince=launch.statusReads;res.end(JSON.stringify({started:true,status:{state:'running',target:{id:body.target,cap_usd:body.cap_usd},log_tail:['starting'],result:null,error:null}}));return;}
  if(req.url==='/api/reviews') {
    if(failSave) {res.writeHead(409);res.end(JSON.stringify({error:'synthetic revision conflict'}));return;}
    posts.push(body); saves++;
    packet={...packet,state_revision:'r'+(saves+1),saved_decisions:body.decisions,saved_history:body.history,review_states:[{document_id:record.document_id,field:record.field,status:'human_confirmed'}],tracker:{...packet.tracker,human_confirmed_fields:1,remaining_loaded_fields:1}};
    res.end(JSON.stringify({saved:true}));return;
  }
  if(req.url==='/api/checkpoint') {assert.deepEqual(body,{packet_id:packet.packet_id,confirm:true});checkpoints++;packet={...packet,state_revision:'checkpoint-1',tracker:{...packet.tracker,checkpointed_fields:1}};res.end(JSON.stringify({checkpointed:true}));return;}
  res.writeHead(404);res.end('{}');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const chrome=spawn(process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',['--headless=new','--remote-debugging-port=0','--user-data-dir='+path.join(out,'chrome-profile'),'--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--window-size=1440,1100','about:blank'],{stdio:['ignore','ignore','pipe']});
let ws,session,sequence=0;const pending=new Map(),errors=[];
try {
  const address=await new Promise((resolve,reject)=>{let output='';chrome.stderr.on('data',d=>{output+=d;const m=output.match(/DevTools listening on (ws:\/\/[^\s]+)/);if(m)resolve(m[1]);});chrome.on('error',reject);chrome.on('exit',code=>reject(Error('Chrome exit '+code)));});
  ws=new WebSocket(address);await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));
  ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id){const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}else if(m.method==='Runtime.exceptionThrown') errors.push(m.params);else if(m.method==='Page.javascriptDialogOpening'){try{call('Page.handleJavaScriptDialog',{accept:true})}catch(exc){}}});
  const evaluateAndRetry=async(method,params)=>{for(let attempt=0;;attempt++){try{return await call(method,params)}catch(exc){if(attempt>1||!/navigated or closed/i.test(exc.message))throw exc}}};
  function call(method,params={},sid=session){const id=++sequence;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params,...(sid?{sessionId:sid}:{})}));});}
  async function evaluate(expression){
    for (let attempt=0; attempt<40; attempt++){
      try { const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
        if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
        return r.result.value; }
      catch (exc) { if (!/navigated or closed/i.test(String(exc))) throw exc;
        await new Promise(resolve=>setTimeout(resolve,50)); } }
    throw Error('target context unavailable');
  }
  async function waitFor(expression){await evaluate(`new Promise((resolve,reject)=>{const end=Date.now()+5000;function check(){if(${expression})resolve(true);else if(Date.now()>end)reject(Error('Condition timeout'));else requestAnimationFrame(check);}check();})`);}
  async function screenshot(name){const r=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(out,name+'.png'),Buffer.from(r.data,'base64'));}
  async function input(id,value){await evaluate(`$('${id}').value=${JSON.stringify(value)};$('${id}').dispatchEvent(new Event('input',{bubbles:true}));`);}
  const target=await call('Target.createTarget',{url:'about:blank'},null);
  session=(await call('Target.attachToTarget',{targetId:target.targetId,flatten:true},null)).sessionId;
  await call('Page.enable');await call('Runtime.enable');
  await call('Browser.setDownloadBehavior',{behavior:'allow',downloadPath:out},null);
  await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/review.html`});
  await waitFor(`document.getElementById('save') && !$('save').disabled`);
  assert.equal(await evaluate(`$('source').textContent`),text);
  assert.equal(await evaluate(`document.querySelector('#source mark').textContent`),quote);
  assert.equal(await evaluate(`document.querySelectorAll('#source img, #links a').length`),0);
  assert.match(await evaluate(`$('tasks').textContent`),/pending/);
  assert.match(await evaluate(`$('revision-queue').textContent`),/SYNTH-PRODUCTION.*public-sha.*test.*human_confirmed/s);
  assert.match(await evaluate(`$('tracker-source').textContent`),/private-sha.*connected.*synthetic\/campaign.yaml/s);
  await evaluate(`$('task-filter').value='completed';$('task-filter').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`$('tasks').textContent`),/approved/);
  assert.doesNotMatch(await evaluate(`$('tasks').textContent`),/pending/);
  await screenshot('01-source-tracker-highlight');
  await input('decision','ACCEPT');await input('initials','AB');await input('notes','Synthetic source verified.');
  assert.equal(await evaluate(`$('checkpoint').disabled`),true);
  await evaluate(`$('save').click()`);await waitFor(`!busy && !dirty`);
  assert.equal(saves,1);assert.equal(posts[0].state_revision,'r1');assert.equal(posts[0].decisions[0].decision,'ACCEPT');
  // The real server rejects a decision without a timestamp (review_page.validate_return).
  assert.ok(!Number.isNaN(Date.parse(posts[0].decisions[0].updated_at)),'edited decision must carry updated_at');assert.equal(posts[0].history.length,3);
  await screenshot('02-saved-server-confirmed');
  await call('Page.reload');await waitFor(`document.getElementById('save') && !$('save').disabled`);
  assert.equal(await evaluate(`$('decision').value`),'ACCEPT');
  assert.equal(await evaluate(`history.length`),3,'recovered history must not be fabricated');
  await evaluate(`$('export').click()`);
  await screenshot('03-reloaded-receipt');
  await evaluate(`window.confirm=()=>true;$('checkpoint').click()`);await waitFor(`!busy && packet.state_revision==='checkpoint-1'`);
  assert.equal(checkpoints,1);
  await input('notes','Keep this local draft.');
  await evaluate(`$('reload').click()`);await waitFor(`!busy`);
  assert.equal(await evaluate(`$('notes').value`),'Keep this local draft.');
  // A real reload exercises localStorage restoration rather than only an in-memory refresh.
  await evaluate(`window.onbeforeunload=null`);
  await evaluateAndRetry('Page.navigate',{url:'about:blank'});
  // CDP bypasses the synthetic tab's beforeunload prompt without altering the production page.
  await call('Page.navigate',{url:`http://127.0.0.1:${server.address().port}/review.html`});
  await waitFor(`document.getElementById('save') && !$('save').disabled`);
  assert.equal(await evaluate(`$('notes').value`),'Keep this local draft.');
  failSave=true;
  await evaluate(`$('save').click()`);await waitFor(`!busy`);
  assert.match(await evaluate(`$('message').textContent`),/409.*Draft retained/s);
  assert.equal(await evaluate(`$('notes').value`),'Keep this local draft.');
  packet={...packet,state_revision:'other-reviewer'};
  await evaluate(`$('reload').click()`);await waitFor(`!busy`);
  assert.equal(await evaluate(`$('discard').hidden`),false);assert.equal(await evaluate(`$('save').disabled`),true);
  await screenshot('04-stale-draft-preserved');
  await evaluate(`window.confirm=()=>true;$('discard').click()`);await waitFor(`!busy && !pendingData`);
  assert.equal(await evaluate(`$('notes').value`),'Synthetic source verified.');
  await evaluate(`$('target').value='1';$('target').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`$('values').textContent`),/Example Corporation/);
  assert.equal(await evaluate(`document.querySelector('#links a').protocol`),'https:');
  await screenshot('05-native-field');
  // Model answers: one row per model with value, status and evidence quote.
  assert.match(await evaluate(`$('model-answers').textContent`),/synthetic\/model-a.*Example Corporation.*Present.*Example Corporation/s);
  assert.match(await evaluate(`$('model-answers').textContent`),/synthetic\/model-b.*No value.*Not found.*No evidence quote/s);
  assert.equal(await evaluate(`document.querySelectorAll('#model-answers tr').length`),3);
  await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
  assert.equal(await evaluate(`!!$('model-answers')`),false,'no model_outputs, no table');
  // Per-label items: keep/drop single labels; the kept items build the CORRECT replacement.
  assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`),/2 of 2 kept/);
  await evaluate(`[...document.querySelectorAll('#item-picker .chips .chip')][0].click()`);
  assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`),/1 of 2 kept/);
  // Manual keep/drop survives navigation away and back (the saved-draft adoption must not
  // re-stomp picker state on re-render).
  await evaluate(`$('target').value='1';$('target').dispatchEvent(new Event('change'));`);
  await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`),/1 of 2 kept/,
    'manual keep/drop survives target navigation');
  await evaluate(`[...document.querySelectorAll('#item-picker input')][1].value='signed claim form (edited)';[...document.querySelectorAll('#item-picker input')][1].dispatchEvent(new Event('input',{bubbles:true}));`);
  await evaluate(`[...document.querySelectorAll('#item-picker button')].at(-1).click()`);
  assert.equal(await evaluate(`$('decision').value`),'CORRECT');
  assert.deepEqual(JSON.parse(await evaluate(`$('correction').value`)),['signed claim form (edited)']);
  assert.match(await evaluate(`$('message').textContent`),/kept items/);
  check('per-label item picker and kept-array correction');
  await screenshot('06-item-picker-perlabel');
  // Next pending jumps the only undecided target (synthetic record 1).
  await evaluate(`$('next-pending').click()`);
  assert.equal(await evaluate(`$('target').value`),'1');
  check('next-pending navigation');
  // Sticky reviewer initials: typing them once pre-fills the next target's decision.
  assert.equal(await evaluate(`$('target').value`),'1');
  await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
  assert.equal(await evaluate(`$('initials').value`),'AB','remembered initials pre-fill the next target');
  await evaluate(`$('target').value='1';$('target').dispatchEvent(new Event('change'));`);
  check('sticky reviewer initials across targets');
  // Bulk accept: one click marks the verified undecided targets of the batch with the
  // reviewer's own initials — the agent accelerates, the human still owns the decision.
  await evaluate(`$('target').value='1';$('target').dispatchEvent(new Event('change'));`);
  await evaluate(`$('bulk-accept').click()`);
  assert.match(await evaluate(`$('message').textContent`),/1 verified targets?/);
  assert.equal(await evaluate(`$('decision').value`),'ACCEPT');
  assert.equal(await evaluate(`decisions.get('SYNTH-002\\nentity_name').decision`),'ACCEPT',
    'bulk accept marked the verified undecided target of the batch the review cursor sits in');
  check('bulk accept verified batch with sticky initials');
  // Batched generation alignment: the review list is grouped per labeling batch and the
  // record header shows the batch's task/set/slice line.
  const batches=await evaluate(`(()=>{const t=$('target');return {optgroups:[...t.children].filter(c=>c.tagName==='OPTGROUP').map(g=>g.label), seen:[...t.querySelectorAll('option')].map(o=>o.value)};})()`);
  assert.equal(batches.optgroups[0],'8a · Claim checklist / pilot — 1/1 decided');
  assert.equal(batches.optgroups[1],'9a · Corporate / pilot — 1/1 decided');
  assert.deepEqual(batches.seen,['0','1']);
  await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`$('values').textContent`),/batch 8a .* pilot · Claim checklist/);
  check('batch-grouped target list and batch chip');
  // Tabs: the launcher, the review interface and the campaign tracker are separate
  // views — one visible at a time — so the page stays navigable.
  await evaluate(`showTab('tracker')`);
  assert.equal(await evaluate(`$('tracker-view').hidden`),false);
  assert.equal(await evaluate(`$('review-view').hidden`),true);
  assert.equal(await evaluate(`$('launch-view').hidden`),true);
  await evaluate(`showTab('review')`);
  assert.equal(await evaluate(`$('review-view').hidden`),false);
  await evaluate(`showTab('launch')`);
  check('launcher / review / tracker tab switching');
  // Keyboard: ARIA tabs pattern — arrows cycle selection (focus follows it),
  // Home/End jump, and only the active tab stays in the tab order.
  assert.equal(await evaluate(`$('tab-review').tabIndex`),-1);
  assert.equal(await evaluate(`$('tab-launch').tabIndex`),0);
  await evaluate(`$('tab-launch').focus()`);
  await evaluate(`$('tab-launch').dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true})))`);
  assert.equal(await evaluate(`$('tab-review').getAttribute('aria-selected')`),'true','ArrowRight moves to the next tab');
  assert.equal(await evaluate(`document.activeElement.id`),'tab-review','focus follows the selection');
  await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true})))`);
  assert.equal(await evaluate(`$('tab-launch').getAttribute('aria-selected')`),'true','Home jumps to the first tab');
  await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true,cancelable:true})))`);
  assert.equal(await evaluate(`$('tab-tracker').getAttribute('aria-selected')`),'true','selection wraps backwards');
  check('tab keyboard: arrow/home/end with roving tabindex');
  // A11y invariants that hold in every run (the axe-core pass when AXE_PATH is set
  // scans the whole document; these are the landmarks/roles it validates).
  const a11y=await evaluate(`(()=>{const tabs=[...document.querySelectorAll('#tabs [role="tab"]')];
    return {mains:document.querySelectorAll('main').length,
            header:!!document.querySelector('header h1'),
            panels:document.querySelectorAll('[role="tabpanel"]').length,
            labelled:tabs.every((t)=>t.getAttribute('aria-controls') && document.getElementById(t.getAttribute('aria-controls'))),
            selected:tabs.filter((t)=>t.getAttribute('aria-selected')==='true').length};})()`);
  assert.equal(a11y.mains,1);assert.ok(a11y.header);
  assert.equal(a11y.panels,3);assert.ok(a11y.labelled);assert.equal(a11y.selected,1);
  check('single main landmark, headered page, labelled tab panels');
  // Batch-scoped next pending: with both targets undecided, Shift+N stays inside the
  // cursor's batch (record 0 is batch 8a's only member) while N searches globally.
  const saved0=await evaluate(`decisions.get('SYNTH-001\\nclaim_checklist')`), saved1=await evaluate(`decisions.get('SYNTH-002\\nentity_name')`);
  await evaluate(`decisions.set('SYNTH-001\\nclaim_checklist',{decision:'NOT_REVIEWED'});decisions.set('SYNTH-002\\nentity_name',{decision:'NOT_REVIEWED'});`);
  await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
  await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'N',shiftKey:true,bubbles:true,cancelable:true})))`);
  assert.equal(await evaluate(`$('target').value`),'0','Shift+N stays within the cursor batch');
  await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'N',bubbles:true,cancelable:true})))`);
  assert.equal(await evaluate(`$('target').value`),'1','plain N searches the whole list');
  await evaluate(`decisions.set('SYNTH-001\\nclaim_checklist',${JSON.stringify(saved0)});decisions.set('SYNTH-002\\nentity_name',${JSON.stringify(saved1)});`);
  assert.match(await evaluate(`$('kbd-legend').textContent`),/Shift\+N next pending in batch/);
  check('batch-scoped next pending and keyboard legend');
  // Launch panel: shown because the server says launch is enabled.
  await waitFor(`!$('launch-panel').hidden && $('launch-target').options.length===3`);
  assert.deepEqual(await evaluate(`[...document.querySelectorAll('#launch-target optgroup')].map(g=>g.label)`),['Campaign','Public trial']);
  assert.equal(await evaluate(`$('launch-key').type`),'password');assert.equal(await evaluate(`$('launch-key').autocomplete`),'off');
  await evaluate(`$('launch-target').value='campaign:1.1';$('launch-target').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`$('launch-info').textContent`),/Qualification \u2014 re-labels gold documents to score models/);
  assert.equal(await evaluate(`$('launch-cap').value`),'0.30');
  await evaluate(`$('launch-target').value='trial:correspondence';$('launch-target').dispatchEvent(new Event('change'));`);
  assert.match(await evaluate(`$('launch-info').textContent`),/Labeling \u2014 documents that need labels/);
  assert.equal(await evaluate(`$('launch-trial-opts').hidden`),false);
  const mixedInfo=await evaluate(`$('launch-info').textContent`);
  assert.match(mixedInfo,/model-a off, model-b low/);assert.match(mixedInfo,/DeepSeek runs at reasoning off/);assert.doesNotMatch(mixedInfo,/\[object Object\]/);
  assert.equal(await evaluate(`$('launch-start').disabled`),true);
  // A failing preflight is loud, and the key is sent once, then wiped and never stored.
  await input('launch-initials','x');await input('launch-key',SECRET);
  await evaluate(`$('launch-preflight').click()`);await waitFor(`document.querySelector('#launch-checklist .check.fail')`);
  assert.equal(launch.preflights[0].api_key,SECRET);assert.equal(launch.preflights[0].target,'trial:correspondence');
  assert.equal(launch.preflights[0].documents,2);
  assert.equal(await evaluate(`$('launch-key').value`),'','key field is cleared after the call');
  assert.match(await evaluate(`$('launch-checklist').textContent`),/FAIL.*Operator initials.*bad initials.*Fix: enter 2-6 capital letters/s);
  assert.match(await evaluate(`$('launch-checklist').textContent`),/WARN.*Route not pinged/s);assert.match(await evaluate(`$('launch-checklist').textContent`),/SKIP.*HF checkpoint/s);
  assert.equal(await evaluate(`$('launch-start').disabled`),true);
  assert.equal(await evaluate(`JSON.stringify([localStorage,sessionStorage]).includes('BROWSER-SECRET')`),false,'key never reaches web storage');
  assert.equal(await evaluate(`document.documentElement.outerHTML.includes('BROWSER-SECRET')`),false);
  await screenshot('06-launch-preflight-fail');
  // Fix the initials: all checks pass, Launch opens, and changing an input closes it again.
  await input('launch-initials','AB');
  await evaluate(`$('launch-preflight').click()`);await waitFor(`$('launch-projection').textContent.includes('about $0.0123')`);
  assert.equal(launch.preflights[1].api_key,undefined);
  assert.equal(await evaluate(`$('launch-start').disabled`),false);
  await input('launch-cap','0.20');assert.equal(await evaluate(`$('launch-start').disabled`),true);
  await input('launch-cap','0.25');await evaluate(`$('launch-preflight').click()`);await waitFor(`!$('launch-start').disabled`);
  await screenshot('07-launch-preflight-pass');
  // Launch asks for the cap again; a wrong answer is refused by the server and shown loudly.
  await evaluate(`window.prompt=()=>'0.20';$('launch-start').click()`);await waitFor(`$('launch-message').textContent.includes('Not started (409)')`);
  assert.match(await evaluate(`$('launch-checklist').textContent`),/Typed confirmation matches the cap.*type the cap again/s);
  await evaluate(`$('launch-preflight').click()`);await waitFor(`!$('launch-start').disabled`);
  const readsBefore=launch.reloads;
  await evaluate(`window.prompt=()=>'0.25';$('launch-start').click()`);await waitFor(`$('launch-status').textContent.startsWith('running')`);
  assert.equal(launch.starts.at(-1).confirm,'0.25');assert.equal(launch.starts.at(-1).api_key,undefined);
  await waitFor(`$('launch-status').textContent.startsWith('succeeded')`);
  assert.match(await evaluate(`$('launch-status').textContent`),/spent \$0\.0012.*7a\/7a-review-data\.json/);
  assert.match(await evaluate(`$('launch-log').textContent`),/1 call done/);
  await waitFor(`!busy`);
  assert.ok(launch.reloads>readsBefore,'a finished run reloads the batches');
  assert.equal(await evaluate(`JSON.stringify([localStorage,sessionStorage]).includes('BROWSER-SECRET')`),false);
  await screenshot('08-launch-finished');
  // A11y pass: when AXE_PATH is set, the served /axe.js (vendored axe-core) scans the live
  // document; the committed run stays offline and only the a11y invariants below execute.
  if (process.env.AXE_PATH){
    const ax=await evaluate(`new Promise((resolve)=>{
      const s=document.createElement('script'); s.src='/axe.js';
      s.onload=()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','best-practice']}})
        .then((r)=>resolve(JSON.stringify({violations:r.violations.map((v)=>v.id+' x'+v.nodes.length+(v.nodes[0]&&v.nodes[0].target?' @'+v.nodes[0].target.join(' '):'')),passes:r.passes.length,incomplete:r.incomplete.length})))
        .catch((e)=>resolve(JSON.stringify({violations:['axe-error: '+e.message]})));
      s.onerror=()=>resolve(JSON.stringify({violations:['axe-load-error']}));
      document.head.append(s);
    })`);
    fs.writeFileSync(path.join(out,'axe.json'),ax);
    console.log('AXE '+ax);
    assert.deepEqual(JSON.parse(ax).violations,[]);
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'submitted-review.json'),JSON.stringify(posts[0],null,2));
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({passed:true,saves,checkpoints,journal_events:posts[0].history.length,checks,console_exceptions:errors},null,2));
  console.log('PASS: integrated synthetic review browser smoke; evidence '+out);
} finally {ws?.close();chrome.kill();server.close();}
