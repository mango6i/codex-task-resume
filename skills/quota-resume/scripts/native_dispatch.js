// Executed only by the native functions tool. Never starts an independent server.
const decode = (r, operation = 'Native tool') => {
  if (!r || r.isError) {
    const detail = r?.content?.filter(c => c.type === 'text').map(c => c.text).join(' ');
    throw Error(operation + ': ' + (detail || 'no result').replace(/\s+/g, ' ').slice(0, 600));
  }
  if (r.structuredContent) return r.structuredContent;
  if (Array.isArray(r.content)) {
    for (const c of r.content) if (c.type === 'text') {
      try { return JSON.parse(c.text); } catch (_) {}
    }
    throw Error(operation + ': JSON result unavailable');
  }
  return r;
};
// Only read operations may retry, once in this pass. Sends and reservations
// never go through this helper; uncertainty still prevents replay.
const readNative = async (operation, read) => {
  for (let attempt = 0; attempt < 2; attempt++) {
    try { return decode(await read(), operation); }
    catch (error) {
      if (attempt === 1) throw Error(operation + ' failed after 2 read attempts: ' + String(error));
    }
  }
};
// PowerShell treats smart apostrophes as quote delimiters too. Only ASCII
// JSON/base64 enters command syntax; Unicode is reconstructed after parsing.
const asciiJson = value => JSON.stringify(value).replace(/[\u007f-\uffff]/g,
  ch => '\\u' + ch.charCodeAt(0).toString(16).padStart(4, '0'));
const quote = s => "'" + String(s).replace(/'/g, "''") + "'";
const pathArg = s => '(ConvertFrom-Json ' + quote(asciiJson(String(s))) + ')';
const encodePayload = value => {
  const data = asciiJson(value), alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let result = '';
  for (let i = 0; i < data.length; i += 3) {
    const a = data.charCodeAt(i), b = data.charCodeAt(i + 1), c = data.charCodeAt(i + 2);
    result += alphabet[a >> 2] + alphabet[((a & 3) << 4) | ((b || 0) >> 4)]
      + (i + 1 < data.length ? alphabet[((b & 15) << 2) | ((c || 0) >> 6)] : '=')
      + (i + 2 < data.length ? alphabet[c & 63] : '=');
  }
  return result;
};
const local = async (action, payload = {}) => {
  const cmd = '& ' + pathArg(config.python) + ' -X utf8 ' + pathArg(config.script)
    + ' --state ' + pathArg(config.state) + ' ' + action + ' --payload-base64 ' + quote(encodePayload(payload));
  const r = await tools.exec_command({cmd, max_output_tokens: 2400});
  if (r.exit_code !== 0) throw Error('Local ' + action + ' failed (exit '
    + String(r.exit_code ?? 'unfinished') + '): ' + (r.output || 'no diagnostic output'));
  return JSON.parse(r.output);
};
const readQuota = async () => {
  try {
    return await readNative('Live quota', () => tools.mcp__codex_app__get_usage_limits({}));
  } catch (nativeError) {
    try {
      const fallback = await local('read-quota');
      if (!fallback.usage || !Number.isFinite(fallback.observedAt)
          || Math.abs(Date.now()/1000-fallback.observedAt) > 90)
        throw Error('Fresh read-only quota unavailable');
      return fallback.usage;
    } catch (error) {
      throw Error(String(nativeError) + '; read-only quota fallback: ' + String(error));
    }
  }
};
const report = {sent: [], skipped: [], failures: []};
const plan = await local('plan');
for (const target of plan.targets) {
  if (target.dueAt > Date.now()/1000) continue;
  let reservation, capturedTurnId;
  try {
    // Only this recorded target. No inventory scan, history read or task-text analysis.
    const [usage, snapshot] = await Promise.all([
      readQuota(),
      readNative('Recorded task status', () => tools.mcp__codex_app__wait_threads({
        targets: [{threadId: target.threadId, hostId: 'local'}], timeoutMs: 0}))
    ]);
    const clock = await tools.clock__curr_time({});
    const poll = snapshot.polls?.find(p => p.thread?.id === target.threadId);
    if (!poll) throw Error('Recorded native target unavailable');
    capturedTurnId = poll.latestTurn?.id;
    // Never forward conversation text into the helper or model output.
    const thread = poll.thread, turn = poll.latestTurn || {};
    const flags = {polls: [{thread: {id: thread.id, hostId: thread.hostId, kind: thread.kind,
      archived: thread.archived, isSubagent: thread.isSubagent, parentThreadId: thread.parentThreadId,
      status: thread.status}, latestTurn: {id: turn.id, status: turn.status, startedAt: turn.startedAt,
      completedAt: turn.completedAt, error: {message: turn.error?.message}},
      assistantPhase: poll.latestAssistantMessage?.phase}]};
    reservation = await local('reserve', {threadId: target.threadId, windowKey: target.windowKey,
      observedAt: Date.parse(clock.current_time.replace(' UTC', 'Z').replace(' ', 'T'))/1000,
      usage, snapshot: flags});
    if (!reservation.accepted) {
      report.skipped.push(target.threadId);
      if (reservation.reason) report.failures.push({threadId: target.threadId, stage: 'check', reason: reservation.reason});
      continue;
    }
  } catch (error) {
    report.failures.push({threadId: target.threadId, stage: 'check', reason: String(error)});
    continue;
  }
  let confirmed = false;
  try {
    const r = await tools.mcp__codex_app__send_message_to_thread({threadId: reservation.threadId,
      hostId: reservation.hostId, prompt: reservation.message});
    const receipt = decode(r);
    confirmed = receipt.threadId === reservation.threadId && receipt.success !== false && !receipt.error;
  } catch (_) { /* Uncertain delivery is never retried. */ }
  try {
    await local('commit', {token: reservation.token, outcome: confirmed ? 'sent' : 'uncertain'});
  } catch (error) {
    report.failures.push({threadId: target.threadId, stage: 'commit', reason: String(error)});
    // A persisted reservation also prevents replay after an unconfirmed commit.
  }
  if (!confirmed) {
    report.failures.push({threadId: target.threadId, stage: 'send', reason: 'uncertain; do not retry'});
    continue;
  }
  let running = false;
  try {
    const result = decode(await tools.mcp__codex_app__wait_threads({targets: [
      {threadId: target.threadId, hostId: 'local'}], timeoutMs: 0}));
    const latest = result.polls?.find(p => p.thread?.id === target.threadId);
    running = latest?.thread?.status?.type === 'active' && latest?.latestTurn?.status === 'inProgress'
      && latest?.latestTurn?.id && latest.latestTurn.id !== capturedTurnId;
  } catch (_) {}
  report.sent.push({threadId: target.threadId, title: target.title, running});
}
try {
  let final = await local('finish');
  if (final.action === 'pause') {
    decode(await tools.mcp__codex_app__automation_update(final.arguments));
    await local('ack-pause');
  } else if (final.action === 'rearm') {
    // At most two timing corrections, never a minute-by-minute model monitor.
    for (let attempt = 0; attempt < 3; attempt++) {
      const request = final.request;
      if (!request) throw Error('Recovery re-arm plan unavailable');
      const result = decode(await tools.mcp__codex_app__automation_update(request.arguments));
      try {
        await local('ack-arm', {id: result.automationId, dueAt: request.dueAt});
        break;
      } catch (error) {
        if (attempt === 2) throw error;
        final = await local('finish');
        if (final.action !== 'rearm') throw error;
      }
    }
  }
} catch (error) {
  report.failures.push({stage: 'schedule', reason: String(error)});
}
return report;
