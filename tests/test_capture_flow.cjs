const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Exercise the real capture/event functions with a fake camera, HTTP and clock.
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8')
  .split('// ---------------- wiring ----------------')[0];
function harness() {
  const nodes = new Map(), timers = new Map(), requests = [];
  let sequence = 0;
  const context = vm.createContext({
    document: {getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {value: id === 'interval' ? '1' : '',
        classList: {toggle() {}}, addEventListener() {}});
      return nodes.get(id);
    }},
    localStorage: {getItem() {return null;}},
    crypto: {randomUUID() {return `frame-${++sequence}`;}},
    setTimeout(fn) {const id = ++sequence; timers.set(id, fn); return id;},
    clearTimeout(id) {timers.delete(id);},
    request: async (url, body) => {requests.push({url, body}); return {id: `job-${++sequence}`};},
  });
  const run = code => vm.runInContext(code, context);
  run(source);
  run(`stream={};ready=true;connected=true;api=request;
    captureFrame=async()=>({image:'photo',captured_at:Date.now()});
    renderLaneStatus=()=>{};renderTrace=()=>{};`);
  const complete = (status='done') => run(`receive({id:[...jobs.keys()].at(-1),session,
    version:1,status:'${status}',lanes:{full:'${status}'},error:'test failure'});`);
  return {run, nodes, timers, requests, complete};
}

test('stream waits for completion, including delayed SSE, then schedules one frame', async () => {
  const h = harness();
  await h.run('auto=true;analyze()');
  assert.equal(h.requests.length, 1);
  assert.equal(h.run('busy'), true);
  assert.equal(h.timers.size, 0);
  await h.run('analyze()');
  assert.equal(h.requests.length, 1);
  h.complete();
  assert.equal(h.timers.size, 1);
  h.run('scheduleAuto()');
  assert.equal(h.timers.size, 1);
  const [id, tick] = [...h.timers.entries()][0];
  h.timers.delete(id);
  tick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests.length, 2);
  assert.equal(h.timers.size, 0);
});

test('manual capture remains enabled while busy and switches off streaming', async () => {
  const h = harness();
  await h.run('auto=true;analyze()');
  h.run('controls()');
  assert.equal(h.nodes.get('capture').disabled, false);
  await h.run('captureOnce()');
  assert.equal(h.run('auto'), false);
  assert.equal(h.requests.filter(r => r.url === '/api/analyze').length, 2);
  assert.equal(h.requests.filter(r => r.url === '/api/cancel').length, 0);
  h.complete();
  assert.equal(h.timers.size, 0);
});

test('switching during the sampling pause removes the pending timer', async () => {
  const h = harness();
  await h.run('auto=true;analyze()');
  h.complete();
  assert.equal(h.timers.size, 1);
  await h.run('captureOnce()');
  assert.equal(h.timers.size, 0);
  assert.equal(h.requests.length, 2);
});

test('a terminal SSE event before HTTP response does not create phantom busy state', async () => {
  const h = harness();
  h.run(`api=async()=>{
    receive({id:'early',session,version:1,status:'done',lanes:{full:'done'}});
    return {id:'early'};
  };`);
  await h.run('auto=true;analyze()');
  assert.equal(h.run('busy'), false);
  assert.equal(h.timers.size, 1);
});

test('analysis errors stop automatic sampling', async () => {
  const h = harness();
  await h.run('auto=true;analyze()');
  h.complete('error');
  assert.equal(h.run('auto'), false);
  assert.equal(h.timers.size, 0);
});

test('selected history is attached to a new photo request', async () => {
  const h = harness();
  await h.run("historyReference='old-record-123';captureOnce()");
  assert.equal(h.requests[0].body.history_record_id, 'old-record-123');
});
