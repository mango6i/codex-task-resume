const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../skills/quota-resume/scripts/native_dispatch.js'),'utf8');
const run=new Function('tools','config','return (async()=>{'+code+'})()');
const envelope=v=>({content:[{type:'text',text:JSON.stringify(v)}]});
async function scenario(kind){
 const calls=[];let reads=0,quotaReads=0,statusReads=0;
 const tools={
  exec_command:async ({cmd})=>{
   assert.equal(/[^\x00-\x7f]/.test(cmd),false,'Command syntax must contain ASCII only');
   const action=cmd.match(/ (plan|reserve|commit|finish|ack-pause|ack-arm|read-quota) --payload-base64 /)[1];
   const encoded=cmd.match(/ --payload-base64 '([A-Za-z0-9+/=]+)'$/)[1];
   const payload=JSON.parse(Buffer.from(encoded,'base64').toString('utf8'));
   calls.push({action,payload});
   if(action==='reserve'&&kind==='process_failure')return {exit_code:1,output:''};
   let result={};
   if(action==='read-quota'){
    if(!['quota_fallback','stale_fallback'].includes(kind))return {exit_code:2,output:'Read-only quota unavailable'};
    result={usage:{accountId:'saved'},observedAt:Date.now()/1000-(kind==='stale_fallback'?100:0)};
   }
   if(action==='plan')result={targets:[{threadId:'recorded',windowKey:'window',dueAt:1}]};
   if(action==='reserve')result=kind==='rejected'?{accepted:false}:{accepted:true,threadId:'recorded',hostId:'local',message:'继续任务\n原文',token:'reserved'};
   if(action==='finish')result={action:'pause',arguments:{id:'same',status:'PAUSED'}};
   return {exit_code:0,output:JSON.stringify(result)};
  },
  mcp__codex_app__get_usage_limits:async()=>{
   calls.push({action:'quota'});quotaReads++;
   if(['quota_unavailable','quota_fallback','stale_fallback'].includes(kind)||(kind==='quota_retry'&&quotaReads===1))
    return {isError:true,content:[{type:'text',text:'Could not read current usage limits. Try again later.'}]};
   return envelope({accountId:'saved'});
  },
  mcp__codex_app__wait_threads:async ({targets})=>{
   assert.deepEqual(targets,[{threadId:'recorded',hostId:'local'}]);
   statusReads++;
   if(kind==='status_retry'&&statusReads===1)throw Error('Native status connection interrupted');
   reads++;return envelope({polls:[{thread:{id:'recorded',hostId:'local',status:{type:reads===1?'systemError':'active'}},
    latestTurn:{id:reads===1?'captured':'new',status:reads===1?'failed':'inProgress',
      error:{message:'You’ve hit your usage limit. 中文 ‘quoted’ 🚀'},items:['DO NOT forward turn contents']},
    latestAssistantMessage:{phase:'commentary',text:'DO NOT forward or interpret this text'}}]});
  },
  clock__curr_time:async()=>({current_time:'2026-10-23 14:00:00 UTC'}),
  mcp__codex_app__send_message_to_thread:async args=>{
   assert.equal(calls.at(-1).action,'reserve');calls.push({action:'send',args});
   assert.equal(args.prompt,'继续任务\n原文');assert.equal(args.model,undefined);
   if(kind==='uncertain')throw Error('connection lost');
   return envelope({threadId:'recorded'});
  },
  mcp__codex_app__automation_update:async args=>{calls.push({action:'pause'});assert.equal(args.id,'same');return envelope({automationId:'same'});}
 };
 const report=await run(tools,{python:"C:/Python’s 'test'/python.exe",script:'C:/插件/recovery_fast.py',state:'C:/saved/state.json'});
 const noSend=['rejected','process_failure','quota_unavailable','stale_fallback'].includes(kind);
 assert.equal(calls.filter(c=>c.action==='send').length,noSend?0:1);
 if(!noSend)assert.equal(calls.find(c=>c.action==='commit').payload.outcome,kind==='uncertain'?'uncertain':'sent');
 assert.equal(JSON.stringify(calls).includes('DO NOT forward'),false);
 assert.equal(JSON.stringify(calls).includes('turn contents'),false);
 if(!['quota_unavailable','stale_fallback'].includes(kind))assert.equal(calls.find(c=>c.action==='reserve').payload.snapshot.polls[0].latestTurn.error.message,
  'You’ve hit your usage limit. 中文 ‘quoted’ 🚀');
 assert.equal(calls.at(-1).action,'ack-pause');
 const success=['success','quota_retry','status_retry','quota_fallback'].includes(kind);
 assert.equal(report.sent.length,success?1:0);
 if(success)assert.equal(report.sent[0].running,true);
 if(kind==='quota_retry')assert.equal(quotaReads,2);
 if(kind==='status_retry')assert.equal(statusReads,3,'One failed read, one validation read, one running-turn check');
 if(kind==='quota_unavailable'){
  assert.equal(quotaReads,2,'A read failure must never become repeated model polling');
  assert.equal(calls.filter(c=>['reserve','send','commit'].includes(c.action)).length,0);
  assert.match(report.failures[0].reason,/Live quota failed after 2 read attempts:.*Could not read current usage limits/);
 }
 if(['quota_fallback','stale_fallback'].includes(kind)){
  assert.equal(quotaReads,2);assert.equal(calls.filter(c=>c.action==='read-quota').length,1);
 }
 if(kind==='stale_fallback')assert.match(report.failures[0].reason,/Fresh read-only quota unavailable/);
 if(kind==='process_failure')assert.match(report.failures[0].reason,/Local reserve failed \(exit 1\)/);
}
async function twoRecordedTargets(){
 const ids=['recorded-first','recorded-second'], snapshots=new Map(), sends=[];let quotaReads=0;
 const tools={
  exec_command:async({cmd})=>{
   const action=cmd.match(/ (plan|reserve|commit|finish) --payload-base64 /)[1];
   const data=JSON.parse(Buffer.from(cmd.match(/ --payload-base64 '([^']+)'$/)[1],'base64').toString('utf8'));
   let result={};
   if(action==='plan')result={targets:ids.map(threadId=>({threadId,windowKey:'window',dueAt:1}))};
   if(action==='reserve')result={accepted:true,threadId:data.threadId,hostId:'local',message:'继续任务',token:data.threadId};
   if(action==='finish')result={action:'none'};
   return {exit_code:0,output:JSON.stringify(result)};
  },
  mcp__codex_app__get_usage_limits:async()=>{
   quotaReads++;
   if(quotaReads===1)return {isError:true,content:[{type:'text',text:'Could not read current usage limits. Try again later.'}]};
   return envelope({accountId:'saved'});
  },
  mcp__codex_app__wait_threads:async({targets})=>{
   assert.equal(targets.length,1);const id=targets[0].threadId;assert.ok(ids.includes(id));
   const running=snapshots.has(id);snapshots.set(id,true);
   return envelope({polls:[{thread:{id,hostId:'local',status:{type:running?'active':'systemError'}},
    latestTurn:{id:running?'new-'+id:'captured-'+id,status:running?'inProgress':'failed',error:{message:'You’ve hit your usage limit.'}}}]});
  },
  clock__curr_time:async()=>({current_time:'2026-10-24 16:00:00 UTC'}),
  mcp__codex_app__send_message_to_thread:async args=>{sends.push(args.threadId);return envelope({threadId:args.threadId});}
 };
 const report=await run(tools,{python:'python',script:'recovery_fast.py',state:'fixture.json'});
 assert.deepEqual(sends,ids,'A transient quota failure on the first recorded target must not lose either task');
 assert.equal(quotaReads,3);assert.equal(report.failures.length,0);
 assert.equal(report.sent.length,2);assert.ok(report.sent.every(item=>item.running));
}
(async()=>{for(const kind of ['success','rejected','uncertain','process_failure','quota_retry','status_retry','quota_unavailable','quota_fallback','stale_fallback'])await scenario(kind);await twoRecordedTargets();console.log('Native dispatcher: two recorded tasks, bounded reads, fresh quota-only fallback, stale rejection, exact text and no uncertain send retry verified.');})().catch(e=>{console.error(e);process.exitCode=1;});
