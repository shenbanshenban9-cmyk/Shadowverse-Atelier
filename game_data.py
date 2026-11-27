"""Discover a supported client and prepare original resources on demand."""
from pathlib import Path
import sqlite3, hashlib, io, os, struct, threading, json, re
from pip._vendor import msgpack
import lz4.block
import UnityPy
import wbtool
from banks import labels, ordered_media
from functools import lru_cache

resource_lock = threading.RLock()

def find_persistent(value):
    root = Path(value.strip().strip('"')).expanduser()
    if root.is_file():
        root = root.parent
    candidates = [root, root / 'Persistent']
    candidates += list(root.glob('*_Data/Persistent'))
    for candidate in candidates:
        if (candidate / 'meta').is_file() and (candidate / 'dat').is_dir():
            return candidate.resolve()
    raise ValueError('未找到 Persistent/meta 和 dat，请选择游戏安装文件夹或游戏 EXE')

def table(data, name):
    stream = msgpack.Unpacker(raw=False, strict_map_key=False)
    stream.feed(data)
    toc = next(stream)
    start = stream.tell()
    offset, length = toc[name]
    block = msgpack.unpackb(data[start+offset:start+offset+length])
    raw = lz4.block.decompress(block.data[5:], int.from_bytes(block.data[1:5], 'big'))
    return msgpack.unpackb(raw, raw=False, strict_map_key=False)

def connect(value, cache, key):
    persistent = find_persistent(value)
    with sqlite3.connect((persistent/'meta').as_uri()+'?mode=ro&immutable=1', uri=True) as db:
        assets = {n:(persistent/'dat'/h[:2]/h,e) for n,h,e in db.execute('select n,h,e from a')}
        deps = {}
        for mat,tex in db.execute("select a.n,b.n from r join a on a.i=r.f join a b on b.i=r.t where a.n like 'Card/Materials/%' and b.n like '%Card/Textures/%'"):
            deps.setdefault(mat,[]).append(tex)
    master = assets['Master/mastermemory.bytes'][0].read_bytes()
    names = dict(table(master,'MasterTextLabel'))
    texts = {r[0]:r[1] for r in table(master,'CardText')}
    cards = {r[0]:r for r in table(master,'CardMaster')}
    resources = {r[0]:r for r in table(master,'CardResourceMaster')}
    result=[]
    def rid(cid):
        card=cards.get(cid)
        resource=resources.get(card[2]) if card else None
        return str(resource[1]) if resource else ''
    for row in table(master,'BaseCardMaster'):
        cid,typ,job,traits,cost,atk,hp,unused,rar,*rest=row
        card=cards.get(cid)
        if card is None:continue
        resource=resources.get(card[2])
        result.append(dict(id=cid,pack=names.get('CPN_'+str(card[3]),'卡组编号 '+str(card[3])),packId=card[3],
          name=names.get(texts.get(cid,''),names.get('CN_'+str(cid),str(cid))),typ=typ,job=job,cost=cost,rar=rar,
          art=rid(cid),evo=rid(card[7]),links=[(j,rid(j)) for j in set(rest[0]+rest[1]+[cid]) if j in cards],
          voice=resource[38] if resource else '',actions={}))
    # Verify encryption compatibility before accepting a client.
    sample=next((n for n in assets if n.startswith('Card/Header/') and assets[n][0].is_file()),None)
    if sample:
        path,seed=assets[sample]
        UnityPy.load(wbtool.decrypt_bytes(path.read_bytes(),key,seed))
    target=cache/hashlib.sha256(str(persistent).encode()).hexdigest()[:16]
    target.mkdir(parents=True,exist_ok=True)
    original=target/'master-original.bytes'
    if not original.exists():original.write_bytes(master)
    return persistent,assets,deps,result,target,resources,cards

def export_texture(workshop,name,folder,texture_name=None):
    if name not in workshop.assets:return
    path,seed=workshop.assets[name]
    if not path.is_file():return
    with resource_lock:
        marker=folder/('.'+hashlib.sha256((name+('#'+texture_name if texture_name else '')).encode()).hexdigest()+'.done')
        if marker.exists():return
        folder.mkdir(parents=True,exist_ok=True)
        env=workshop.load_bundle(name)
        for obj in env.objects:
            if obj.type.name!='Texture2D':continue
            texture=obj.read()
            safe=Path(texture.m_Name).name
            if texture_name is not None and safe!=texture_name:continue
            image=folder/(safe+'.png')
            if not image.exists():
                tmp=image.with_suffix('.tmp')
                texture.image.save(tmp,'PNG')
                os.replace(tmp,image)
        marker.touch()

def card_titles(w):
    return {str(c['id']):c['name'] for c in w.cards}

WORDS={'sky':'天空','super_sky':'超级天空'}

