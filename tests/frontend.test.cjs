const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');

class Element{
  constructor(){this.value='';this.textContent='';this.disabled=false;this.children=[];this.listeners={};this.classes=new Set();this.isConnected=true;this.childNodes={};this.checked=false;this.files=[];this.classList={add:x=>this.classes.add(x),remove:x=>this.classes.delete(x),contains:x=>this.classes.has(x),toggle:(x,on)=>{if(on)this.classes.add(x);else this.classes.delete(x)}}}
  set innerHTML(value){this.html=value;this.children=[];this.childNodes={}}
  get innerHTML(){return this.html||''}
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=children;this.value=''}
  querySelector(selector){return this.childNodes[selector]||(this.childNodes[selector]=new Element())}
  querySelectorAll(){return this.focusable||[]}
  addEventListener(event,fn){this.listeners[event]=fn}
  setAttribute(name,value){this[name]=value}
  removeAttribute(){}
  getClientRects(){return [1]}
  focus(){if(this.document)this.document.activeElement=this;this.focused=true}
  contains(el){return el===this||this.focusable?.includes(el)}
}

function harness(){
  const elements=new Map(),document={hidden:false,activeElement:null,addEventListener(){},querySelectorAll:()=>[],createElement:()=>new Element(),getElementById:id=>{if(!elements.has(id)){const el=new Element();el.document=document;elements.set(id,el)}return elements.get(id)}};
  const context=vm.createContext({console,document,crypto:webcrypto,Uint8Array,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},setTimeout:()=>0,clearTimeout(){},confirm:()=>true,window:{open(){}},FormData:class{constructor(){this.entries=[]}append(...args){this.entries.push(args)}}});
  for(const filename of ['aplus.js','usage.js','app.js']){
    const source=fs.readFileSync(path.join(__dirname,'../static',filename),'utf8').replace(/\binit\(\);\s*$/,'');
    vm.runInContext(source,context);
  }
  const run=code=>vm.runInContext(code,context);
  run("toast=()=>{}; loadJobs=async()=>{};loadStats=async()=>{};setView=()=>{};config.max_images=250; $('scenes').value='Scene one';$('model').value='gpt-image-2.5-flare';$('quality').value='medium';$('canvas').value='square';products=[{file:{name:'a.png'},sku:'SKU-A',scenes:''}]");
  return {context,run,get:id=>document.getElementById(id)};
}

function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}

test('scene edits cannot re-enable or duplicate an in-flight generation; request keeps its snapshot',async()=>{
  const h=harness(),pending=deferred(),calls=[];
  h.context.send=async(url,options)=>{calls.push({url,options});return pending.promise};
  h.run('api=send;submission=generate()');
  h.run("products[0].sku='CHANGED';$('scenes').value='Changed scene';recalc();generate()");
  assert.equal(h.get('generate-btn').disabled,true);
  assert.equal(calls.length,1);
  const parts=Object.fromEntries(calls[0].options.body.entries.map(([key,value])=>[key,value]));
  assert.equal(parts.skus,'["SKU-A"]');assert.equal(parts.scenes,'Scene one');
  assert.match(calls[0].options.headers['Idempotency-Key'],/^[\da-f-]{36}$/);
  pending.resolve({images_queued:1,mode:'demo'});await h.run('submission');
  assert.equal(h.get('generate-btn').disabled,false);
});

test('a lost response reuses the submission key; successful or changed submissions get a fresh key',async()=>{
  const h=harness(),keys=[];
  h.context.send=async(url,options)=>{keys.push(options.headers['Idempotency-Key']);if(keys.length===1||keys.length===4)throw new Error('Connection lost');return {images_queued:1,mode:'demo'}};
  h.run('api=send');
  await h.run('generate()');await h.run('generate()');await h.run('generate()');
  assert.equal(keys[0],keys[1]);assert.notEqual(keys[1],keys[2]);
  await h.run('generate()');h.run("$('scenes').value='Different prompt'");await h.run('generate()');
  assert.notEqual(keys[3],keys[4]);
});

