let foilEdits = {};
function openFoil() {
 const faces = slots.map((slot,index)=>({slot,index})).filter(entry=>entry.slot.foil);
 modal.innerHTML='<h2>闪卡动态预览</h2><select aria-label="选择卡面"></select><canvas style="display:block;height:min(48vh,460px);width:auto;margin:12px auto"></canvas><p role="status"></p><button data-mode="common">改为闪卡通用特效</button> <button data-mode="original">还原闪卡特效</button> <button data-close>关闭</button>';
 const select=modal.querySelector('select'), status=modal.querySelector('[role=status]');
 let canvas=modal.querySelector('canvas');
 for(const {slot,index} of faces)select.add(new Option(slot.label,index));
 let dispose=()=>{};
 let nativeImage=null;
 modal.addEventListener('close',()=>{version++;dispose()},{once:true});
 let version=0;
 async function draw(mode) {
  dispose();dispose=()=>{};
  const token=++version,index=Number(select.value),slot=slots[index];
  const action=mode||foilEdits[slot.art]||'current';
  nativeImage?.remove();nativeImage=null;canvas.hidden=false;canvas.style.display='block';
  const controller=new AbortController();
  dispose=()=>controller.abort();
  status.textContent='正在读取闪卡材质…';
  try {
   try{
    const response=await fetch('foil-native',{method:'POST',signal:controller.signal,
     body:JSON.stringify({i:idx,slot:index,art:slot.art,action,image:changes[index]})});
    if(!response.ok)throw Error(await response.text());
    const url=URL.createObjectURL(await response.blob());
    let image;
    try{image=await loadPicture(url)}catch(error){URL.revokeObjectURL(url);throw error}
    if(token!==version||!canvas.isConnected||!modal.open){URL.revokeObjectURL(url);return}
    image.style.cssText=canvas.style.cssText;
    canvas.style.display='none';canvas.after(image);nativeImage=image;
    dispose=()=>{controller.abort();URL.revokeObjectURL(url);image.remove()};
    if(mode){foilEdits[slot.art]=mode;detail()}
    status.textContent=(foilEdits[slot.art]?'待写入 · ':'')+'Direct3D 原版着色器预览';
    return;
   }catch(error){
    if(token!==version||controller.signal.aborted)return;
    console.warn('Native foil unavailable:',error);
   }
   const response=await fetch('foil-preview?'+new URLSearchParams({i:idx,art:slot.art,action}));
   if(!response.ok)throw Error(await response.text());
   const material=await response.json();
   const {layers,mask}=material;
   const [face,maskImage]=await Promise.all([loadPicture(urls[index]||'current?i='+idx+'&s='+index),mask?loadPicture(mask):null]);
   const cssHeight=Math.min(window.innerHeight*.48,460);
   canvas.height=Math.min(face.naturalHeight,Math.round(cssHeight*window.devicePixelRatio));
   canvas.width=Math.round(canvas.height*ratio(slot));
   const W=canvas.width,H=canvas.height;
   const output=canvas.cloneNode();
   try {
    const renderer=await foilWebGL(output,face,material,maskImage);
    if(token!==version||!canvas.isConnected||!modal.open){renderer();return}
    canvas.replaceWith(output);canvas=output;dispose=renderer;
    if(mode){foilEdits[slot.art]=mode;detail()}
    status.textContent=(foilEdits[slot.art]?'待写入 · ':'')+'WebGL 材质预览（近似）';
    return;
   }catch(error){console.warn('Foil WebGL unavailable:',error)}
   if(token!==version||!canvas.isConnected||!modal.open)return;
   const fallback=canvas.cloneNode();canvas.replaceWith(fallback);canvas=fallback;
   const ctx=canvas.getContext('2d');
   const prepared=await Promise.all(layers.map(async layer=>{
    const image=await loadPicture(layer.image);
    const tile=document.createElement('canvas');tile.width=W;tile.height=H;
    const context=tile.getContext('2d');
    const maskCanvas=tile.cloneNode();const m=maskCanvas.getContext('2d');
    if(maskImage){
     m.drawImage(maskImage,0,0,W,H);const pixels=m.getImageData(0,0,W,H);
     const channel=layer.kind.startsWith('_Back')?0:layer.kind.startsWith('_Middle')?1:2;
     for(let i=0;i<pixels.data.length;i+=4)pixels.data[i+3]=pixels.data[i+channel];
     m.putImageData(pixels,0,0);
    }else{m.fillStyle='rgba(255,255,255,.38)';m.fillRect(0,0,W,H)}
    return {...layer,tile,context,maskCanvas,pattern:context.createPattern(image,'repeat'),image};
   }));
   if(token!==version||!canvas.isConnected||!modal.open)return;
   if(mode){foilEdits[slot.art]=mode;detail()}
   status.textContent=(foilEdits[slot.art]?'待写入 · ':'')+'材质近似预览，游戏内光照与混合效果可能有差异。';
   const highlights=canvas.cloneNode();
   const light=highlights.getContext("2d");
   const blendOrder={Back:0,Middle:1,Front:2};
   const depth=l=>blendOrder[l.kind.match(/Back|Middle|Front/)[0]];
   prepared.sort((a,b)=>depth(a)-depth(b));
   function frame(ms){
    if(token!==version||!canvas.isConnected||!modal.open)return;
    const t=ms/1000;ctx.globalCompositeOperation='source-over';ctx.drawImage(face,0,0,W,H);
    light.clearRect(0,0,W,H);
    for(const l of prepared){
     const c=l.context;c.clearRect(0,0,W,H);
     const sx=l.scale[0]||1,sy=l.scale[1]||1;
     const angle=l.angle*Math.PI/180;
     const du=l.u*Math.cos(angle)-l.v*Math.sin(angle);
     const dv=l.u*Math.sin(angle)+l.v*Math.cos(angle);
     const x=-(l.offset[0]+du*t)*W/sx,y=(l.offset[1]+dv*t)*H/sy;
     const matrix=new DOMMatrix().translate(W/2,H/2).rotate(l.rotation*t*360).translate(-W/2+x,-H/2+y).scale(W/l.image.width/(l.scale[0]||1),H/l.image.height/(l.scale[1]||1));
     l.pattern.setTransform(matrix);c.fillStyle=l.pattern;c.fillRect(0,0,W,H);
     c.globalCompositeOperation='destination-in';c.drawImage(l.maskCanvas,0,0);c.globalCompositeOperation='source-over';
     const pulse=l.flash?l.minimum+(l.maximum-l.minimum)*(.5-.5*Math.cos(t*l.speed*Math.PI*2)):1;
     light.globalCompositeOperation=l.blend===1?'lighter':'screen';
     light.globalAlpha=Math.min(1,Math.max(0,l.alpha*pulse));light.drawImage(l.tile,0,0);
    }
    ctx.globalCompositeOperation='lighter';ctx.globalAlpha=.72;ctx.drawImage(highlights,0,0);
    ctx.globalAlpha=1;requestAnimationFrame(frame)
   }
   requestAnimationFrame(frame)
  }catch(e){if(token===version)status.textContent='读取失败：'+e.message}
 }
 select.onchange=()=>draw();
 modal.querySelector('[data-mode=original]').onclick=()=>draw('original');
 modal.querySelector('[data-mode=common]').onclick=()=>{
  const picker=document.createElement('dialog');
  picker.innerHTML='<h2>选择通用闪卡样式</h2><form method="dialog"><button value="common">花影鎏金 · 花瓣与金尘</button> <button value="common:frost">冰晶星河 · 星点与光泡</button> <button value="common:silk">紫羽流光 · 羽片与花影</button> <button value="">取消</button></form>';
  document.body.append(picker);
  picker.addEventListener('click',event=>{
   const button=event.target.closest('button');
   if(!button)return;
   event.preventDefault();
   picker.close();picker.remove();
   if(button.value)draw(button.value);
  });
  picker.onclose=()=>picker.remove();
  picker.showModal();
 };
 modal.querySelector('[data-close]').onclick=()=>modal.close();
 modal.showModal();draw();
}
 
