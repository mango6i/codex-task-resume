// Real Windows shell + real persistent helper; every desktop tool is a fixture.
// This never reads user state, starts an app-server or sends to a real chat.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
if (process.platform !== 'win32') {
  console.log('Windows process integration requires Windows.');
  process.exit(0);
}
const python = process.argv[2] || 'python';
const shells = process.argv.slice(3);
if (!shells.length) shells.push('powershell.exe');
const source = path.resolve(__dirname, '../skills/quota-resume/scripts');
const code = fs.readFileSync(path.join(source, 'native_dispatch.js'), 'utf8');
const run = new Function('tools', 'config', 'return (async()=>{' + code + '})()');
const envelope = value => ({content: [{type: 'text', text: JSON.stringify(value)}]});
const exact = '继续任务\n保留 ‘弯引号’、"双引号"、\'apostrophe\' 与 🚀。';

function execute(shell, cmd) {
  assert.equal(/[^\x00-\x7f]/.test(cmd), false, 'Shell syntax must contain ASCII only');
  const prelude = '$OutputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding = $OutputEncoding; ';
  const result = spawnSync(shell, ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', prelude + cmd],
    {encoding: 'utf8', windowsHide: true, timeout: 20000});
  if (result.error) throw result.error;
  return {exit_code: result.status, output: (result.stdout || '') + (result.stderr || '')};
}

function removeFixture(root) {
  const resolved = fs.realpathSync(root);
  assert.equal(path.dirname(resolved).toLowerCase(), fs.realpathSync(os.tmpdir()).toLowerCase());
  assert.ok(path.basename(resolved).startsWith('quota-native-fixture-'));
  const check = current => {
    assert.equal(fs.lstatSync(current).isSymbolicLink(), false, 'Linked fixture refused');
    if (fs.statSync(current).isDirectory()) for (const name of fs.readdirSync(current)) check(path.join(current, name));
  };
  check(resolved);
  fs.rmSync(resolved, {recursive: true, force: true});
}