test('job-wide limit disables submission and explains how to fix it',async()=>{
  const h=harness();h.run("products=Array.from({length:50},()=>({file:{name:'a.png'},sku:'A',scenes:''}));$('scenes').value=Array(12).fill('Scene').join('\\n');recalc();api=()=>{throw Error('Unexpected request')}");
  assert.equal(h.get('generate-btn').disabled,true);
  assert.match(h.get('generation-hint').textContent,/Maximum 250 images/);
  await h.run('generate()');
});

test('catalog matching preserves manually corrected SKUs, including edits while matching is pending',async()=>{
  const h=harness(),pending=deferred(),requests=[];
  h.run("products[0].sku='MANUAL';products[0].skuEdited=true;products.push({file:{name:'new.png'},sku:'new',scenes:'',skuEdited:false});showProducts()");
  h.context.send=(url,options)=>{requests.push(JSON.parse(options.body));return pending.promise};
  h.run('api=send;matching=matchProductsWithCatalog()');
  assert.deepEqual(requests[0].filenames,['new.png']);
  const row=h.get('product-list').children[1],input=row.querySelector('input');
  input.listeners.input({target:{value:'EDITED-WHILE-WAITING'}});
  pending.resolve({matches:{'a.png':{matched:true,sku:'CAT-A'},'new.png':{matched:true,sku:'CAT-NEW'}}});
  await h.run('matching');
  assert.equal(h.run('products[0].sku'),'MANUAL');assert.equal(h.run('products[1].sku'),'EDITED-WHILE-WAITING');
});

test('brand creation assigns the product selected at submission even after switching products',async()=>{
  const h=harness(),pending=deferred(),calls=[];
  h.run("currentMemorySku='OLD';currentMemory={};brandEditorNew=true;currentBrandBody=()=>({name:'Brand'});loadMemoryHome=async()=>{}");
  h.context.send=(url,options)=>{calls.push({url,options});return url==='/api/memory/brands'?pending.promise:Promise.resolve({ok:true})};
  h.run('api=send;saving=saveBrandProfile()');h.run("currentMemorySku='NEW';currentMemory={}");pending.resolve({id:'brand-id'});
  await h.run('saving');
  assert.equal(calls[1].url,'/api/memory/products/OLD/brand');assert.equal(h.run('currentMemory.brand_id'),undefined);
});

test('stale product-memory responses cannot replace the newer product',async()=>{
  const h=harness(),old=deferred(),recent=deferred();
  h.context.send=url=>url.endsWith('/OLD')?old.promise:recent.promise;
  h.run('api=send;refreshBrands=async()=>{};older=openProductMemory("OLD");newer=openProductMemory("NEW")');
  recent.resolve({sku:'NEW',facts:'Manual',catalog_facts:'Imported',rules:[]});await h.run('newer');
  old.resolve({sku:'OLD',facts:'Stale',rules:[]});await h.run('older');
  assert.equal(h.run('currentMemorySku'),'NEW');assert.equal(h.get('memory-facts').value,'Manual');assert.equal(h.get('catalog-facts').textContent,'Imported');
});

test('revision retries are idempotent and concurrent revision clicks submit once',async()=>{
  const h=harness(),pending=deferred(),keys=[];
  h.run("selected={id:'render-a'};$('revision-prompt').value='Correct the background';$('revision-model').value='gpt-image-2.5-sunburst';$('revision-quality').value='high'");
  h.context.send=(url,options)=>{keys.push(options.headers['Idempotency-Key']);return keys.length===1?pending.promise:Promise.resolve({mode:'demo'})};
  h.run('api=send;revision=revise();revise()');assert.equal(keys.length,1);
  pending.reject(new Error('Connection lost'));await h.run('revision');
  await h.run('revise()');assert.equal(keys[0],keys[1]);
});

test('review dialog traps Tab and Escape restores the invoking control',()=>{
  const h=harness(),first=new Element(),last=new Element(),trigger=new Element();
  h.get('modal').focusable=[first,last];h.get('modal').classList.add('hidden');h.context.trigger=trigger;
  h.run('document.activeElement=trigger;loadRevisionHistory=()=>{};openModal({id:"render-a",sku:"A",kind:"main",url:"/image.jpg",model:"flare",quality:"medium"})');
  assert.equal(h.get('close-modal').focused,true);
  h.context.last=last;h.run('document.activeElement=last');let prevented=false;
  h.context.key={key:'Tab',shiftKey:false,preventDefault:()=>{prevented=true}};h.run('modalKeydown(key)');
  assert.equal(prevented,true);assert.equal(first.focused,true);
  h.context.key={key:'Escape',preventDefault(){}};h.run('modalKeydown(key)');
  assert.equal(h.get('modal').classList.contains('hidden'),true);assert.equal(trigger.focused,true);
});