async function foilWebGL(canvas, face, material, mask) {
 const layers=[...material.layers].sort((a,b)=>order(a)-order(b));
 function order(layer){return layer.kind.startsWith('_Back')?0:layer.kind.startsWith('_Middle')?1:2}
 const maps=await Promise.all([Promise.resolve(mask),material.secondary?loadPicture(material.secondary):null,
  ...material.distortion.map(d=>d?loadPicture(d.image):null),...layers.map(l=>loadPicture(l.image))]);
 const gl=canvas.getContext('webgl2',{alpha:false,antialias:false});
 if(!gl)throw Error('WebGL 2 不可用');
 const clean=[];
 let frame;
 function stop(){cancelAnimationFrame(frame);for(const release of clean.reverse())release()}
 try{
  const float=n=>Number.isFinite(n)?Number(n).toFixed(7):'0.0';
  const vec=(a,b)=>'vec2('+float(a)+','+float(b)+')';
  function uv(p){
   const a=p.angle*Math.PI/180;
   const u=p.u*Math.cos(a)-p.v*Math.sin(a),v=p.u*Math.sin(a)+p.v*Math.cos(a);
   if(p.radial||p.motion===4&&p.spiral){
    return 'radial('+vec(...p.scale)+','+vec(...p.offset)+','+vec(u,v)+','+
      float(p.rotation)+'*t,'+float(p.spiral)+','+(p.radial?'true':'false')+')';
   }
   return 'motion('+vec(...p.scale)+','+vec(...p.offset)+','+
    vec(p.motion===3?0:u,p.motion===3?0:v)+','+float(p.fixed*Math.PI/180)+'+'+
    float(p.motion===2?0:p.rotation*2*Math.PI)+'*t)';
  }
  const warp=material.distortion.map((d,i)=>d?
   '(texture(maps,vec3('+uv(d)+','+float(i+2)+')).rg*2.0-1.0)*'+vec(...d.intensity):'vec2(0.0)');
  const fragment=`#version 300 es
precision highp float;
precision highp sampler2DArray;
uniform sampler2D art;
uniform sampler2DArray maps;
uniform float t;
in vec2 p;
out vec4 result;
vec2 motion(vec2 scale,vec2 offset,vec2 scroll,float angle){
 float c=cos(angle),s=sin(angle);
 return (mat2(c,-s,s,c)*(p-0.5)+0.5)*scale+offset+scroll*t;
}
vec2 radial(vec2 scale,vec2 offset,vec2 scroll,float spin,float spiral,bool field){
 vec2 delta=(p-0.5)*vec2(1.0,1.15);
 float radius=length(delta);
 float angle=atan(delta.y,delta.x)/6.2831853+0.5;
 if(field)return vec2(angle,radius)*max(scale*100.0,vec2(1.0))+offset+scroll*t;
 return vec2(fract(angle+spin)*scale.x+offset.x,
             radius*scale.y+spiral*angle+offset.y)+scroll*t;
}
void main(){
 vec3 color=texture(art,p).rgb;
 vec3 primary=texture(maps,vec3(p,0.0)).rgb;
 vec3 secondary=texture(maps,vec3(p,1.0)).rgb;
 vec2 warp1=${warp[0]}, warp2=${warp[1]};
 ${layers.map((l,i)=>{
   const channel='rgb'[order(l)];
   const coverage=material.secondary?'max(primary.'+channel+',secondary.'+channel+'*0.5)':'primary.'+channel;
   const pulse=l.flash?'mix('+float(l.minimum)+','+float(l.maximum)+',0.5-0.5*cos(t*'+float(l.speed*2*Math.PI)+'))':'1.0';
   const displacement=l.warp===1?'+warp1':l.warp===2?'+warp2':l.warp===3?'+warp1+warp2':'';
   return `{
    vec4 effect=texture(maps,vec3(${uv(l)}${displacement},${float(i+4)}));
    float strength=clamp(${coverage}*${pulse}*${float(l.alpha)},0.0,1.0);
    ${l.blend===3?'color=mix(color,effect.rgb,strength*effect.a);':l.blend===2?'color=1.0-(1.0-color)*(1.0-effect.rgb*effect.a*strength);':'color+=effect.rgb*effect.a*strength;'}
   }`;
 }).join('\n')}
 result=vec4(clamp(color,0.0,1.0),1.0);
}`;
  function compile(type,source){
   const shader=gl.createShader(type);clean.push(()=>gl.deleteShader(shader));
   gl.shaderSource(shader,source);gl.compileShader(shader);
   if(!gl.getShaderParameter(shader,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(shader));
   return shader;
  }
  const program=gl.createProgram();clean.push(()=>gl.deleteProgram(program));
  gl.attachShader(program,compile(gl.VERTEX_SHADER,`#version 300 es
out vec2 p;
void main(){
 vec2 xy=vec2((gl_VertexID<<1)&2,gl_VertexID&2);
 p=vec2(xy.x,1.0-xy.y);
 gl_Position=vec4(xy*2.0-1.0,0,1);
}`));
  gl.attachShader(program,compile(gl.FRAGMENT_SHADER,fragment));gl.linkProgram(program);
  if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(program));
  gl.useProgram(program);
  function bind(unit,target,name){
   const texture=gl.createTexture();clean.push(()=>gl.deleteTexture(texture));
   gl.activeTexture(gl.TEXTURE0+unit);gl.bindTexture(target,texture);
   for(const param of [gl.TEXTURE_MIN_FILTER,gl.TEXTURE_MAG_FILTER])gl.texParameteri(target,param,gl.LINEAR);
   gl.uniform1i(gl.getUniformLocation(program,name),unit);
  }
  bind(0,gl.TEXTURE_2D,'art');
  gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,face);
  bind(1,gl.TEXTURE_2D_ARRAY,'maps');
  const tile=document.createElement('canvas'),size=384;tile.width=tile.height=size;
  const ctx=tile.getContext('2d');
  gl.texStorage3D(gl.TEXTURE_2D_ARRAY,1,gl.RGBA8,size,size,maps.length);
  maps.forEach((image,i)=>{
   ctx.clearRect(0,0,size,size);
   if(image)ctx.drawImage(image,0,0,size,size);
   else if(i===0){ctx.fillStyle='#616161';ctx.fillRect(0,0,size,size)}
   gl.texSubImage3D(gl.TEXTURE_2D_ARRAY,0,0,0,i,size,size,1,gl.RGBA,gl.UNSIGNED_BYTE,tile);
  });
  if(gl.getError()!==gl.NO_ERROR)throw Error('WebGL 纹理上传失败');
  const time=gl.getUniformLocation(program,'t'),start=performance.now();
  gl.viewport(0,0,canvas.width,canvas.height);
  function render(now){
   gl.uniform1f(time,(now-start)/1000);gl.drawArrays(gl.TRIANGLES,0,3);
   frame=requestAnimationFrame(render);
  }
  render(start);
  return stop;
 }catch(error){stop();throw error}
}
