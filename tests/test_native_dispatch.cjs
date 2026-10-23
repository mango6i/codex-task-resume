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
   const action=cmd.match(/ (plan|reserve|commit|finish|ack-pause|ack-arm) --json /)[1];
   const payload=JSON.parse(cmd.slice(cmd.indexOf(' --json ')+8).slice(1,-1).replace(/''/g,"'"));
   calls.push({action,payload});
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
    latestTurn:{id:reads===1?'captured':'new',status:reads===1?'failed':'inProgress'},
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
 const report=await run(tools,{python:"C:/Python's/python.exe",script:'C:/plugin/recovery_fast.py',state:'C:/saved/state.json'});
 assert.equal(calls.filter(c=>c.action==='send').length,kind==='rejected'?0:1);
 if(kind!=='rejected')assert.equal(calls.find(c=>c.action==='commit').payload.outcome,kind==='uncertain'?'uncertain':'sent');
 assert.equal(JSON.stringify(calls).includes('DO NOT forward'),false);
 assert.equal(calls.at(-1).action,'ack-pause');
 assert.equal(report.sent.length,kind==='success'?1:0);
 if(kind==='success')assert.equal(report.sent[0].running,true);
}
(async()=>{for(const kind of ['success','rejected','uncertain'])await scenario(kind);console.log('Native dispatcher: recorded target only, reserve/send/commit order, exact text, no history and no uncertain retry verified.');})().catch(e=>{console.error(e);process.exitCode=1;});
