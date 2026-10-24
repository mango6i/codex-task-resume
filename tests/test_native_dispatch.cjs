const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../skills/quota-resume/scripts/native_dispatch.js'),'utf8');
const run=new Function('tools','config','return (async()=>{'+code+'})()');
const envelope=v=>({content:[{type:'text',text:JSON.stringify(v)}]});
async function scenario(kind){
 const calls=[];let reads=0;
 const tools={
  exec_command:async ({cmd})=>{
   assert.equal(/[^\x00-\x7f]/.test(cmd),false,'Command syntax must contain ASCII only');
   const action=cmd.match(/ (plan|reserve|commit|finish|ack-pause|ack-arm) --payload-base64 /)[1];
   const encoded=cmd.match(/ --payload-base64 '([A-Za-z0-9+/=]+)'$/)[1];
   const payload=JSON.parse(Buffer.from(encoded,'base64').toString('utf8'));
   calls.push({action,payload});
   if(action==='reserve'&&kind==='process_failure')return {exit_code:1,output:''};
   let result={};
   if(action==='plan')result={targets:[{threadId:'recorded',windowKey:'window',dueAt:1}]};
   if(action==='reserve')result=kind==='rejected'?{accepted:false}:{accepted:true,threadId:'recorded',hostId:'local',message:'继续任务\n原文',token:'reserved'};
   if(action==='finish')result={action:'pause',arguments:{id:'same',status:'PAUSED'}};
   return {exit_code:0,output:JSON.stringify(result)};
  },
  mcp__codex_app__get_usage_limits:async()=>{calls.push({action:'quota'});return envelope({accountId:'saved'});},
  mcp__codex_app__wait_threads:async ({targets})=>{
   assert.deepEqual(targets,[{threadId:'recorded',hostId:'local'}]);
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
 const noSend=['rejected','process_failure'].includes(kind);
 assert.equal(calls.filter(c=>c.action==='send').length,noSend?0:1);
 if(!noSend)assert.equal(calls.find(c=>c.action==='commit').payload.outcome,kind==='uncertain'?'uncertain':'sent');
 assert.equal(JSON.stringify(calls).includes('DO NOT forward'),false);
 assert.equal(JSON.stringify(calls).includes('turn contents'),false);
 assert.equal(calls.find(c=>c.action==='reserve').payload.snapshot.polls[0].latestTurn.error.message,
  'You’ve hit your usage limit. 中文 ‘quoted’ 🚀');
 assert.equal(calls.at(-1).action,'ack-pause');
 assert.equal(report.sent.length,kind==='success'?1:0);
 if(kind==='success')assert.equal(report.sent[0].running,true);
 if(kind==='process_failure')assert.match(report.failures[0].reason,/Local reserve failed \(exit 1\)/);
}
(async()=>{for(const kind of ['success','rejected','uncertain','process_failure'])await scenario(kind);console.log('Native dispatcher: recorded target only, encoded Unicode data, reserve/send/commit order, exact text, no history and no uncertain retry verified.');})().catch(e=>{console.error(e);process.exitCode=1;});
