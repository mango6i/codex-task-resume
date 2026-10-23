// Executed only by the native functions tool. Never starts an independent server.
const decode = r => {
  if (!r || r.isError) throw Error('Native tool failed');
  if (r.structuredContent) return r.structuredContent;
  if (Array.isArray(r.content)) {
    for (const c of r.content) if (c.type === 'text') {
      try { return JSON.parse(c.text); } catch (_) {}
    }
    throw Error('Native JSON result unavailable');
  }
  return r;
};
const quote = s => "'" + String(s).replace(/'/g, "''") + "'";
const local = async (action, payload = {}) => {
  const cmd = '& ' + quote(config.python) + ' -X utf8 ' + quote(config.script)
    + ' --state ' + quote(config.state) + ' ' + action + ' --json ' + quote(JSON.stringify(payload));
  const r = await tools.exec_command({cmd, max_output_tokens: 2400});
  if (r.exit_code !== 0) throw Error(r.output || 'Local recovery check failed');
  return JSON.parse(r.output);
};
const report = {sent: [], skipped: [], failures: []};
const plan = await local('plan');
for (const target of plan.targets) {
  if (target.dueAt > Date.now()/1000) continue;
  let reservation, capturedTurnId;
  try {
    // Only this recorded target. No inventory scan, history read or task-text analysis.
    const [rawUsage, rawStatus, clock] = await Promise.all([
      tools.mcp__codex_app__get_usage_limits({}),
      tools.mcp__codex_app__wait_threads({targets: [{threadId: target.threadId, hostId: 'local'}], timeoutMs: 0}),
      tools.clock__curr_time({})
    ]);
    const usage = decode(rawUsage), snapshot = decode(rawStatus);
    const poll = snapshot.polls?.find(p => p.thread?.id === target.threadId);
    if (!poll) throw Error('Recorded native target unavailable');
    capturedTurnId = poll.latestTurn?.id;
    // Never forward conversation text into the helper or model output.
    const flags = {polls: [{thread: poll.thread, latestTurn: poll.latestTurn,
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
