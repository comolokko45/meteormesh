const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};const element=()=>({hidden:true,innerHTML:'',textContent:'',className:'',style:{},focus(){},classList:{contains(){return false},toggle(){}},setAttribute(){},querySelectorAll(){return[]}});
const context={console,Intl,Date,Number,JSON,Math,Set,Promise,URL,AbortController,crypto:{randomUUID:()=> 'a'.repeat(36)},setTimeout:()=>1,clearTimeout(){},setInterval(){},fetch:()=>new Promise(()=>{}),location:{hash:''},document:{hidden:false,body:{style:{},classList:{contains:()=>false,toggle(){}}},documentElement:{dataset:{}},activeElement:null,getElementById:id=>elements[id]||(elements[id]=element()),querySelectorAll:()=>[],addEventListener(){}},window:{addEventListener(){},scrollTo(){}},localStorage:{getItem:()=>null,setItem(){}},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},matchMedia:()=>({matches:false}),navigator:{}};
vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/mobile.js','utf8'),context);
function run(s){return vm.runInContext(s,context)}
assert.strictEqual(run('fmt(null)'),'—');assert.strictEqual(run('esc("<img onerror=x>")'),'&lt;img onerror=x&gt;');
run('state={phones:[],night:{capture_analysis:{queue:{}}},segments:{devices:{}}};offline=false');assert.strictEqual(run('totals().total'),null);
run('state={phones:[null,{code:"ruby",camera:"recording",last_mp4:Date.now()-1000000}],night:{},segments:{devices:{"cam-ruby":null}}}');assert.strictEqual(run('phones().length'),1);assert.strictEqual(run('segments().length'),0);assert.match(run('cameraBrief(phones()[0])'),/gecikmiş/);
run('state={phones:[{code:"ruby",free_gib:null,reserve_gib:32}],segments:{devices:{"cam-ruby":{segments:[{segment_id:"ruby_test",size_bytes:1000000,duration_seconds:300}]}}}}');assert.strictEqual(run('forecasts().phone.ruby'),undefined);
run('state={source_state:"READ_OK",generated_utc:new Date().toISOString(),freshness:{state:"CURRENT"}};offline=true;csrf="x";auth={}');assert.strictEqual(run('usable()'),false);
run('offline=false');assert.strictEqual(run('usable()'),true);
run('state={night:{capture_night_id:"20261106-night",detector:{detector_result_items_unreviewed:0,verified_result_files:0,analysis_complete:false}}}');assert.strictEqual(run('candidateCount()'),'—');assert.strictEqual(run('nightLabel()'),'6 Kasım gecesi');
run('state.night.detector.verified_result_files=1');assert.strictEqual(run('candidateCount()'),'0');
console.log('PASS missing data, escaping, stale recording, capacity unknown, offline command gate');
