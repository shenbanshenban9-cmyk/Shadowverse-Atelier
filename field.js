let fieldEdit=null,variantFieldEdits={};
async function fieldPanel(j,host){
 const ci=idx,card=cards[ci],variant=slots[j].variant||0;
 const response=await fetch('field?i='+ci+'&variant='+variant);
 if(!response.ok)throw Error('取景参数读取失败');
 const original=await response.json();
 if(!host.isConnected)return;
 const pending=variant?variantFieldEdits[variant]:fieldEdit;
 const uv=(pending?.after||original).slice();
 const touched=new Set(pending?.states||[]);
 const names=['常态','进化','超进化'];
 const rarity=['bronze','silver','gold','legend'][card.rar-1]||'bronze';
 const redraws=[];
 host.innerHTML='<h3>战场取景</h3><div class="field-cards">'+names.map((name,state)=>'<section class="field-state" data-state="'+state+'"><h4>'+name+'</h4><canvas aria-label="'+name+'取景，拖动调整位置" width="256" height="256"></canvas><label>取景范围<input type="range" min=".1" max="1.5" step=".001"></label><button type="button" data-reset>恢复默认位置</button></section>').join('')+'</div><p><button type="button" data-save>保存取景</button></p><div class="field-status" aria-live="polite"></div>';
 const status=host.querySelector('.field-status');
 host.redraw=()=>redraws.forEach(draw=>draw());
 function changed(state){
  touched.add(state);
  status.textContent='取景已调整，点击保存取景。';
 }
 await Promise.all(names.map(async(name,state)=>{
  const section=host.querySelector('[data-state="'+state+'"]');
  const canvas=section.querySelector('canvas'),ctx=canvas.getContext('2d'),range=section.querySelector('input');
  const offset=state*4;
  const frameName='frame2d_field_follower_'+['','evol_','super_evol_'][state]+rarity+'.png';
  const imageIndex=slots.findIndex(slot=>isFace(slot)&&(slot.variant||0)===variant&&(slot.state||0)===(state?1:0));
  if(imageIndex<0){
   ctx.fillStyle='white';ctx.fillText('无对应卡面',80,120);
   range.disabled=true;section.querySelector('button').disabled=true;
   return;
  }
  const [art,frame,mask]=await Promise.all([
   loadPicture(urls[imageIndex]||'current?i='+ci+'&s='+imageIndex),
   loadPicture('fieldpic?file='+frameName),
   loadPicture('fieldpic?file=mask_'+frameName)
  ]);
  if(!host.isConnected)return;
  const box=state===2?[29,52,198,149]:[64,49,128,154];
  const windowUV=state===2?[.012,.348,.974,.594]:[.065,.075,.868,.86];
  const [x,y,width,height]=box,[left,bottom,uw,vh]=windowUV;
  function draw(){
   const source=imageIndex===j&&modal.querySelector('#newPane canvas')||art;
   const sx=(uv[offset+2]+uv[offset]*left)*source.width;
   const sy=(1-uv[offset+3]-uv[offset+1]*(bottom+vh))*source.height;
   ctx.clearRect(0,0,256,256);
   ctx.drawImage(source,sx,sy,uv[offset]*uw*source.width,uv[offset+1]*vh*source.height,x,y,width,height);
   ctx.globalCompositeOperation='destination-in';ctx.drawImage(mask,0,0,256,256);
   ctx.globalCompositeOperation='source-over';ctx.drawImage(frame,0,0,256,256);
   range.min=Math.min(.1,uv[offset]);range.max=Math.max(1.5,uv[offset]);range.value=uv[offset];
  }
  redraws.push(draw);draw();
  canvas.onpointerdown=event=>{
   canvas.setPointerCapture(event.pointerId);
   let previousX=event.clientX,previousY=event.clientY;
   canvas.onpointermove=move=>{
    uv[offset+2]-=(move.clientX-previousX)*256/canvas.clientWidth/width*uv[offset]*uw;
    uv[offset+3]+=(move.clientY-previousY)*256/canvas.clientHeight/height*uv[offset+1]*vh;
    previousX=move.clientX;previousY=move.clientY;changed(state);draw();
   };
   canvas.onpointerup=()=>canvas.onpointermove=null;
   canvas.onlostpointercapture=()=>canvas.onpointermove=null;
  };
  range.oninput=()=>{
   const factor=Number(range.value)/uv[offset];
   const dw=uv[offset]*(factor-1),dh=uv[offset+1]*(factor-1);
   uv[offset+2]-=dw*(left+uw/2);uv[offset+3]-=dh*(bottom+vh/2);
   uv[offset]*=factor;uv[offset+1]*=factor;changed(state);draw();
  };
  section.querySelector('[data-reset]').onclick=async event=>{
   const button=event.currentTarget;button.disabled=true;
   try{
    const result=await fetch('field-default?i='+ci+'&variant='+variant);
    if(!result.ok)throw Error('未能读取原始取景');
    const defaults=await result.json();if(!host.isConnected)return;
    uv.splice(offset,4,...defaults.slice(offset,offset+4));changed(state);draw();
   }catch(error){alert(error.message)}
   finally{button.disabled=false}
  };
 }));
 if(!host.isConnected)return;
 host.querySelector('[data-save]').onclick=()=>{
  const edit={before:original,after:uv.slice(),states:[...touched]};
  if(variant)variantFieldEdits[variant]=edit;else fieldEdit=edit;
  status.textContent='取景已保存，点击“备份并替换”写入游戏。';
  detail();
 };
}