const profile={id:'profile-1',name:'Saved profile',master_instructions:'Keep the exact product',branding_notes:'Warm background',heading_font:'Cinzel',body_font:'Roboto',primary_color:'#283f44',secondary_color:'#f4e7d9',accent_color:'#9e7655',logo_url:''};
async function aplusHarness(){
  const h=harness();h.context.profileResult={profiles:[profile],can_edit:true,default_master:'Default A+ instructions'};
  h.run("window.StudioFlowAplus.bind();api=async url=>url==='/api/aplus/profiles'?profileResult:{matches:{}};$('aplus-model').value='gpt-image-2.5-sunburst';$('aplus-quality').value='high';$('aplus-canvas').value='banner'");
  await h.run('window.StudioFlowAplus.load()');
  await h.run("window.StudioFlowAplus.addFiles([{name:'A.png',size:100,type:'image/png'}])");
  return h;
}

test('A+ generation keeps its snapshot, blocks double clicks and retries with the same key',async()=>{
  const h=await aplusHarness(),pending=deferred(),calls=[];
  h.context.send=(url,options)=>{calls.push({url,options});return calls.length===1?pending.promise:Promise.resolve({images_queued:2})};
  h.run('api=send;submission=window.StudioFlowAplus.generate();window.StudioFlowAplus.generate()');
  h.get('aplus-scenes').value='Changed while waiting';h.run('window.StudioFlowAplus.recalc()');
  assert.equal(h.get('aplus-generate').disabled,true);assert.equal(calls.length,1);
  const sent=Object.fromEntries(calls[0].options.body.entries.map(([key,value])=>[key,value]));
  assert.equal(sent.aplus_profile_id,'profile-1');assert.match(sent.scenes,/Hero banner/);
  h.get('aplus-scenes').value=sent.scenes;
  pending.reject(new Error('Connection lost'));await h.run('submission');
  await h.run('window.StudioFlowAplus.generate()');
  assert.equal(calls[0].options.headers['Idempotency-Key'],calls[1].options.headers['Idempotency-Key']);
  await h.run('window.StudioFlowAplus.generate()');
  assert.notEqual(calls[1].options.headers['Idempotency-Key'],calls[2].options.headers['Idempotency-Key']);
});

test('A+ catalog responses preserve SKU edits made while matching is pending',async()=>{
  const h=await aplusHarness(),pending=deferred();h.context.send=()=>pending.promise;
  h.run("api=send;matching=window.StudioFlowAplus.addFiles([{name:'new.png',type:'image/png',size:100}])");
  const input=h.get('aplus-file-list').children[1].children[1];
  input.listeners.input({target:{value:'MANUAL-SKU'}});
  pending.resolve({matches:{'new.png':{matched:true,sku:'AUTO-SKU'}}});await h.run('matching');
  assert.equal(h.get('aplus-file-list').children[1].children[1].value,'MANUAL-SKU');
});

test('A+ save locks the editor and profile selection until the saved profile is loaded',async()=>{
  const h=await aplusHarness(),pending=deferred();h.get('aplus-name').value='Updated profile';h.get('aplus-name').listeners.input();
  h.context.send=(url,options)=>options?.method==='PUT'?pending.promise:Promise.resolve({profiles:[{...profile,name:'Updated profile'}],can_edit:true,default_master:'Default instructions'});
  h.run('api=send');const saving=h.get('aplus-save').listeners.click();
  for(const id of ['aplus-save','aplus-profile','aplus-new','aplus-name','aplus-upload-logo','aplus-generate'])assert.equal(h.get(id).disabled,true,id);
  pending.resolve({id:'profile-1'});await saving;
  assert.equal(h.get('aplus-name').value,'Updated profile');assert.equal(h.get('aplus-profile').disabled,false);
  assert.equal(h.get('aplus-generate').disabled,false);
});

