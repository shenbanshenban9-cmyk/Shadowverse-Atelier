let stageDirty=false;
async function stageJSON(url){
 const response=await fetch(url);
 const result=await response.json();
 if(!response.ok)throw Error(result.error||'背景资源读取失败');
 return result;
}
async function stageList(){
 stageDirty=false;view='stages';main.innerHTML='<h1>战斗背景</h1><div class="stage-grid"></div>';
 try{const ids=await stageJSON('stages');if(view!=='stages')return;
 main.querySelector('.stage-grid').innerHTML=(ids.length?ids.map(id=>'<button onclick="openStage('+id+')"><img loading="lazy" src="stage-cover?stage='+id+'">场地 '+String(id).padStart(2,'0')+'</button>').join(''):'<p>暂无背景，请在设置中填写游戏路径。</p>')
 }catch(e){alert(e.message)}
}
async function openStage(stage){
 view='stage';stageDirty=false;
 main.innerHTML='<h1>场地 '+String(stage).padStart(2,'0')+'</h1><p role="status">正在读取背景资源…</p>';
 try{
 main.innerHTML='<section><h1>场地 '+String(stage).padStart(2,'0')+'</h1><p id="hint"></p><div class="stage-work"><div><h3>当前对局资源</h3><img id="installed"></div><div><h3>替换预览</h3><canvas id="stageCanvas"></canvas></div></div><div class="bar"><label>选择图片 <input type=file id="file" accept="image/*"></label><label>缩放 <input type=range id="zoom" min=1 max=5 step=".01" value=1></label><label>白化程度 <input type=range id="white" min=0 max=100 value=0> <output id="percent">0%</output></label></div><button id="restore" type="button">还原背景</button><button class="apply" id="write" disabled>备份并替换</button></section>';
 const el=id=>main.querySelector('#'+id),cv=el('stageCanvas'),file=el('file'),zoom=el('zoom'),white=el('white'),write=el('write');
 let current=0,source=null,x=.5,y=.5,serial=0,busy=false;
 function draw(){
  const ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);if(!source)return;
  const scale=Math.max(cv.width/source.width,cv.height/source.height)*Number(zoom.value);
  const w=source.width*scale,h=source.height*scale;ctx.drawImage(source,(cv.width-w)*x,(cv.height-h)*y,w,h);ctx.fillStyle='rgba(255,255,255,'+Number(white.value)/100+')';ctx.fillRect(0,0,cv.width,cv.height);el('percent').textContent=white.value+'%'
 }
 async function load(){
  zoom.disabled=white.disabled=true;el('percent').textContent='0%';
  const request=++serial;source=null;stageDirty=false;file.value='';write.disabled=file.disabled=true;zoom.value=1;white.value=0;x=y=.5;
  const url='stage-image?stage='+stage+'&layer='+current+'&revision='+Date.now();el('installed').removeAttribute('src');draw();
  el('hint').textContent='工坊按对局相机的视角，将选好的画面投影到天空盒的六个面。';
  try{let im=await loadPicture(url);if(request!==serial||view!=='stage')return;el('installed').src=im.src;cv.width=im.width;cv.height=im.height;source=im;const recipe=await stageJSON('stage-recipe?stage='+stage+'&layer='+current);if(recipe.src){const saved=await loadPicture(recipe.src);if(request!==serial||view!=='stage')return;source=saved;x=recipe.x;y=recipe.y;zoom.value=recipe.zoom;white.value=recipe.white}draw();if(recipe.src)el('installed').src=cv.toDataURL();zoom.disabled=white.disabled=file.disabled=false}catch(e){if(request===serial)el('hint').textContent='当前场地贴图读取失败：'+e.message}
 }
 file.onchange=async()=>{if(!file.files.length)return;const request=serial,url=URL.createObjectURL(file.files[0]);try{const im=await loadPicture(url);if(request!==serial)return;source=im;stageDirty=true;x=y=.5;zoom.value=1;draw();write.disabled=false}catch(e){alert(e.message)}finally{URL.revokeObjectURL(url)}};
 function changed(){if(!source||busy)return;stageDirty=true;write.disabled=false;draw()}
 zoom.oninput=white.oninput=changed;
 el('restore').onclick=async()=>{if(busy)return;
 try{const check=await fetch('stage-capabilities',{cache:'no-store'});if(!check.ok||!(await check.json()).restore){alert('此页面连接的是旧版后台。请关闭这个标签页，从桌面重新打开影之工坊，再进入背景页面。');return}}catch(e){alert('无法连接工坊后台，请从桌面重新打开影之工坊。');return}
 if(!await nativeConfirm('还原此场地所有已备份的背景和材质？'))return;
 busy=true;main.inert=true;try{const r=await fetch('stage-restore',{method:'POST',body:JSON.stringify({stage})});if(!r.ok)throw Error(await r.text());await load();if(source)el('hint').textContent='已还原原始背景，预览已更新。'}catch(e){alert(e.message)}finally{busy=false;main.inert=false}};

 cv.onpointerdown=e=>{if(!source||busy)return;cv.setPointerCapture(e.pointerId);let a=e.clientX,b=e.clientY;
 cv.onpointermove=m=>{if(m.clientX===a&&m.clientY===b)return;if(Number(zoom.value)===1)zoom.value=1.15;const scale=Math.max(cv.width/source.width,cv.height/source.height)*zoom.value;
 const rect=cv.getBoundingClientRect(),factor=Math.min(rect.width/cv.width,rect.height/cv.height);
 x=Math.min(1,Math.max(0,x-(m.clientX-a)/Math.max(1,(source.width*scale-cv.width)*factor)));y=Math.min(1,Math.max(0,y-(m.clientY-b)/Math.max(1,(source.height*scale-cv.height)*factor)));a=m.clientX;b=m.clientY;changed()};cv.onlostpointercapture=()=>cv.onpointermove=null};
 write.onclick=async()=>{busy=true;file.disabled=write.disabled=true;write.textContent='正在备份并写入…';try{
 const base=document.createElement('canvas');const ratio=Math.min(1,4096/Math.max(source.width,source.height));base.width=Math.max(1,Math.round(source.width*ratio));base.height=Math.max(1,Math.round(source.height*ratio));base.getContext('2d').drawImage(source,0,0,base.width,base.height);
 const recipe={src:base.toDataURL(),x,y,zoom:Number(zoom.value),white:Number(white.value)};
 const r=await fetch('stage-apply',{method:'POST',body:JSON.stringify({stage,layer:current,recipe,image:cv.toDataURL().split(',')[1]})});let text=await r.text();if(!r.ok)throw Error(text);stageDirty=false;await load();alert('背景已写入。备份位于 '+text)
 }catch(e){alert(e.message);write.disabled=false;file.disabled=false}finally{busy=false;write.textContent='备份并替换'}};
 await load()
 }catch(error){
  if(view!=='stage')return;
  main.innerHTML='<h1>战斗背景</h1><p id="stageError" role="alert"></p><button type="button" onclick="stageList()">返回场地列表</button>';
  main.querySelector('#stageError').textContent='场地读取失败：'+error.message;
 }
}
