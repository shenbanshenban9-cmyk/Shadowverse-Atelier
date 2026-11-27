from pathlib import Path
import sys,sqlite3,json,base64,os,io,struct,uuid,threading,webbrowser,hashlib,shutil,warnings
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('SZB_CRAFT_DATA',str(Path(sys.executable).parent/'影之工坊数据') if getattr(sys,'frozen',False) else str(ROOT/'用户数据')))
DATA.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'lib'))
from banks import replace_media,media_slots as voice_payloads
import UnityPy,wbtool
from PIL import Image
warnings.filterwarnings('ignore');UnityPy.config.FALLBACK_UNITY_VERSION='2022.3.62f1'
P=None
OUT=DATA/'cache'/'empty'
cards=[]
assets={}
deps={}
game_resources={}
game_cards={}
KEY=(ROOT/'candidate.bin').read_bytes()
from functools import lru_cache
import copy
import local_art
import game_data
import foil

def configure_game(value, loaded=None):
 global P,OUT,cards,assets,deps,stages,game_resources,game_cards
 if value.strip():
  state=loaded if loaded is not None else game_data.connect(value,DATA/'cache',KEY)
  P,assets,deps,cards,OUT,game_resources,game_cards=state
 else:
  P=None;assets={};deps={};cards=[];OUT=DATA/'cache'/'empty'
  game_resources={};game_cards={}
 header_art={name.removeprefix('Card/Header/log_') for name in assets if name.startswith('Card/Header/log_')}
 alternate_bases=set()
 def has_face(art):
  return 'Assets/_Wizard2Resources/Card/Textures/'+art in assets or 'Card/Materials/'+art+'_M' in assets
 for art in header_art:
  if len(art)<3 or not art[-2:].isdigit():continue
  suffix=int(art[-2:])
  if suffix<2 or suffix%2:continue
  after=art[:-2]+str(suffix+1).zfill(2)
  if has_face(art) and (art[:-2]+"01" not in header_art or (after in header_art and has_face(after))):alternate_bases.add(art[:-2]+'00')
 for card in cards:card['hasVariants']=card['art'] in alternate_bases
 card_resource_changed.cache_clear()
 material_png.cache_clear()
 variant_faces.cache_clear()
 stages=BattleStages(assets,OUT)

def refresh_card_names(data):
 names=dict(game_data.table(data,'MasterTextLabel'))
 keys={row[0]:row[1] for row in game_data.table(data,'CardText')}
 for entry in cards:
  cid=entry['id'];entry['name']=names.get(keys.get(cid),names.get('CN_'+str(cid),str(cid)))

def material_info(env):
 mat=next(o.read() for o in env.objects if o.type.name=='Material')
 ptr=dict(mat.m_SavedProperties.m_TexEnvs)['_MainTex'].m_Texture
 return mat,ptr

def load_bundle(name):
 path,key=assets[name]
 return UnityPy.load(wbtool.decrypt_bytes(path.read_bytes(),KEY,key))

def load_original_bundle(name):
 path,key=assets[name]
 source=prior_backup(path) or path
 return UnityPy.load(wbtool.decrypt_bytes(source.read_bytes(),KEY,key))

def original_png(name,file):
 env=load_original_bundle(name)
 if name.startswith('Card/Materials/'):
  texture=main_texture(name,env,original=True)
 else:
  texture=next(o.read() for o in env.objects if o.type.name=='Texture2D' and o.read().m_Name==file.stem)
 buffer=io.BytesIO();texture.image.save(buffer,'PNG')
 return buffer.getvalue()

def main_texture(n,env,original=False):
 mat,ptr=material_info(env)
 if ptr.m_FileID==0:return mat.object_reader.assets_file.objects[ptr.m_PathID].read()
 cab=mat.object_reader.assets_file.externals[ptr.m_FileID-1].path.split('/')[-1].lower()
 for dep in deps.get(n,[]):
  for obj in (load_original_bundle(dep) if original else load_bundle(dep)).objects:
   if obj.path_id==ptr.m_PathID and obj.assets_file.name.lower()==cab:return obj.read()
 raise ValueError('未定位到进化材质引用的主贴图')

@lru_cache(maxsize=20)
def material_png(n,stamp):
 t=main_texture(n,load_bundle(n));buf=io.BytesIO();t.image.save(buf,'PNG',compress_level=1)
 return buf.getvalue(),(t.m_Width,t.m_Height)

def preview(n):
 return material_png(n,assets[n][0].stat().st_mtime_ns)

def dimensions(n,f):
 if n.startswith('Card/Materials/'):return preview(n)[1]
 with Image.open(f) as im:return im.size

