"""Read and edit only the twelve float32 field-art UV values in a MasterMemory row."""
import struct,math
from pip._vendor import msgpack
import lz4.block
MASTER='Master/mastermemory.bytes'
def reader(b):
 u=msgpack.Unpacker(raw=False,strict_map_key=False);u.feed(b);return u
def unpack(block):
 ext=msgpack.unpackb(block)
 if ext.code!=99 or ext.data[0]!=210:raise ValueError('不支持的主表压缩格式')
 return lz4.block.decompress(ext.data[5:],int.from_bytes(ext.data[1:5],'big'))
def fields(b,cid=None,edit=None,resource_id=None):
 u=reader(b);toc=next(u);start=u.tell()
 def raw(name):
  p,l=toc[name];return unpack(b[start+p:start+p+l])
 if resource_id is None:
  cards=msgpack.unpackb(raw('CardMaster'))
  card=next(r for r in cards if r[0]==cid);rid=card[2]
  linked={r[2] for r in cards if r[6]==card[6] and r[1]==card[1]}
 else:
  rid=resource_id
  resources=msgpack.unpackb(raw('CardResourceMaster'))
  linked={row[0] for row in resources if row[2]==resource_id}|{resource_id}
 targets={}
 data=bytearray(raw('CardResourceMaster'));q=reader(data)
 for _ in range(q.read_array_header()):
  count=q.read_array_header();key=next(q)
  if key not in linked:
   for _ in range(count-1):q.skip()
   continue
  for _ in range(25):q.skip()
  offsets=[];values=[]
  for _ in range(12):
   pos=q.tell();value=next(q)
   if data[pos]!=202:raise ValueError('不支持的坐标字段格式')
   offsets.append(pos+1);values.append(value)
  targets[key]=(offsets,values)
  if key==rid and edit is None:return values
  for _ in range(count-38):q.skip()
 if rid not in targets:raise ValueError('缺少随从取景坐标')
 offsets,values=targets[rid]
 if edit is None:return values
 if values!=edit['before']:raise ValueError('取景参数已变更，请重新打开卡牌')
 vals=edit['after']
 if len(vals)!=12 or not all(isinstance(v,(float,int)) and math.isfinite(v) and abs(v)<=10 for v in vals):raise ValueError('无效的取景坐标')
 states=edit.get('states',range(3))
 for offsets,_ in targets.values():
  for state in states:
   if state not in (0,1,2):raise ValueError('无效的战场状态')
   for i in range(state*4,state*4+4):struct.pack_into('>f',data,offsets[i],vals[i])
 ext=msgpack.ExtType(99,b'\xd2'+struct.pack('>I',len(data))+lz4.block.compress(bytes(data),store_size=False))
 block=msgpack.packb(ext);p,l=toc['CardResourceMaster'];delta=len(block)-l
 head=bytearray(b[:start]);u=reader(head)
 for _ in range(u.read_map_header()):
  name=next(u);u.read_array_header();i=u.tell();off=next(u);j=u.tell();length=next(u)
  if head[i]!=210 or head[j]!=210:raise ValueError('不支持的主表目录格式')
  if off>p:struct.pack_into('>i',head,i+1,off+delta)
  if name=='CardResourceMaster':struct.pack_into('>i',head,j+1,len(block))
 return bytes(head)+b[start:start+p]+block+b[start+p+l:]