test('A+ prompt and batch limits reject oversized work before submission',async()=>{
  const h=await aplusHarness();h.get('aplus-extra').value='x'.repeat(2000);h.get('aplus-scenes').value='s'.repeat(1600);
  h.run('window.StudioFlowAplus.recalc()');
  assert.equal(h.get('aplus-generate').disabled,true);assert.match(h.get('aplus-hint').textContent,/3,500/);
});

test('multiline A+ batch instructions cannot silently create extra paid scenes',async()=>{
  const h=await aplusHarness(),calls=[];
  h.get('aplus-extra').value='Keep the exact product\nLeave room for copy';
  h.context.send=(url,options)=>{calls.push(options);return Promise.resolve({images_queued:2})};h.run('api=send');
  await h.run('window.StudioFlowAplus.generate()');
  const sent=Object.fromEntries(calls[0].body.entries.map(([key,value])=>[key,value]));
  assert.equal(sent.scenes.split('\n').length,2);
  assert.match(sent.scenes,/Keep the exact product Leave room for copy/);
});

test('quality choices follow the shared model capabilities in every selector',()=>{
  const h=harness();h.run("config.models=[{id:'gpt-image-2.5-flare',qualities:['low','medium','high','xhigh','max','auto']},{id:'gpt-image-1-mini',qualities:['low','medium','high','auto']}]");
  for(const prefix of ['','aplus-','revision-']){
    h.run(`syncModelChoices('${prefix}model','${prefix}quality','gpt-image-2.5-flare','high')`);
    h.get(prefix+'quality').value='max';h.get(prefix+'model').value='gpt-image-1-mini';h.get(prefix+'model').onchange();
    assert.equal(h.get(prefix+'quality').value,'high');
    assert.deepEqual(h.get(prefix+'quality').children.map(x=>x.value),['low','medium','high','auto']);
  }
});

function usageData(balance=10,canEdit=true){return {tracked_spend_usd:.01,requests:2,unreported:1,pending:0,image_input_tokens:80,text_input_tokens:20,image_output_tokens:200,text_output_tokens:0,notice:'Unknown costs are not free',rate_card_version:'test',tracking_since:null,can_edit_budget:canEdit,by_user:[],by_model:[],balance_estimate:{balance_checkpoint_usd:balance,estimated_remaining_usd:balance,alert_percent:20,incomplete:true,unreported_since_checkpoint:1,low:false}}}

test('usage polling displays unknown costs and preserves unsaved checkpoint edits',async()=>{
  const h=harness();h.context.result=usageData();h.run('window.StudioFlowUsage.bind();api=async()=>result');
  await h.run('window.StudioFlowUsage.load()');
  assert.equal(h.get('usage-unreported').textContent,'1');assert.match(h.get('usage-alert').textContent,/incomplete/);
  h.get('usage-checkpoint').value='42.123456';h.get('usage-checkpoint').listeners.input();
  await h.run('window.StudioFlowUsage.load()');
  assert.equal(h.get('usage-checkpoint').value,'42.123456');
});

test('a delayed usage poll cannot overwrite a newly saved balance checkpoint',async()=>{
  const h=harness(),stale=deferred();h.context.result=usageData();h.run('window.StudioFlowUsage.bind();api=async()=>result');
  await h.run('window.StudioFlowUsage.load()');
  let gets=0;h.context.send=(url,options)=>options?.method==='PUT'?Promise.resolve({ok:true}):++gets===1?stale.promise:Promise.resolve(usageData(42));
  h.run('api=send;polling=window.StudioFlowUsage.load()');
  h.get('usage-checkpoint').value='42';h.get('usage-threshold').value='20';h.get('usage-checkpoint').listeners.input();
  await h.run('window.StudioFlowUsage.save()');
  stale.resolve(usageData(10));await h.run('polling');
  assert.equal(Number(h.get('usage-checkpoint').value),42);assert.match(h.get('usage-balance').textContent,/42/);
});

test('nonowners cannot submit a checkpoint even when the handler is invoked directly',async()=>{
  const h=harness();h.context.result=usageData(10,false);h.run('api=async()=>result');
  await h.run('window.StudioFlowUsage.load()');
  h.run("api=()=>{throw Error('Unexpected write')}");await h.run('window.StudioFlowUsage.save()');
  assert.equal(h.get('usage-save').disabled,true);
});