@lru_cache(maxsize=2048)
def variant_faces(art,evo):
 def available(number):
  return ('Assets/_Wizard2Resources/Card/Textures/'+number in assets or 'Card/Materials/'+number+'_M' in assets)
 result=[dict(label='进化前',art=art,variant=0,state=0)]
 evolved=art[:-2]+'01' if art.endswith('00') else evo
 if evolved and evolved!=art and available(evolved):
  result.append(dict(label='进化后',art=evolved,variant=0,state=1))
 if art.endswith('00'):
  prefix=art[:-2]
  numbers=set()
  for name in assets:
   if name.startswith('Card/Header/log_'+prefix):
    suffix=name.removeprefix('Card/Header/log_'+prefix)
    if len(suffix)==2 and suffix.isdigit():numbers.add(int(suffix))
  for before in sorted(n for n in numbers if n>=2 and n%2==0 and (1 not in numbers or n+1 in numbers)):
   group=before//2
   for state in (0,1):
    number=prefix+str(before+state).zfill(2)
    if before+state in numbers and available(number):
     result.append(dict(label='异画 '+str(group)+'（'+['进化前','进化后'][state]+'）',art=number,variant=group,state=state))
 return result

def variant_resource(card,group):
 face=next((f for f in variant_faces(card['art'],card['evo']) if f['variant']==group and f['state']==0),None)
 if face is None or group<=0:raise ValueError('无效的异画分组')
 return int(face['art'])

def card_fields(data,card,group=0):
 if group:return fields(data,resource_id=variant_resource(card,group))
 return fields(data,card['id'])

def patch_card_fields(data,card,edits):
 if edits.get('base'):data=fields(data,card['id'],edits['base'])
 for group,edit in edits.get('variants',{}).items():
  data=fields(data,edit=edit,resource_id=variant_resource(card,int(group)))
 return data

def describe_slots(card,items):
 faces=variant_faces(card['art'],card['evo'])
 lookup={face['label']:face for face in faces}
 crests={face['art']:face['variant'] for face in faces if face['state']==0}
 descriptors=[]
 for label,name,file in items:
  metadata=dict(label=label,audio=file.suffix=='.wem',size=None if file.suffix=='.wem' else dimensions(name,file))
  base=label.split(' · ')[0]
  if base in lookup:
   face=lookup[base]
   metadata.update(kind='header' if '横幅' in label else 'face',art=face['art'],variant=face['variant'],state=face['state'])
   metadata['foil']=metadata['kind']=='face' and foil.name(face['art']) in assets and assets[foil.name(face['art'])][0].is_file()
  elif name.startswith('Card/IconCrest/'):
   art=name.rsplit('CRT_',1)[1]
   metadata.update(kind='crest',art=art,variant=crests.get(art,0),state=0)
  descriptors.append(metadata)
 return descriptors

def slots(c):
 game_data.prepare_card(sys.modules[__name__],c)
 result=[]
 def add(label,n,img):
  if n in assets and img.is_file():result.append((label,n,img))
 art=c['art']
 variants=[(face['label'],face['art']) for face in variant_faces(art,c['evo'])]
 for label,num in variants:
  source='Assets/_Wizard2Resources/Card/Textures/'+num
  material='Card/Materials/'+num+'_M'
  img=OUT/'卡面'/(num+'.png')
  if source in assets:
   add(label,source,img)
  elif material in assets:
   result.append((label,material,img))
  header='log_'+num
  add(label+' · 预览横幅 / 记牌器小图','Card/Header/'+header,
      OUT/'卡牌横幅与记牌器小图'/(header+'.png'))
 for cid,num in c['links']:
  n='Card/IconCrest/CRT_'+num
  if any(x[1]==n for x in result):continue
  add(('信仰' if (OUT/'信仰图标'/f'{cid}.png').exists() else '纹章')+' '+str(cid),n,OUT/'纹章图标'/'IconCrest'/('CRT_'+num+'.png'))
 for face in variant_faces(art,c['evo']):
  if face['variant']==0 or face['state']!=0:continue
  name='Card/IconCrest/CRT_'+face['art']
  add('异画 '+str(face['variant'])+' · 纹章',name,OUT/'纹章图标'/'IconCrest'/('CRT_'+face['art']+'.png'))
 voice=c['voice'] or 'dx_'+str(c['id'])
 for img in sorted((OUT/'卡牌语音'/'Japanese(JP)'/voice).glob('*.wem')):
  add(c.get('actions',{}).get(str(int(img.stem)),'语音')+' · '+img.stem,'sound/Windows/d/Japanese(JP)/'+voice+'.pck',img)
 return result
def voice_pack(voice):
 name='sound/Windows/d/Japanese(JP)/'+voice+'.pck'
 if name not in assets or not assets[name][0].is_file():raise ValueError('缺少该语音包')
 return assets[name][0].read_bytes()

def variant_voice_face(card,group):
 face=next((f for f in variant_faces(card['art'],card['evo']) if f['variant']==group and f['state']==0),None)
 if face is None:raise ValueError('无效的异画分组')
 return face

