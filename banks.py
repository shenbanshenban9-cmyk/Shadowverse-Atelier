import struct
def u(b,k):return struct.unpack_from('<I',b,k)[0]
def entries(b):
 if b[:4]!=b'AKPK':return []
 pos=28+u(b,12);rows=[]
 for sz in struct.unpack_from('<III',b,16):
  if sz:
   count=u(b,pos)
   if sz!=4+20*count:return []
   for i in range(count):
    off=pos+4+i*20;media,align,size,block,lang=struct.unpack_from('<5I',b,off)
    start=align*block
    if not align or start+size>len(b):return []
    rows.append((start,size,off,media,align))
  pos+=sz
 return sorted(rows)
def combine(a,b):
 if a==b:return a
 head,_,x=a.partition(' · ')
 other,_,y=b.partition(' · ')
 if x and other==head:
  for prefix in ('联动 ','增强'):
   if x.startswith(prefix) and y.startswith(prefix):return head+' · '+prefix+x[len(prefix):]+' / '+y[len(prefix):]
  return head+' · '+x+' / '+y
 return a+' / '+b
def labels(b,events):
 objs={};media={};clips=[];pos=0
 while (pos:=b.find(b'RIFF',pos))>=0:
  if b[pos+8:pos+12]==b'WAVE':clips.append(pos);pos+=8+u(b,pos+4)
  else:pos+=4
 for start,size,off,mid,align in entries(b):
  if start in clips:media[mid]=clips.index(start)+1
  if b[start:start+4]!=b'BKHD':continue
  k=start
  while k+8<=start+size:
   end=k+8+u(b,k+4)
   if end>start+size:break
   if b[k:k+4]==b'DIDX':
    for j in range(k+8,end,12):
     ptr=end+8+u(b,j+4)
     if ptr in clips:media[u(b,j)]=clips.index(ptr)+1
   if b[k:k+4]==b'HIRC':
    j=k+12
    while j+5<=end:
     z=u(b,j+1);v=b[j+5:j+5+z]
     if len(v)>=4:objs[u(v,0)]=(b[j],v)
     j+=5+z
   k=end
 out={}
 for name,label in events.items():
  h=2166136261
  for char in name.lower().encode():h=((h*16777619)&0xffffffff)^char
  try:
   typ,event=objs[h];typ,action=objs[u(event,5)];typ,sound=objs[u(action,6)]
   if typ==2:
    index=str(media[u(sound,9)])
    previous=out.get(index)
    out[index]=label if previous is None else combine(previous,label)
  except (KeyError,struct.error):pass
 return out
def _index(b):
 objs={};media={};clips=[];pos=0
 while (pos:=b.find(b'RIFF',pos))>=0:
  if b[pos+8:pos+12]==b'WAVE':clips.append(pos);pos+=8+u(b,pos+4)
  else:pos+=4
 for start,size,off,mid,align in entries(b):
  if start in clips:media[mid]=clips.index(start)+1
  if b[start:start+4]!=b'BKHD':continue
  k=start
  while k+8<=start+size:
   end=k+8+u(b,k+4)
   if end>start+size:break
   if b[k:k+4]==b'DIDX':
    for j in range(k+8,end,12):
     ptr=end+8+u(b,j+4)
     if ptr in clips:media[u(b,j)]=clips.index(ptr)+1
   if b[k:k+4]==b'HIRC':
    j=k+12
    while j+5<=end:
     z=u(b,j+1);v=b[j+5:j+5+z]
     if len(v)>=4:objs[u(v,0)]=(b[j],v)
     j+=5+z
   k=end
 return objs,media,clips

def event_media(b,names):
 objs,media,_=_index(b)
 out={}
 for name in names:
  h=2166136261
  for char in name.lower().encode():h=((h*16777619)&0xffffffff)^char
  try:
   typ,event=objs[h];typ,action=objs[u(event,5)];typ,sound=objs[u(action,6)]
   if typ==2:out[name]=str(media[u(sound,9)])
  except (KeyError,struct.error):pass
 return out

def clip_media(b):
 objs,media,clips=_index(b)
 result={}
 for start,size,off,mid,align in entries(b):
  if start in clips:result[clips.index(start)+1]=b[start:start+size]
  if b[start:start+4]!=b'BKHD':continue
  k=start
  while k+8<=start+size:
   end=k+8+u(b,k+4)
   if end>start+size:break
   if b[k:k+4]==b'DIDX':
    for j in range(k+8,end,12):
     ptr=end+8+u(b,j+4)
     if ptr in clips:result[clips.index(ptr)+1]=b[ptr:ptr+u(b,j+8)]
   k=end
 return result