def target_title(names,cid):
    for cut in (0,1,2):
        key=cid[:-cut] if cut else cid
        if key in names:return names[key],cut==0
    return cid,False

def named(names,cid):
    name,exact=target_title(names,cid)
    return name if exact else name+'（'+cid+'）'

def voice_label(event,voice,title,names):
    prefix='Play_'+voice+'_'
    if not event.startswith(prefix):return title
    parts=[part for part in event[len(prefix):].split('_') if part]
    if len(parts)<2:return title
    if parts[0] in ('7','8','9','11'):
        ids=[part for part in parts[1:] if part.isdigit()]
        return title+' · 联动 '+' 与 '.join(named(names,cid) for cid in ids) if ids else title
    parts=parts[1:]
    if 'sp' in parts:title='超进化时'
    desc=[];i=0
    while i<len(parts):
        part=parts[i]
        if part.startswith('enh'):desc.append('增强'+((' '+part[3:]) if part[3:] else ''));i+=1
        elif part.startswith('mode'):desc.append('模式 '+part[4:]);i+=1
        elif part in ('sp','evo','token'):i+=1
        elif part=='skill' and i+1<len(parts):desc.append('联动 '+named(names,parts[i+1]));i+=2
        elif part=='lottery' and i+1<len(parts):desc.append('抽选 '+parts[i+1]);i+=2
        elif part=='super' and i+1<len(parts) and 'super_'+parts[i+1] in WORDS:desc.append(WORDS['super_'+parts[i+1]]);i+=2
        elif part in WORDS:desc.append(WORDS[part]);i+=1
        else:desc.append(part);i+=1
    return title+''.join(' · '+item for item in desc)

def prepare_card(w,c):
    faces=w.variant_faces(c['art'],c['evo'])
    variants=[face['art'] for face in faces]
    for art in set(variants):
        if not art:continue
        name='Assets/_Wizard2Resources/Card/Textures/'+art
        if name in w.assets:export_texture(w,name,w.OUT/'卡面')
        elif 'Card/Materials/'+art+'_M' in w.assets:
            material='Card/Materials/'+art+'_M'
            image=w.OUT/'卡面'/(art+'.png')
            if not image.exists():
                image.parent.mkdir(parents=True,exist_ok=True)
                texture=w.main_texture(material,w.load_bundle(material))
                texture.image.save(image,'PNG')
        export_texture(w,'Card/Header/log_'+art,w.OUT/'卡牌横幅与记牌器小图')
    crests={art for cid,art in c['links']}|{face['art'] for face in faces if face['state']==0}
    for art in crests:
        export_texture(w,'Card/IconCrest/CRT_'+art,w.OUT/'纹章图标'/'IconCrest')
    voice=c['voice'] or 'dx_'+str(c['id'])
    name='sound/Windows/d/Japanese(JP)/'+voice+'.pck'
    if name in w.assets and w.assets[name][0].is_file():
        with resource_lock:
            folder=w.OUT/'卡牌语音'/'Japanese(JP)'/voice
            folder.mkdir(parents=True,exist_ok=True)
            source=w.assets[name][0]
            original=w.prior_backup(source) or source
            data=original.read_bytes()
            media=ordered_media(data)
            if not media:raise ValueError('无法读取语音包的媒体索引')
            for index,(key,payload) in enumerate(media,1):
                if payload[:4]!=b'RIFF' or payload[8:12]!=b'WAVE':
                    raise ValueError('语音包包含不支持的媒体格式')
                file=folder/(str(index).zfill(3)+'.wem')
                if not file.exists() or file.read_bytes()!=payload:
                    temp=file.with_suffix('.tmp');temp.write_bytes(payload);os.replace(temp,file)
                metadata=file.with_suffix('.media.json')
                value=json.dumps(dict(bank=key[0],media=key[1]))
                if not metadata.exists() or metadata.read_text('utf8')!=value:
                    temp=metadata.with_suffix('.tmp');temp.write_text(value,'utf8');os.replace(temp,metadata)
            resource=w.game_resources.get(w.game_cards[c['id']][2])
            if resource:
                names=card_titles(w)
                events={}
                for j,title in [(39,'出牌时'),(41,'攻击时'),(42,'进化后攻击时'),(43,'进化时'),(45,'被破坏时'),(46,'进化后被破坏时'),(47,'衍生卡被破坏时'),(48,'技能发动时'),(49,'进化后技能发动时'),(50,'行动时')]:
                    for event in resource[j].split(','):
                        if not event:continue
                        events[event]=voice_label(event,voice,title,names)
                c['actions']=labels(data,events)