def variant_voice_plan(card,group):
 from banks import event_media
 face=variant_voice_face(card,group)
 voice=card['voice'] or 'dx_'+str(card['id'])
 variant_voice='dx_'+face['art']
 if 'sound/Windows/d/Japanese(JP)/'+variant_voice+'.pck' not in assets:raise ValueError('这组异画没有独立语音，只能替换卡图与横幅')
 if 'sound/Windows/d/Japanese(JP)/'+voice+'.pck' not in assets:raise ValueError('这张卡在游戏里没有语音包，只能替换卡图与横幅')
 base=voice_pack(voice);variant=voice_pack(variant_voice)
 resource=game_resources.get(game_cards[card['id']][2])
 if not resource:raise ValueError('缺少该卡牌的资源数据')
 prefix='Play_'+voice+'_'
 suffixes={event[len(prefix):] for j in (39,41,42,43,45,46,47,48,49,50) for event in resource[j].split(',') if event.startswith(prefix)}
 base_map=event_media(base,['Play_'+voice+'_'+suffix for suffix in suffixes])
 variant_map=event_media(variant,['Play_'+variant_voice+'_'+suffix for suffix in suffixes])
 media_of=lambda table,name:table.get(name)
 by_suffix={}
 for suffix in suffixes:
  index=media_of(base_map,'Play_'+voice+'_'+suffix)
  if index is not None:by_suffix.setdefault(int(index),suffix)
 variant_of={}
 for suffix in suffixes:
  index=media_of(variant_map,'Play_'+variant_voice+'_'+suffix)
  if index is not None:variant_of.setdefault(suffix,int(index))
 rows=[]
 for j,(label,name,file) in enumerate(slots(card)):
  if file.suffix!='.wem':continue
  suffix=by_suffix.get(int(file.stem))
  if suffix is None:continue
  media=variant_of.get(suffix)
  if media is None:
   head=suffix.split('_')[0]
   media=variant_of.get('1') if head in ('1','7','8','9','11') else variant_of.get(head)
  if media is None:continue
  rows.append(dict(slot=j,media=media,label=label))
 return dict(voice=voice,variant=variant_voice,slots=rows)

def encode(n,b,items):
 if n.startswith('Scenes/BGb') and n.endswith('_Reduction'):
  from reduced_stage import rebuild
  return rebuild(n,b,items[0][1])
 if n.endswith('.pck'):
  path=assets[n][0];backup=prior_backup(path)
  return replace_media(b,items,backup.read_bytes() if backup else b)
 if len(items)==1 and n.startswith(('Card/Materials/','Assets/_Wizard2Resources/Card/Textures/','Card/Header/','Card/IconCrest/','UI/Emblem/')):
  backup=prior_backup(assets[n][0])
  if backup:
   file,raw=items[0]
   with Image.open(io.BytesIO(raw)) as incoming,Image.open(io.BytesIO(original_png(n,file))) as original:
    if incoming.size==original.size and incoming.convert('RGBA').tobytes()==original.convert('RGBA').tobytes():
     return backup.read_bytes()
 key=assets[n][1];crypt=lambda data:wbtool.decrypt_bytes(data,KEY,key)
 env=UnityPy.load(crypt(b))
 for img,raw in items:
  if n.startswith('Card/Materials/'):
   mat,ptr=material_info(env);t=main_texture(n,env)
   if ptr.m_FileID:
    af=mat.object_reader.assets_file
    template=next((o for o in af.objects.values() if o.type.name=='Texture2D'),None)
    if template is None:raise ValueError('此材质包缺少可用的本地贴图类型')
    cloned=copy.copy(template);cloned.path_id=next(i for i in range(1,len(af.objects)+2) if i not in af.objects)
    af.objects[cloned.path_id]=cloned;t.object_reader=cloned;t.m_Name='Craft_MainTex'
    ptr.m_FileID=0;ptr.m_PathID=cloned.path_id;mat.save()
  else:t=next(o.read() for o in env.objects if o.type.name=='Texture2D' and o.read().m_Name==img.stem)
  im=Image.open(io.BytesIO(raw)).convert('RGBA')
  if im.size!=(t.m_Width,t.m_Height):raise ValueError('裁切尺寸不匹配')
  t.set_image(im,mipmap_count=t.m_MipCount or 1);t.save()
 result=env.file.save(packer='lz4');check=UnityPy.load(result)
 if n.startswith('Card/Materials/'):main_texture(n,check).image.load()
 else:
  for img,_ in items:next(o.read().image.load() for o in check.objects if o.type.name=='Texture2D' and o.read().m_Name==img.stem)
 return crypt(result)
def settings():
 file=DATA/'settings.json'
 return json.loads(file.read_text('utf8')) if file.exists() else {'path':str(DATA/'备份'),'previous':[]}

def set_folder(value, game_path):
 path=Path(value.strip())
 if not path.is_absolute():raise ValueError('请输入绝对目录路径')
 path=path.resolve()
 if P is not None and path.is_relative_to(P.parents[1]):raise ValueError('请选择游戏安装目录以外的位置')
 path.mkdir(parents=True,exist_ok=True)
 cfg=settings()
 from backup_migration import BackupMigration
 migration=BackupMigration(cfg['path'],path)
 old_path=cfg['path']
 cfg['previous']=list(dict.fromkeys([*cfg.get('previous',[]),old_path]))
 cfg['path']=str(path);cfg['gamePath']=game_path
 tmp=DATA/('settings-'+uuid.uuid4().hex+'.tmp')
 try:
  tmp.write_text(json.dumps(cfg,ensure_ascii=False),'utf8')
  os.replace(tmp,DATA/'settings.json')
 except Exception:
  migration.rollback()
  raise
 finally:
  tmp.unlink(missing_ok=True)
 migration.finish()
 return str(path)