async function scenario(shell, mode) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'quota-native-fixture-'));
  try {
    const scripts = path.join(root, "插件’s 'scripts'");
    const saved = path.join(root, "数据’s 'state'.json");
    fs.mkdirSync(scripts);
    for (const name of ['state.py', 'recovery_schedule.py', 'recovery_fast.py', 'native_dispatch.js'])
      fs.copyFileSync(path.join(source, name), path.join(scripts, name));
    const now = Math.floor(Date.now() / 1000), reset = now - 180;
    const state = {version: 1, enabled: true, accountId: 'fixture-account', controllerId: 'fixture-controller',
      settings: {threshold: 10, delaySeconds: 60, checkSeconds: 60, expiryHours: 72, limitId: 'codex', message: exact},
      excluded: [], runtime: {}, events: [], automationId: null, queue: {
        captured: {threadId: 'fixture-work', hostId: 'local', title: 'Fixture only', turnId: 'captured-turn',
          turnStartedAt: reset - 300, capturePolicy: 2, status: 'pending', windowKey: 'codex:' + reset,
          resetAt: reset, dueAt: reset + 60, capturedAt: reset - 30, expiresAt: now + 7200}}};
    fs.writeFileSync(saved, JSON.stringify(state), 'utf8');
    if (mode === 'success') {
      const getPrompt = 'import sys,json;sys.path.insert(0,sys.argv[1]);import recovery_schedule as r;print(json.dumps(r.fast_prompt(sys.argv[2])))';
      const raw = spawnSync(python, ['-X', 'utf8', '-c', getPrompt, scripts, saved], {encoding: 'utf8', windowsHide: true});
      assert.equal(raw.status, 0, raw.stderr);
      const prompt = JSON.parse(raw.stdout);
      const bootstrap = JSON.parse(prompt.match(/cmd:("(?:\\.|[^"\\])*"),max_output_tokens/)[1]);
      const boot = execute(shell, bootstrap);
      assert.equal(boot.exit_code, 0, boot.output);
      const actualState = JSON.parse(boot.output).config.state;
      assert.equal(path.basename(actualState), path.basename(saved), 'Unicode filename must survive bootstrap');
      assert.equal(fs.statSync(actualState).ino, fs.statSync(saved).ino,
        'Expanded and short Windows paths must identify the same state file');
    }
    let sends = 0, snapshots = 0, quotaReads = 0;
    const fixtureUsage = () => ({accountId: 'fixture-account', ordinaryUsageAllowed: true,
      rateLimitsByLimitId: {codex: {primary: {usedPercent: 0, windowDurationMins: 300, resetsAt: now + 18000},
        secondary: {usedPercent: 20, windowDurationMins: 10080, resetsAt: now + 604800}}}});
    const tools = {
      exec_command: async ({cmd}) => {
        if (cmd.includes(' read-quota --payload-base64 ')) {
          // Quota endpoints are fixtures too; never start an independent server in tests.
          if (mode !== 'quota_fallback') return {exit_code: 2, output: 'Fixture quota endpoint unavailable'};
          return {exit_code: 0, output: JSON.stringify({usage: fixtureUsage(), observedAt: Date.now()/1000})};
        }
        return execute(shell, cmd);
      },
      clock__curr_time: async () => ({current_time: new Date().toISOString().slice(0, 19).replace('T', ' ') + ' UTC'}),
      mcp__codex_app__get_usage_limits: async () => {
        quotaReads++;
        if (['quota_unavailable', 'quota_fallback'].includes(mode) || (mode === 'quota_retry' && quotaReads === 1))
          return {isError: true, content: [{type: 'text', text: 'Could not read current usage limits. Try again later.'}]};
        return envelope({accountId: 'fixture-account', ordinaryUsageAllowed: true,
          rateLimitsByLimitId: {codex: {primary: {usedPercent: 0, windowDurationMins: 300, resetsAt: now + 18000},
            secondary: {usedPercent: 20, windowDurationMins: 10080, resetsAt: now + 604800}}}});
      },
      mcp__codex_app__wait_threads: async ({targets}) => {
        assert.deepEqual(targets, [{threadId: 'fixture-work', hostId: 'local'}]);
        const started = snapshots++ > 0;
        return envelope({polls: [{thread: {id: 'fixture-work', hostId: 'local', status: {type: started ? 'active' : 'systemError'}},
          latestTurn: {id: started ? 'new-turn' : 'captured-turn', status: started ? 'inProgress' : mode === 'completed' ? 'completed' : 'failed',
            startedAt: reset - 300, completedAt: reset - 1, error: {message: 'You’ve hit your usage limit. ‘中文’ "quote" 🚀'}},
          latestAssistantMessage: {phase: 'commentary', text: 'Fixture context must not be interpreted'}}]});
      },
      mcp__codex_app__send_message_to_thread: async args => {
        sends++;
        assert.equal(args.threadId, 'fixture-work');
        assert.equal(args.prompt, exact);
        assert.equal(args.model, undefined);
        const reservation = JSON.parse(fs.readFileSync(saved, 'utf8')).queue.captured;
        assert.equal(reservation.status, 'reserved', 'Reservation must persist BEFORE the native send');
        assert.equal(reservation.reservedMessage, exact);
        if (mode === 'uncertain') throw Error('Fixture connection lost');
        return envelope({threadId: 'fixture-work'});
      },
      mcp__codex_app__automation_update: async () => { throw Error('A fixture must never touch a real timer'); }
    };
    const report = await run(tools, {python, script: path.join(scripts, 'recovery_fast.py'), state: saved});
    const final = JSON.parse(fs.readFileSync(saved, 'utf8')).queue.captured;
    const success = ['success', 'quota_retry', 'quota_fallback'].includes(mode);
    const noSend = mode === 'completed' || mode === 'quota_unavailable';
    assert.equal(sends, noSend ? 0 : 1, JSON.stringify(report));
    assert.equal(final.status, success ? 'sent' : mode === 'quota_unavailable' ? 'pending' : mode);
    assert.equal(report.sent.length, success ? 1 : 0);
    assert.equal(report.failures.length, ['uncertain', 'quota_unavailable'].includes(mode) ? 1 : 0, JSON.stringify(report));
    if (success) assert.equal(report.sent[0].running, true);
    if (mode.startsWith('quota_')) assert.equal(quotaReads, 2);
    if (mode === 'quota_unavailable') {
      assert.equal(final.token, undefined, 'A failed quota read must leave the queue unreserved');
      assert.match(report.failures[0].reason, /Live quota failed after 2 read attempts:.*Could not read current usage limits/);
    }
  } finally { removeFixture(root); }
}

(async () => {
  for (const shell of shells) {
    for (const mode of ['success', 'completed', 'uncertain', 'quota_retry', 'quota_unavailable', 'quota_fallback']) await scenario(shell, mode);
    console.log(path.basename(shell) + ': real Unicode-path bootstrap, native quota error, exact prompt, completed skip and no uncertain retry passed.');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
