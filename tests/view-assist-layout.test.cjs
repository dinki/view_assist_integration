const {test} = require('node:test');

const assert = require('node:assert/strict');

const fs = require('node:fs');

const vm = require('node:vm');

const source = fs.readFileSync(`${__dirname}/../custom_components/view_assist/js_modules/view_assist.js`, 'utf8');

function fixture(selectTree) {
  const timers = new Map();
 let next = 0;

  const context = vm.createContext({document:{body:{}}, selectTree,
    setTimeout(fn) {const id=++next;
timers.set(id,fn);
return id;
},
    clearTimeout(id) {timers.delete(id);
}});

  const methods=source.slice(source.indexOf('  async hide_header('),source.indexOf('  missingModules()'));

  const instance=vm.runInContext(`new (class {${methods}})()`,context);

  return {instance,timers,context};

}
test('missing optional layout elements do not schedule retries',async()=>{
  const main={style:{setProperty(){},removeProperty(){}}};

  const {instance,timers}=fixture(async(root,path)=>path.includes('home-assistant')?main:null);

  await instance.hide_header(true);
await instance.hide_sidebar(true);

  assert.equal(timers.size,0);

});

test('layout styles hide and restore with valid important priority',async()=>{
  const styles=new Map();

  const element={style:{setProperty(k,v,p){styles.set(k,[v,p]);
},removeProperty(k){styles.delete(k);
}}};

  const {instance,timers}=fixture(async()=>element);

  await instance.hide_header(true);
await instance.hide_sidebar(true);

  assert.equal(styles.get('--ha-sidebar-width')[0],'0px');

  assert.equal(styles.get('--mdc-top-app-bar-width')[1],'important');

  await instance.hide_header(false);
await instance.hide_sidebar(false);

  assert.equal(styles.size,0);
assert.equal(timers.size,0);

});

test('unmounted roots have one bounded retry per method and navigation replaces it',async()=>{
  const {instance,timers}=fixture(async()=>null);

  await instance.hide_sidebar(true);
await instance.hide_sidebar(false);

  assert.equal(timers.size,1);

  for(let i=0;
i<30&&timers.size;
i++){
    const [id,fn]=timers.entries().next().value;
timers.delete(id);
fn();

    await new Promise(setImmediate);

    assert.ok(timers.size<=1);

  }
  assert.equal(timers.size,0);

  await instance.hide_header(true);

  for(let i=0;
i<30&&timers.size;
i++){
    const [id,fn]=timers.entries().next().value;
timers.delete(id);
fn();
await new Promise(setImmediate);

  }
  assert.equal(timers.size,0);

});

test('tree queries clear timeout timers on success, timeout, and unexpected failure',async()=>{
  const {context,timers}=fixture(async()=>null);

  const fn=source.slice(source.indexOf('export async function selectTree('),source.indexOf('\nexport async function hass_base_el')).replace('export ','');

  context.TIMEOUT_ERROR='SELECTTREE-TIMEOUT';
context._selectTree=async()=>42;

  vm.runInContext(fn,context);

  assert.equal(await context.selectTree({},'x'),42);
assert.equal(timers.size,0);

  context._selectTree=()=>new Promise(()=>{});

  const pending=context.selectTree({},'x');
[...timers.values()][0]();

  assert.equal(await pending,null);
assert.equal(timers.size,0);

  context._selectTree=async()=>{throw new Error('unexpected');
};

  await assert.rejects(context.selectTree({},'x'),/unexpected/);
assert.equal(timers.size,0);

});