def prior_backup(f):
 cfg=settings()
 candidates=[]
 for root in dict.fromkeys([cfg['path'],*cfg['previous'],str(ROOT/'备份')]):
  candidates.extend(x for x in Path(root).glob('*/'+f.name) if x.is_file())
 return min(candidates,key=lambda x:x.stat().st_mtime_ns,default=None)

def current_png(n,f):
 if n.startswith('Card/Materials/'):return preview(n)[0]
 env=load_bundle(n)
 tex=next(o.read() for o in env.objects if o.type.name=='Texture2D' and o.read().m_Name==f.stem)
 stream=io.BytesIO();tex.image.save(stream,'PNG',compress_level=1);return stream.getvalue()

def trim_png(raw):
 try:
  with Image.open(io.BytesIO(raw)) as source:im=source.convert('RGBA')
 except Exception:return raw
 width,height=im.size
 px=im.load()
 def blank(y):
  for x in range(width):
   r,g,b,a=px[x,y]
   if a>16 and not (r>238 and g>238 and b>238):return False
  return True
 top=0
 while top<height-1 and blank(top):top+=1
 bottom=height
 while bottom-1>top and blank(bottom-1):bottom-=1
 if (top,bottom)==(0,height):return raw
 if bottom-top<8:return raw
 stream=io.BytesIO();im.crop((0,top,width,bottom)).save(stream,'PNG',compress_level=1);return stream.getvalue()
from battle_field import MASTER,fields
from battle_stages import BattleStages
stages=BattleStages(assets,OUT)
@lru_cache(maxsize=4096)
def card_resource_changed(name,path,backup,stamp,cid,art,evo):
 current=path.read_bytes()
 original=backup.read_bytes()
 if current==original:return False
 if name==MASTER:
  if cid==10734120 and game_data.summon_name(original)!=game_data.summon_name(current):return True
  if game_data.card_name(original,cid)!=game_data.card_name(current,cid):return True
  if not evo:return False
  if fields(original,cid)!=fields(current,cid):return True
  for face in variant_faces(art,evo):
   if face['variant'] and not face['state']:
    resource=int(face['art'])
    if fields(original,resource_id=resource)!=fields(current,resource_id=resource):return True
  return False
 if any(name==foil.name(face['art']) for face in variant_faces(art,evo)):return True
 if name.endswith('.pck'):return voice_payloads(original)!=voice_payloads(current)
 if not name.startswith(('Card/Materials/','Assets/_Wizard2Resources/Card/Textures/','Card/Header/','Card/IconCrest/','UI/Emblem/')):return False
 def texture_from(data,original):
  env=UnityPy.load(wbtool.decrypt_bytes(data,KEY,assets[name][1]))
  if name.startswith('Card/Materials/'):return main_texture(name,env,original=original)
  stem=name.rsplit('/',1)[-1]
  return next(o.read() for o in env.objects if o.type.name=='Texture2D' and o.read().m_Name==stem)
 texture=texture_from(original,True)
 before=texture.image.convert('RGBA')
 after=texture_from(current,False).image.convert('RGBA')
 if before.size!=after.size:return True
 if before.tobytes()==after.tobytes():return False
 # Older versions restored PNGs by passing them through the texture compressor again.
 texture.set_image(before,mipmap_count=texture.m_MipCount or 1)
 return texture.image.convert('RGBA').tobytes()!=after.tobytes()

def stage_recipe(stage,layer):
 layers=stages.layers(stage,include_scene=layer>=2)
 if not 0<=layer<len(layers):raise ValueError('无效的背景图层')
 return DATA/'背景编辑数据'/str(stage)/(str(layer)+'.json'),assets[f'Scenes/BGb{stage:05}_Reduction'][0] if layer==0 else assets[layers[layer][0]][0]

def stage_originals(stage):
 if stage not in stages.ids:raise ValueError('未知场地')
 field=f'Field{stage:02}'
 names={f'Battle/Textures/{quality}/{field}' for quality in ('FieldLow','FieldSuperLow')}
 names.add(f'Assets/_Wizard2Resources/3d/Env/Battle/BGb{stage:05}')
 names.update(f'Scenes/BGb{stage:05}{suffix}' for suffix in ('_Reduction','_FullBake_Reduction'))
 cfg=settings();found={}
 for n in names:
  if n not in assets:continue
  file=assets[n][0]
  copies=[Path(root)/('战场-'+str(stage))/file.name for root in dict.fromkeys([cfg['path'],*cfg['previous'],str(ROOT/'备份')])]
  original=min((f for f in copies if f.is_file()),key=lambda f:f.stat().st_mtime_ns,default=None)
  if original:found[n]=original
 if not found:raise ValueError('此场地没有可用的原始备份')
 return found