def media_slots(b):
 slots={}
 for start,size,off,mid,align in entries(b):
  payload=b[start:start+size]
  if payload[:4]==b'RIFF':slots[(mid,None)]=payload
  if payload[:4]!=b'BKHD':continue
  chunks=bank_chunks(payload)
  data=next((v for tag,v in chunks if tag==b'DATA'),b'')
  for tag,index in chunks:
   if tag==b'DIDX':
    for pos in range(0,len(index),12):
     media,offset,length=struct.unpack_from('<III',index,pos)
     slots[(mid,media)]=data[offset:offset+length]
 return slots

def bank_chunks(payload):
 result=[];pos=0
 while pos+8<=len(payload):
  size=u(payload,pos+4);end=pos+8+size
  if end>len(payload):raise ValueError('语音银行区块损坏')
  result.append((payload[pos:pos+4],payload[pos+8:end]));pos=end
 if pos!=len(payload):raise ValueError('语音银行存在无法识别的尾部')
 return result

def ordered_media(package):
 rows=[]
 for start,size,offset,bank,align in entries(package):
  payload=package[start:start+size]
  if payload[:4]==b'RIFF':
   rows.append((start,(bank,None),payload))
  elif payload[:4]==b'BKHD':
   chunks=bank_chunks(payload);cursor=0;data_start=None;audio=b'';index=b''
   for tag,value in chunks:
    if tag==b'DATA':data_start=start+cursor+8;audio=value
    if tag==b'DIDX':index=value
    cursor+=8+len(value)
   if data_start is not None:
    for pos in range(0,len(index),12):
     media,offset,length=struct.unpack_from('<III',index,pos)
     rows.append((data_start+offset,(bank,media),audio[offset:offset+length]))
 return [(key,payload) for position,key,payload in sorted(rows)]

def media_key(file,original,current=None):
 import json
 reference=media_slots(original)
 mapping=file.with_suffix('.media.json')
 if mapping.is_file():
  metadata=json.loads(mapping.read_text('utf8'))
  key=(metadata['bank'],metadata['media'])
  if key in reference:return key
  raise ValueError('语音资源编号已变化，请重新打开此卡牌')
 payload=file.read_bytes()
 matches=[key for key,value in reference.items() if value==payload]
 if len(matches)!=1 and current is not None:
  matches=[key for key,value in media_slots(current).items() if value==payload and key in reference]
 if len(matches)!=1:raise ValueError('无法确定语音资源编号，请重新打开此卡牌')
 return matches[0]

def replace_media(b,items,original):
 reference=media_slots(original);current=media_slots(b);changes={}
 for f,value in items:
  key=media_key(f,original,b)
  if key not in current:raise ValueError('当前语音包缺少该资源编号')
  if value[:4]!=b'RIFF' or value[8:12]!=b'WAVE' or len(value)!=8+u(value,4):raise ValueError('请选择有效 WEM')
  changes[key]=value
 rows=entries(b)
 if not rows:raise ValueError('不支持的语音包索引')
 result=bytearray(b[:rows[0][0]])
 for start,size,off,mid,align in rows:
  payload=b[start:start+size]
  if (mid,None) in changes:payload=changes[(mid,None)]
  elif any(key[0]==mid for key in changes):
   chunks=bank_chunks(payload)
   data=next(v for tag,v in chunks if tag==b'DATA')
   index=bytearray(next(v for tag,v in chunks if tag==b'DIDX'));rebuilt=bytearray()
   for pos in range(0,len(index),12):
    media,offset,length=struct.unpack_from('<III',index,pos)
    rebuilt.extend(bytes(-len(rebuilt)%16))
    value=changes.get((mid,media),data[offset:offset+length])
    struct.pack_into('<III',index,pos,media,len(rebuilt),len(value));rebuilt.extend(value)
   chunks=[(tag,bytes(index) if tag==b'DIDX' else bytes(rebuilt) if tag==b'DATA' else value) for tag,value in chunks]
   payload=b''.join(tag+struct.pack('<I',len(value))+value for tag,value in chunks)
  result.extend(bytes(-len(result)%align))
  struct.pack_into('<II',result,off+8,len(payload),len(result)//align);result.extend(payload)
 output=bytes(result)
 expected=dict(current);expected.update(changes)
 if media_slots(output)!=expected:raise ValueError('语音包重建验证失败')
 return output

def replace_external(b,items):
 rows=entries(b); replacements={}
 for f,v in items:
  src=f.read_bytes()
  matched=[start for start,size,off,mid,align in rows if b[start:start+size]==src]
  if len(matched)!=1:return None
  if v[:4]!=b'RIFF' or v[8:12]!=b'WAVE' or len(v)!=8+u(v,4):raise ValueError('请选择有效 WEM')
  replacements[matched[0]]=v
 result=bytearray(b[:rows[0][0]])
 for start,size,off,mid,align in rows:
  result.extend(bytes(-len(result)%align));payload=replacements.get(start,b[start:start+size]);struct.pack_into('<II',result,off+8,len(payload),len(result)//align);result.extend(payload)
 return bytes(result)
