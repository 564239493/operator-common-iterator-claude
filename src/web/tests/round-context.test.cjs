const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {transformSync}=require('esbuild');
const vue=require('vue');
const root=path.resolve(__dirname,'../src');
const page=fs.readFileSync(path.join(root,'pages/RunPage.vue'),'utf8');
function setup(){
 // round_states：后端共享判据（progress_rules.per_round_states）的带轮次角色状态
 const roundStates={
  1:{'constraint-extractor':{status:'passed',basis:'首轮约束已提交',inferred:true},
     'constraint-updater':{status:'pending',basis:'本轮尚无诊断，未进入更新回路',inferred:true},
     'case-generator':{status:'running',basis:'历史轮次存在运行中证据，可能为遗留',inferred:true}},
  2:{'constraint-updater':{status:'passed',basis:'约束更新完成：11 处最小修改',inferred:true},
     'case-generator':{status:'passed',basis:'generation_progress.state=complete',inferred:true}},
  6:{'constraint-updater':{status:'unconfirmed',basis:'诊断建议更新但未见 constraint_update.json',inferred:true},
     'case-generator':{status:'passed',basis:'generation_summary 有效，无冲突进度证据（约定判据）',inferred:true}}
 };
 const run=vue.ref({current_iteration:6,is_terminal:true,round_states:roundStates,iterations:[
  {n:1,execution:{passed:1,total:10,failed:9,verdict:'partial_pass',status_raw:'success'}},
  {n:2,execution:{passed:10,total:10,failed:0,verdict:'full_pass'},regression:{kind:'none',checked_cases:1},constraint_update:{change_count:11,finding_ids:['CF-1']}},
  {n:6,execution:{passed:97,total:100,failed:3,verdict:'partial_pass'},regression:{kind:'evaluator_unsupported'},constraint_update:{change_count:2,finding_ids:[]}}
 ]});
 const replay=vue.ref([{iteration:1,agent:'constraint-extractor',action:'passed',basis:'首轮提取'},{iteration:6,agent:'constraint-updater',action:'passed',basis:'本轮修改'}]);
 let fetchCount=0;
 let code=page.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm,'');
 code+='\nglobalThis.result={runView,selectedRound,rounds,roundEngineers,outcomes,metrics,nextRound,iterFor};';
 const context=vm.createContext({...vue,defineProps:()=>({runId:'fixture'}),useRouter:()=>({}),useTheme:()=>({theme:vue.ref('light'),toggle(){}}),usePolling:()=>({data:fetchCount++===0?run:replay,error:vue.ref(null),refresh(){},start(){},stop(){}}),useTask:()=>({loadError:vue.ref(null),ready:vue.ref(true),selectedRunId:vue.ref('fixture-run')}),useEngineers:()=>({engineers:vue.ref([{name:'constraint-extractor',runtime:{status:'passed'}},{name:'constraint-updater',runtime:{status:'passed'}}])}),onBeforeUnmount(){},setTimeout:()=>1,clearTimeout(){},Date,api:{}});
 vm.runInContext(transformSync(code,{loader:'ts',format:'cjs'}).code,context);
 return {s:context.result};
}
test('round states come from shared backend rules; no-record rounds are not forced to not_involved',async()=>{
 const {s}=setup();
 assert.equal(s.selectedRound.value,6);
 // 第 6 轮：updater 取后端共享判据（unconfirmed），无记录≠未参与
 assert.equal(s.roundEngineers.value[1].runtime.status,'unconfirmed');
 assert.match(s.roundEngineers.value[1].runtime.basis,/未见 constraint_update/);
 s.selectedRound.value=1;await vue.nextTick();
 // 第 1 轮：updater 无诊断 → pending（旧版"无记录一律 not_involved"已废弃）
 assert.equal(s.roundEngineers.value[1].runtime.status,'pending');
 // 后端 running 保留；历史轮的 running 由后端降级为待确认（可能为遗留）
 assert.equal(s.roundEngineers.value[0].runtime.status,'passed');
 s.selectedRound.value=2;await vue.nextTick();
 assert.equal(s.roundEngineers.value[1].runtime.status,'passed');
});
test('metrics and outcomes still follow the selected iteration',async()=>{
 const {s}=setup();assert.equal(s.selectedRound.value,6);assert.match(s.outcomes.value[0].title,/97\/100/);
 s.selectedRound.value=1;await vue.nextTick();assert.match(s.outcomes.value[0].title,/1\/10/);assert.equal(s.outcomes.value.length,1);assert.equal(s.iterFor('regression'),null);
 s.selectedRound.value=2;await vue.nextTick();assert.equal(s.metrics.value[0].value,'10 / 10');assert.match(s.outcomes.value[2].text,/11 处/);
 s.runView.value={...s.runView.value,iterations:[...s.runView.value.iterations,{n:7}],current_iteration:7};await vue.nextTick();assert.equal(s.selectedRound.value,2);
});
test('one shared avatar band and one round selector',()=>{
 assert.equal((page.match(/<AgentBand\b/g)||[]).length,1);
 const board=fs.readFileSync(path.join(root,'components/board/HandoffBoard.vue'),'utf8');
 assert(!board.includes('<RobotHead'));assert(!board.includes('role="tablist"'));assert(board.includes('e.iteration === props.round'));
 // 交接图核心抽为共享纯函数：明确 to_agent 才有边，禁止相邻自动连线
 assert(board.includes("buildGraph(props.events.filter"));assert(!board.includes('e.to_agent || (next'));
 const detail=fs.readFileSync(path.join(root,'components/stage/AgentDetailGrid.vue'),'utf8');assert(!detail.includes('<el-select'));assert(detail.includes('v.n === props.round'));
 const band=fs.readFileSync(path.join(root,'components/band/AgentBand.vue'),'utf8');assert(band.includes('round-control'));
 // RunPage 不再客户端硬造 not_involved：roundEngineers 消费后端 round_states
 assert(page.includes('round_states'));assert(!page.includes("'not_involved'"));
});
test('shared band sits between screens, sticks at top and matches flow columns',()=>{
 const mainEnd=page.indexOf('class="screen screen-main"');
 const band=page.indexOf('class="band-dock"');
 const board=page.indexOf('class="screen screen-board"');
 assert(mainEnd<band&&band<board);
 assert.match(page,/\.band-dock\s*\{\s*position: sticky; top: 72px/);
 assert(!/\.band-dock\s*\{\s*position: fixed/.test(page));
 const avatars=fs.readFileSync(path.join(root,'components/band/AgentBand.vue'),'utf8');
 const flow=fs.readFileSync(path.join(root,'components/board/HandoffBoard.vue'),'utf8');
 assert(avatars.includes('laneWidth(props.viewport, props.engineers.length, props.zoom)'));assert(flow.includes('laneWidth(props.viewport, lanes.value.length, props.zoom)'));assert(flow.includes('laneBoundary(index, LANE_W)'));
 assert(avatars.includes("emit('scroll-x', bandScroller.value.scrollLeft)"));assert(flow.includes("emit('scroll-x', scroller.value.scrollLeft)"));
 assert(flow.includes('defineExpose({ selectAgent'));
});
function loadTs(relative){const source=fs.readFileSync(path.join(root,relative),'utf8');const module={exports:{}};vm.runInNewContext(transformSync(source,{loader:'ts',format:'cjs'}).code,{module,exports:module.exports});return module.exports;}
test('shared column geometry fills viewport, puts dividers at cell edges at every zoom',()=>{
 const g=loadTs('components/board/flow-layout.ts');
 for(const viewport of [800,1920,2560])for(const zoom of [.5,.75,1]){
  const w=g.laneWidth(viewport,12,zoom);assert(w>=154);assert((130+12*w)*zoom>=viewport-.001);
  for(let i=0;i<12;i++){assert.equal(g.laneCenter(i,w),g.laneBoundary(i,w)+w/2);assert(Math.abs(g.laneBoundary(i+1,w)-g.laneBoundary(i,w)-w)<.001);}
 }
});
test('input materials are role-specific and previous-round update references stay scoped',()=>{
 const {stageInputs}=loadTs('components/stage/stage-inputs.ts');const run={operator_doc:'inputs/doc.md',iterations:[{n:1,exists:{'analysis.json':true}},{n:2,exists:{'constraints.json':true}}]};
 const scanner=stageInputs('scene-scanner',2,run);assert.equal(scanner.length,1);assert.equal(scanner[0].path,'inputs/doc.md');
 const generator=stageInputs('case-generator',2,run);assert(!generator.some(x=>x.path==='inputs/doc.md'));assert(generator.some(x=>x.path==='iter_002/constraints.json'&&x.evidence==='文件已记录'));
 const updater=stageInputs('constraint-updater',2,run);assert(updater.some(x=>x.path==='iter_001/analysis.json'&&x.evidence==='文件已记录'));assert(!updater.some(x=>x.path==='iter_002/analysis.json'));
});
test('graph core: adjacency is not handoff evidence and node status survives edges',()=>{
 const {buildGraph}=loadTs('components/board/graph.ts');
 // 相邻两条无 to_agent 的完成事件：两个独立节点、零条边
 const standalone=buildGraph([{iteration:2,agent:'source-analyst',action:'passed',basis:'b1',inferred:true},{iteration:2,agent:'scene-scanner',action:'passed',basis:'b2',inferred:true}]);
 assert.equal(standalone.nodes.length,2);assert.equal(standalone.links.length,0);
 // 明确 to_agent 才有边；接收方状态不被边覆盖
 const linked=buildGraph([{iteration:2,agent:'case-generator',action:'passed',to_agent:'case-executor',basis:'b3',inferred:true},{iteration:2,agent:'case-executor',action:'unconfirmed',basis:'b4',inferred:true}]);
 assert.equal(linked.links.length,1);
 const target=linked.nodes.find(n=>n.agent==='case-executor');assert.equal(target.action,'unconfirmed');
 // passed 无 to_agent：独立完成节点（不因无箭头消失）
 const solo=buildGraph([{iteration:2,agent:'source-analyst',action:'passed',basis:'b5',inferred:true}]);
 assert.equal(solo.nodes.length,1);assert.equal(solo.nodes[0].action,'passed');
});