TOKEN=uuid.uuid4().hex
lock=threading.RLock()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,data,kind):
  self.send_response(200);self.send_header('Cache-Control','no-store');self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def route(self):
  path=urlsplit(self.path)
  if not path.path.startswith('/'+TOKEN+'/'):raise ValueError('无效请求')
  return path.path.split('/')[-1],parse_qs(path.query)
 def do_GET(self):
  try:
   op,q=self.route()
   if not op:return self.send((ROOT/'index.html').read_bytes(),'text/html;charset=utf-8')
   hero_files={'card-cover': 'image/png', 'tile1.webp': 'image/webp', 'hero.png': 'image/png', 'hero.atlas': 'text/plain', 'class_1223.skel': 'application/octet-stream', 'spine-player.min.js': 'text/javascript', 'spine-player.min.css': 'text/css'}
   if op in hero_files:
    if op.startswith('spine-player'):
     source=ROOT/'hero1223'/op
    elif op=='card-cover':
     try:cover,ok=local_art.home_cover(OUT)
     except FileNotFoundError:
      tried=False
      for bundle in ('Assets/_Wizard2Resources/Card/Textures/'+local_art.COVER_EVOLVED,'Assets/_Wizard2Resources/Card/Textures/'+local_art.COVER,'Prefabs/LeaderSkin/class_1223'):
       env=local_art.decode_bundle(assets,KEY,bundle) if bundle in assets else None
       if env is None:continue
       texture=next((o.read() for o in env.objects if o.type.name=='Texture2D' and (bundle.endswith('class_1223') or o.read().m_Name==bundle.rsplit('/',1)[-1])),None)
       if texture is None:continue
       face=local_art._face_from_texture(texture)
       local_art.write_cover(OUT,face,bundle!=local_art.HERO)
       cover,ok=local_art.home_cover(OUT);tried=True
       break
      if not tried:raise ValueError('游戏资源里没有封面立绘')
     stream=io.BytesIO();cover.convert('RGB').save(stream,'PNG',compress_level=1);return self.send(stream.getvalue(),'image/png')
    else:
     if op=='tile1.webp':
      bundle=next(n for n in assets if n.startswith('UI/BattleStage/utx_pict_stage_') and assets[n][0].is_file())
     else:bundle=local_art.HERO
     source=local_art.image_file(OUT,assets,KEY,op,bundle)
    return self.send(source.read_bytes(),hero_files[op])
   if op=='wall-layout':return self.send(json.dumps(settings().get('wallLayout',dict(x=50,y=50,transparency=90))).encode(),'application/json')
   if op=='wallpaper':
    value=settings().get('wallpaper')
    if value:return self.send(base64.b64decode(value),'image/jpeg')
    return self.send(bytes.fromhex('47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b'),'image/gif')
   if op=='settings':return self.send(json.dumps(dict(path=settings()['path'],gamePath=settings().get('gamePath',''),connected=P is not None)).encode(),'application/json')
   if op=='app-status':return self.send(json.dumps(dict(connected=P is not None,error=STARTUP_ERROR)).encode(),'application/json')
   if op=='modified-cards':
    cfg=settings();known={str(c['id']):c['id'] for c in cards};backups={}
    for root in dict.fromkeys([cfg['path'],*cfg['previous'],str(ROOT/'备份')]):
     for folder in Path(root).glob('*'):
      if folder.is_dir() and folder.name in known:
       for backup in folder.iterdir():
        if backup.is_file() and not backup.name.endswith('.tmp'):
         key=(folder.name,backup.name)
         if key not in backups or backup.stat().st_mtime_ns<backups[key].stat().st_mtime_ns:backups[key]=backup
    paths={path.name:(name,path) for name,(path,key) in assets.items()};modified=set()
    card_map={str(card['id']):card for card in cards}
    for (cid,filename),backup in backups.items():
     if cid in modified or filename not in paths:continue
     name,path=paths[filename]
     if not path.is_file():continue
     card=card_map[cid]
     stamp=(path.stat().st_mtime_ns,backup.stat().st_mtime_ns)
     if card_resource_changed(name,path,backup,stamp,card['id'],card['art'],card['evo']):
      modified.add(cid)
    return self.send(json.dumps(sorted(modified)).encode(),'application/json')
   if op=='cards':return self.send(json.dumps(cards).encode(),'application/json')
   if op=='foil.js':return self.send((ROOT/'foil.js').read_bytes(),'text/javascript;charset=utf-8')
   if op=='voice.js':return self.send((ROOT/'voice.js').read_bytes(),'text/javascript;charset=utf-8')
   if op=='field.js':return self.send((ROOT/'field.js').read_bytes(),'text/javascript;charset=utf-8')
   if op=='fieldpic':
    import re
    name=q['file'][0]
    if not re.fullmatch(r'(mask_)?frame2d_field_follower_(evol_|super_evol_)?(bronze|silver|gold|legend)\.png',name):raise ValueError()
    return self.send(local_art.image_file(OUT,assets,KEY,name,'Card/Frame/000/TexturesLow/'+name.removeprefix('mask_')[:-4]).read_bytes(),'image/png')
   if op=='background.js':return self.send((ROOT/'background.js').read_bytes(),'text/javascript;charset=utf-8')
   if op=='stage-recipe':
    path,target=stage_recipe(int(q['stage'][0]),int(q['layer'][0]))
    recipe=json.loads(path.read_text('utf8')) if path.is_file() else {}
    if recipe.get('stamp')!=str(target.stat().st_mtime_ns) and not (recipe.get('sceneHash') and recipe['sceneHash']==hashlib.sha256(target.read_bytes()).hexdigest()):recipe={}
    return self.send(json.dumps(recipe).encode(),'application/json')
   if op=='stage-capabilities':return self.send(b'{"restore":true}','application/json')
   if op=='stages':return self.send(json.dumps(stages.ids).encode(),'application/json')
   if op in ('stage-cover','stage-layers','stage-image'):
    stage=int(q['stage'][0])
    if op=='stage-cover':return self.send(stages.cover(stage),'image/png')
    detailed=q.get('detail',['0'])[0]=='1' if op=='stage-layers' else int(q['layer'][0])>=2
    layers=stages.layers(stage,include_scene=detailed)
    if op=='stage-layers':return self.send(json.dumps([f.stem for n,f in layers]).encode(),'application/json')
    n,f=layers[int(q['layer'][0])]
    if int(q['layer'][0])>=2:game_data.export_texture(sys.modules[__name__],n,f.parent,texture_name=f.stem)
    return self.send(current_png(n,f),'image/png')
   if P is None:raise ValueError('请先在设置中填写游戏路径')
   c=cards[int(q['i'][0])]
   if op=='variant-voice':return self.send(json.dumps(variant_voice_plan(c,int(q['group'][0]))).encode(),'application/json')
   if op=='variant-audio':
    from banks import clip_media
    plan=variant_voice_plan(c,int(q['group'][0]))
    slot=int(q['slot'][0])
    entry=next((row for row in plan['slots'] if row['slot']==slot),None)
    if entry is None:raise ValueError('这组异画没有对应的语音')
    payload=clip_media(voice_pack(plan['variant'])).get(int(entry['media']))
    if payload is None:raise ValueError('异画语音读取失败')
    return self.send(payload,'application/octet-stream')
   if op=='foil-preview':
    art=q['art'][0];action=q['action'][0];valid={v['art'] for v in variant_faces(c['art'],c['evo'])}
    if art not in valid or action not in valid|foil.PRESETS.keys()|{'current','original'}:raise ValueError('无效的闪卡资源')
    return self.send(json.dumps(foil.preview(sys.modules[__name__],art,action)).encode(),'application/json')
   if op=='field-default':
    source=prior_backup(assets[MASTER][0]) or OUT/'master-original.bytes'
    return self.send(json.dumps(card_fields(source.read_bytes(),c,int(q.get('variant',['0'])[0]))).encode(),'application/json')
   if op=='field':
    return self.send(json.dumps(card_fields(assets[MASTER][0].read_bytes(),c,int(q.get('variant',['0'])[0]))).encode(),'application/json')
   if op=='variant-field':
    data=assets[MASTER][0].read_bytes()
    group=int(q['variant'][0])
    return self.send(json.dumps(dict(before=card_fields(data,c),after=card_fields(data,c,group))).encode(),'application/json')
   if op=='art':
    game_data.export_texture(sys.modules[__name__],'Card/Header/log_'+c['art'],OUT/'卡牌横幅与记牌器小图')
    f=OUT/'卡牌横幅与记牌器小图'/('log_'+c['art']+'.png')
    if not f.exists():f=OUT/'卡面'/(c['art']+'.png')
    name='Card/Header/'+f.stem if f.stem.startswith('log_') else 'Assets/_Wizard2Resources/Card/Textures/'+f.stem
    return self.send(trim_png(current_png(name,f) if name in assets else f.read_bytes()),'image/png')
   s=slots(c)
   if op=='alignment':
    from header_render import header_transform
    label,_,header=s[int(q['s'][0])]
    base_label=label.split(' · ')[0]
    if '横幅' not in label:raise ValueError('请选择横幅')
    _,n,f=next(item for item in s if item[0]==base_label)
    source=io.BytesIO(preview(n)[0]) if n.startswith('Card/Materials/') else f
    with Image.open(source) as original,Image.open(header) as mask:
     return self.send(json.dumps(header_transform(original,mask).tolist()).encode(),'application/json')
   if op=='slots':return self.send(json.dumps(describe_slots(c,s)).encode(),'application/json')
   if op=='voice-original':
    _,name,f=s[int(q['s'][0])]
    if f.suffix!='.wem':raise ValueError('无效语音槽')
    from banks import media_key,media_slots
    path=assets[name][0];original=(prior_backup(path) or path).read_bytes()
    return self.send(media_slots(original)[media_key(f,original)],'application/octet-stream')
   if op=='listen':
    import tempfile,subprocess
    _,name,f=s[int(q['s'][0])]
    from banks import media_slots,media_key
    path=assets[name][0];backup=prior_backup(path)
    original=(backup or path).read_bytes();current=path.read_bytes()
    payload=media_slots(current)[media_key(f,original,current)]
    with tempfile.TemporaryDirectory() as d:
     wav=Path(d)/'sample.wav'
     f=Path(d)/'current.wem';f.write_bytes(payload)
     subprocess.run([str(ROOT/'audio'/'vgmstream-cli.exe'),'-o',str(wav),str(f)],check=True,stdout=subprocess.DEVNULL,creationflags=0x08000000)
     return self.send(wav.read_bytes(),'audio/wav')
   if op=='current':
    _,n,f=s[int(q['s'][0])];return self.send(current_png(n,f),'image/png')
   if op=='image':
    _,name,file=s[int(q['s'][0])]
    return self.send(original_png(name,file),'image/png')
   raise ValueError()
  except Exception as error:
   import traceback
   with (DATA/'resource-errors.log').open('a',encoding='utf8') as log:
    log.write(self.path+'\n'+traceback.format_exc()+'\n')
   self.send_response(400);self.send_header('Content-Type','application/json;charset=utf-8');self.end_headers()
   self.wfile.write(json.dumps({'error':str(error)},ensure_ascii=False).encode('utf8'))
 def do_POST(self):
  try:
   op,q=self.route()
   if op not in ('apply','header','settings','stage-apply','stage-restore','audio-convert','audio-preview','foil-native','card-rename'):raise ValueError('当前后台不支持此操作，请从桌面重新打开影之工坊')
   if self.headers.get('Origin')!='http://127.0.0.1:'+str(self.server.server_port):raise ValueError('请求来源错误')
   length=int(self.headers['Content-Length'])
   if length>100_000_000:raise ValueError('单批文件过大')
   req=json.loads(self.rfile.read(length))
   if op=='card-rename':
    if P is None:raise ValueError('请先设置游戏目录')
    card=cards[int(req['i'])]
    target=assets[MASTER][0];data=target.read_bytes()
    base=prior_backup(target)
    original_name=game_data.card_name(base.read_bytes(),card['id']) if base else None
    restore=req.get('restore') is True
    title=(original_name or game_data.card_name(data,card['id'])) if restore else req['name']
    patched=game_data.rename_card(data,card['id'],title,original_name)
    if card['id']==10734120:
     patched=game_data.rename_summon(patched,game_data.summon_name(base.read_bytes() if base else data) if restore else title)
    backup=Path(settings()['path'])/str(card['id'])/target.name
    backup.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
     if not backup.exists():
      base=prior_backup(target)
      backup.write_bytes(base.read_bytes() if base else data)
     tmp.write_bytes(patched)
     if target.read_bytes()!=data:raise ValueError('游戏资源已变化，请重试')
     os.replace(tmp,target)
    finally:tmp.unlink(missing_ok=True)
    refresh_card_names(patched)
    return self.send(json.dumps(card['name'],ensure_ascii=False).encode(),'application/json;charset=utf-8')
   if op=='foil-native':
    c=cards[int(req['i'])]
    art,action=req['art'],req['action']
    valid={v['art'] for v in variant_faces(c['art'],c['evo'])}
    if art not in valid or action not in valid|foil.PRESETS.keys()|{'current','original'}:
     raise ValueError('无效的闪卡资源')
    label,name,file=slots(c)[int(req['slot'])]
    if art not in {v['art'] for v in variant_faces(c['art'],c['evo']) if v['label']==label}:
     raise ValueError('卡面与闪卡资源不匹配')
    raw=base64.b64decode(req['image'],validate=True) if req.get('image') else current_png(name,file)
    with Image.open(io.BytesIO(raw)) as picture:
     return self.send(foil.native_preview(sys.modules[__name__],art,action,picture),'image/webp')
   if op=='audio-preview':
    import tempfile,subprocess
    with tempfile.TemporaryDirectory() as folder:
     source=Path(folder)/'preview.wem';target=Path(folder)/'preview.wav'
     source.write_bytes(base64.b64decode(req['audio'],validate=True))
     subprocess.run([str(ROOT/'audio'/'vgmstream-cli.exe'),'-o',str(target),str(source)],check=True,capture_output=True,timeout=30,creationflags=0x08000000)
     return self.send(target.read_bytes(),'audio/wav')
   if op=='audio-convert':
    from audio_encoder import convert
    label,name,source=slots(cards[int(req['i'])])[int(req['slot'])]
    if source.suffix!='.wem':raise ValueError('无效语音槽')
    converted=convert(base64.b64decode(req['audio'],validate=True),source.read_bytes())
    return self.send(json.dumps({'wem':base64.b64encode(converted).decode()}).encode(),'application/json')
   if op=='settings':
    with lock:
     game_path=req.get('gamePath','').strip()
     loaded=None
     if game_path:
      new_p=game_data.find_persistent(game_path)
      if Path(req['path']).resolve().is_relative_to(new_p.parents[1]):raise ValueError('请选择游戏目录之外的备份路径')
      loaded=game_data.connect(game_path,DATA/'cache',KEY)
     folder=set_folder(req['path'],game_path)
     configure_game(game_path,loaded)
     if P is not None:
      original=prior_backup(assets[MASTER][0])
      if original:(OUT/'master-original.bytes').write_bytes(original.read_bytes())

    return self.send(folder.encode(),'text/plain;charset=utf-8')
   if op=='stage-restore':
    stage=int(req['stage']);groups=stage_originals(stage);backup_id='战场-'+str(stage)
   elif op=='stage-apply':
    stage=int(req['stage']);groups=stages.groups(stage,int(req['layer']),base64.b64decode(req['image'],validate=True),req.get('recipe'))
    backup_id='战场-'+str(stage)
   else:
    s=slots(cards[req['i']]);groups={};backup_id=str(cards[req['i']]['id'])
   if op=='header':
    from header_render import make_header
    j=int(req['slot']);label,n,f=s[j]
    if label not in {face['label'] for face in variant_faces(cards[req['i']]['art'],cards[req['i']]['evo'])}:raise ValueError('无效的卡面')
    header=next(item[2] for item in s if item[0]==label+' · 预览横幅 / 记牌器小图')
    source=io.BytesIO(preview(n)[0]) if n.startswith('Card/Materials/') else f
    with Image.open(source) as original,Image.open(header) as mask,Image.open(io.BytesIO(base64.b64decode(req['image'],validate=True))) as replacement:
     return self.send(make_header(original,mask,replacement),'image/png')
   for k,v in ({} if op.startswith('stage-') else req['changes']).items():
    _,n,f=s[int(k)];groups.setdefault(n,[]).append((f,base64.b64decode(v,validate=True)))
   if op=='apply':
    for name,items in list(groups.items()):
     if not name.startswith('Card/IconCrest/CRT_'):continue
     emblem='utx_tex_em_'+name.rsplit('CRT_',1)[1]
     target='UI/Emblem/'+emblem
     if target not in assets or not assets[target][0].is_file():continue
     file,raw=items[-1]
     icon=Path(emblem+'.png')
     original=original_png(target,icon)
     if raw==original_png(name,file):
      converted=original
     else:
      with Image.open(io.BytesIO(original)) as template,Image.open(io.BytesIO(raw)) as art:
       output=io.BytesIO()
       art.convert('RGBA').resize(template.size,Image.Resampling.LANCZOS).save(output,'PNG')
       converted=output.getvalue()
     groups[target]=[(icon,converted)]
   if op=='apply' and req.get('foils'):
    card=cards[req['i']];valid={v['art'] for v in variant_faces(card['art'],card['evo'])}
    for art,action in req['foils'].items():
     if art not in valid or action not in valid|foil.PRESETS.keys()|{'original'}:raise ValueError('无效的闪卡替换')
     groups[foil.name(art)]=foil.replacement(sys.modules[__name__],art,action)
   if op=='apply' and (req.get('field') or req.get('variantFields')) and cards[req['i']]['typ']==1:groups[MASTER]=dict(base=req.get('field'),variants=req.get('variantFields',{}))
   with lock:
    batch=Path(settings()['path'])/backup_id;batch.mkdir(parents=True,exist_ok=True)
    staged=[];written=[]
    try:
     for n,items in groups.items():
      f,e=assets[n];b=f.read_bytes();patched=items if isinstance(items,bytes) else items.read_bytes() if op=='stage-restore' else patch_card_fields(b,cards[req['i']],items) if n==MASTER else encode(n,b,items);backup=batch/f.name;tmp=f.with_name(f.name+'.'+uuid.uuid4().hex+'.tmp')
      if op!='stage-restore' and not backup.exists():
       prior=prior_backup(f);original=prior.read_bytes() if prior else b
       backup_tmp=batch/(uuid.uuid4().hex+'.tmp')
       try:
        backup_tmp.write_bytes(original);os.replace(backup_tmp,backup)
       finally:backup_tmp.unlink(missing_ok=True)
      staged.append((f,tmp,b));tmp.write_bytes(patched)
     for f,tmp,backup in staged:
      if f.read_bytes()!=backup:raise ValueError('资源已由其他程序更改')
      os.replace(tmp,f);written.append((f,backup))
     if op=='stage-restore':
      for recipe in (DATA/'背景编辑数据'/str(stage)).glob('*.json'):
       recipe.unlink(missing_ok=True)
     if op=='stage-apply' and 'recipe' in req:
      path,target=stage_recipe(int(req['stage']),int(req['layer']))
      recipe=req['recipe'];recipe['stamp']=str(target.stat().st_mtime_ns)
      path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp')
      try:
       temp.write_text(json.dumps(recipe),'utf8');os.replace(temp,path)
      finally:temp.unlink(missing_ok=True)
    except Exception:
     for f,backup in reversed(written):f.write_bytes(backup)
     raise
    finally:
     for _,tmp,_ in staged:tmp.unlink(missing_ok=True)
   self.send(str(batch).encode(),'text/plain;charset=utf-8')
  except Exception as ex:self.send_response(400);self.end_headers();self.wfile.write(str(ex).encode())
STARTUP_ERROR=''
try:
 configure_game(settings().get('gamePath',''))
except Exception as error:
 configure_game('')
 STARTUP_ERROR=str(error)

# Keep client selection and resource reads/writes in the same session.
def synchronized(method):
 def run(self):
  with lock:return method(self)
 return run
Handler.do_GET=synchronized(Handler.do_GET)
Handler.do_POST=synchronized(Handler.do_POST)