def prepare_stage(w,stage,include_scene=False):
    field=f'Field{stage:02}';scene=f'BGb{stage:05}';root=w.OUT/'战场背景'
    for quality in ('FieldLow','FieldSuperLow'):
        export_texture(w,f'Battle/Textures/{quality}/{field}',root/quality/field)
    export_texture(w,f'UI/BattleStage/utx_pict_stage_{stage}',root/'BattleStage'/f'utx_pict_stage_{stage}')


@lru_cache(maxsize=24)
def texture_names(path,seed,key,stamp):
    env=UnityPy.load(wbtool.decrypt_bytes(Path(path).read_bytes(),key,seed))
    return tuple(sorted(Path(obj.read().m_Name).name for obj in env.objects if obj.type.name=='Texture2D'))


def card_name_key(data,cid):
    rows=dict((row[0],row[1]) for row in table(data,'CardText'))
    labels=dict(table(data,'MasterTextLabel'))
    key=rows.get(cid)
    return key if key in labels else 'CN_'+str(cid)


def card_name(data,cid):
    return dict(table(data,'MasterTextLabel')).get(card_name_key(data,cid),str(cid))


def rename_card(data,cid,title,original_title=None):
    if not isinstance(title,str) or not title.strip() or len(title)>60 or any(ch in '<>『』' or ord(ch)<32 for ch in title):
        raise ValueError('请输入 1–60 个字符的单行卡名')
    title=title.strip()
    key=card_name_key(data,cid)
    rows=table(data,'MasterTextLabel')
    row=next((row for row in rows if row[0]==key),None)
    if row is None:
        raise ValueError('找不到游戏中的卡名记录')
    old=row[1]
    aliases={name for name in (old,original_title) if name and name!=title}
    if not aliases:
        return data
    labels=dict(rows)
    base_cards=table(data,'BaseCardMaster')
    card_rows={r[0]:r for r in table(data,'CardMaster')}
    text_rows={r[0]:r for r in table(data,'CardText')}

    def text_for(card):
        return text_rows.get(card_rows[card][4],()) if card in card_rows else ()

    owners={}
    for base in base_cards:
        linked=set().union(*base[9:12])
        if cid not in linked:
            continue
        other_names={labels.get(text[1]) for other in linked-{cid} if (text:=text_for(other))}
        unambiguous=aliases-other_names
        if not unambiguous:
            continue
        if title in other_names:
            raise ValueError('部分效果说明同时引用了这个名字，请使用不同的名称')
        owners[base[0]]=unambiguous
    references={text[2]:names for owner,names in owners.items() if (text:=text_for(owner))}
    references.update(('SSL_'+str(skill[0]),owners[skill[4]]) for skill in table(data,'SkillMaster') if skill[4] in owners)
    for entry in rows:
        if entry[0] in references:
            alternatives='|'.join(re.escape(name) for name in sorted(references[entry[0]]))
            pattern=re.compile(r'(『(?:<color=[^>]+>)?)(?:'+alternatives+r')((?:</color>)?』)')
            entry[1]=pattern.sub(lambda match:match[1]+title+match[2],entry[1])
    row[1]=title
    return write_text_labels(data,rows)


def summon_name(data):
    return dict(table(data,'MasterTextLabel'))['ENT_107341200']


def rename_summon(data,title):
    if not isinstance(title,str) or not title.strip() or len(title)>60 or any(ch in title for ch in '<>『』') or any(ord(ch)<32 for ch in title):
        raise ValueError('请输入有效的单行召唤物名称')
    rows=table(data,'MasterTextLabel')
    old=next(row[1] for row in rows if row[0]=='ENT_107341200')
    title=title.strip()
    for row in rows:
        if row[0] in ('ENT_107341200','ENT_107341250'):
            row[1]=title
        elif row[0] in ('SD_10734120_01','SD_86534930_01','SSL_107341205','SSL_865349302','CN_86534930'):
            row[1]=row[1].replace(old,title)
    return write_text_labels(data,rows)


def write_text_labels(data,rows):
    raw=msgpack.packb(rows,use_bin_type=True)
    block=msgpack.packb(msgpack.ExtType(99,b'\xd2'+struct.pack('>I',len(raw))+lz4.block.compress(raw,store_size=False)))
    from battle_field import reader
    stream=reader(data);toc=next(stream);start=stream.tell()
    offset,length=toc['MasterTextLabel']
    head=bytearray(data[:start])
    stream=reader(head)
    for _ in range(stream.read_map_header()):
        name=next(stream);stream.read_array_header()
        pos=stream.tell();old=next(stream);size_pos=stream.tell();next(stream)
        if head[pos]!=210 or head[size_pos]!=210:
            raise ValueError('不支持的主表目录格式')
        if old>offset:
            struct.pack_into('>i',head,pos+1,old+len(block)-length)
        if name=='MasterTextLabel':
            struct.pack_into('>i',head,size_pos+1,len(block))
    return bytes(head)+data[start:start+offset]+block+data[start+offset+length:]
