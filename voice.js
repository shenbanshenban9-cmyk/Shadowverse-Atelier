const voiceStates = {};
let voiceGeneration = 0;
function clearVoices(){
 voiceGeneration++;
 for(const key of Object.keys(voiceStates)){URL.revokeObjectURL(voiceStates[key].preview);delete voiceStates[key]}
}
function voiceTools(j){
 const gain=voiceStates[j]?.gain??1;
 return `<div class="voice-tools" data-voice="${j}"><canvas class="voice-wave" width="600" height="152" aria-label="音频波形"></canvas><label>音量 <input type="range" min="0" max="200" step="1" value="${Math.round(gain*100)}" disabled><output>${Math.round(gain*100)}%</output></label><p class="voice-status">正在读取波形…</p><button type="button" onclick="restoreVoice(${j},this)">恢复原音频</button></div>`;
}
function voiceWav(buffer,gain){
 const channels=buffer.numberOfChannels,frames=buffer.length;
 const bytes=new ArrayBuffer(44+frames*channels*2),v=new DataView(bytes);
 const text=(offset,s)=>{for(let i=0;i<s.length;i++)v.setUint8(offset+i,s.charCodeAt(i))};
 text(0,'RIFF');v.setUint32(4,bytes.byteLength-8,true);text(8,'WAVE');text(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,channels,true);v.setUint32(24,buffer.sampleRate,true);v.setUint32(28,buffer.sampleRate*channels*2,true);v.setUint16(32,channels*2,true);v.setUint16(34,16,true);text(36,'data');v.setUint32(40,bytes.byteLength-44,true);
 for(let c=0;c<channels;c++){
  const samples=buffer.getChannelData(c);
  for(let i=0;i<frames;i++){const value=Math.max(-1,Math.min(1,samples[i]*gain));v.setInt16(44+(i*channels+c)*2,Math.round(value*(value<0?32768:32767)),true)}
 }
 return new Blob([bytes],{type:'audio/wav'});
}
function drawVoice(j,element){
 const state=voiceStates[j],canvas=element.querySelector('canvas'),ctx=canvas.getContext('2d');
 ctx.clearRect(0,0,canvas.width,canvas.height);ctx.strokeStyle='#ddc591';ctx.beginPath();
 for(let x=0;x<canvas.width;x++){
  let peak=0;
  const from=Math.floor(x*state.buffer.length/canvas.width),to=Math.max(from+1,Math.floor((x+1)*state.buffer.length/canvas.width));
  for(let c=0;c<state.buffer.numberOfChannels;c++){
   const samples=state.buffer.getChannelData(c);
   for(let i=from;i<Math.min(to,samples.length);i++)peak=Math.max(peak,Math.abs(samples[i]));
  }
  const height=Math.min(1,peak*state.gain)*canvas.height*.46;
  ctx.moveTo(x,canvas.height/2-height);ctx.lineTo(x,canvas.height/2+Math.max(1,height));
 }
 ctx.stroke();element.querySelector('output').value=Math.round(state.gain*100)+'%';
 element.querySelector('.voice-status').textContent=state.dirty?'音量已调整 · 待写入':changes[j]?'新音频 · 可直接试听 · 待写入':'';
}
async function initVoices(){
 const generation=voiceGeneration;
 for(const element of document.querySelectorAll('[data-voice]')){
  const j=Number(element.dataset.voice);
  try{
   if(!voiceStates[j]){
    const audio=element.previousElementSibling;
    const response=await fetch(audio.src);if(!response.ok)throw Error('读取音频失败');
    const context=new AudioContext();let buffer;
    try{buffer=await context.decodeAudioData(await response.arrayBuffer())}finally{await context.close()}
    if(generation!==voiceGeneration||!element.isConnected)return;
    voiceStates[j]={buffer,gain:1,dirty:false};
   }
   if(!element.isConnected)continue;
   const state=voiceStates[j],slider=element.querySelector('input');slider.disabled=false;
   drawVoice(j,element);
   if(state.preview)element.previousElementSibling.src=state.preview;
   slider.oninput=()=>{
    state.gain=Number(slider.value)/100;state.dirty=true;drawVoice(j,element);
    const player=element.previousElementSibling,time=player.currentTime,playing=!player.paused;
    const previous=state.preview;state.preview=URL.createObjectURL(voiceWav(state.buffer,state.gain));player.src=state.preview;
    player.onloadedmetadata=()=>{player.currentTime=Math.min(time,player.duration);if(playing)player.play().catch(()=>{})};
    if(previous)URL.revokeObjectURL(previous);
    document.querySelector('.apply').disabled=false;
   };
  }catch(e){if(element.isConnected)element.querySelector('.voice-status').textContent=e.message}
 }
}
async function restoreVoice(j,button){
 button.disabled=true;
 try{
  const response=await fetch('voice-original?i='+idx+'&s='+j);if(!response.ok)throw Error('原音频读取失败');
  const raw=new Uint8Array(await response.arrayBuffer());let binary='';
  for(let i=0;i<raw.length;i+=8192)binary+=String.fromCharCode(...raw.subarray(i,i+8192));
  if(voiceStates[j]?.preview)URL.revokeObjectURL(voiceStates[j].preview);
  await stageVoice(j,btoa(binary));detail();
 }catch(e){alert(e.message);button.disabled=false}
}
async function prepareVoiceChanges(){
 for(const key of Object.keys(voiceStates)){
  const state=voiceStates[key];if(!state.dirty)continue;
  const file=new File([voiceWav(state.buffer,state.gain)],'volume.wav',{type:'audio/wav'});
  await encodeVoice(Number(key),file);
 }
}
