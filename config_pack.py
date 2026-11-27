"""Portable, authenticated packs of applied workshop edits."""
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from Cryptodome.Cipher import AES
from banks import media_slots, replace_media

MAGIC = b'SVATELIER\x02'
FORMAT_KEY = bytes.fromhex('395599e6004510a3885ee1648e5c85c6216cbaab6822b873400742c5d828a1df')
BLOCK = 1024 * 1024


def crypt_file(source, target, decrypt, progress):
    with source.open('rb') as src, target.open('wb') as dst:
        if decrypt:
            header = src.read(len(MAGIC))
            if header == b'SVATELIER\x01':
                raise ValueError('这是旧版口令配置，请用旧版工坊导入后，再用新版重新导出')
            if header != MAGIC:
                raise ValueError('请选择影之工坊加密配置文件')
            salt, nonce = src.read(16), src.read(12)
        else:
            salt, nonce = os.urandom(16), os.urandom(12)
            dst.write(MAGIC + salt + nonce)
        key = hashlib.blake2b(salt, key=FORMAT_KEY, digest_size=32).digest()
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(MAGIC + salt + nonce)
        size = source.stat().st_size - src.tell() - (16 if decrypt else 0)
        if size < 0:
            raise ValueError('配置文件不完整')
        done = 0
        while done < size:
            chunk = src.read(min(BLOCK, size - done))
            if not chunk:
                raise ValueError('配置文件不完整')
            dst.write(cipher.decrypt(chunk) if decrypt else cipher.encrypt(chunk))
            done += len(chunk)
            progress(done / size)
        if decrypt:
            try:
                cipher.verify(src.read(16))
            except ValueError:
                raise ValueError('配置文件已损坏或被修改') from None
        else:
            dst.write(cipher.digest())


def allowed(w, owner, cards):
    if owner in cards:
        card = cards[owner]
        faces = w.variant_faces(card['art'], card['evo'])
        names = {w.MASTER}
        voice = card['voice'] or 'dx_' + owner
        names.add('sound/Windows/d/Japanese(JP)/' + voice + '.pck')
        for face in faces:
            art = face['art']
            names.add(w.foil.name(art))
            names.update(('Assets/_Wizard2Resources/Card/Textures/' + art,
                          'Card/Materials/' + art + '_M', 'Card/Header/log_' + art,
                          'Card/IconCrest/CRT_' + art))
        names.update('Card/IconCrest/CRT_' + art for _, art in card['links'])
        names.update('UI/Emblem/utx_tex_em_'+n.rsplit('CRT_',1)[1] for n in list(names) if n.startswith('Card/IconCrest/CRT_'))
        return names
    for stage in w.stages.ids:
        if owner == f'战场-{stage}':
            field = f'Field{stage:02}'
            return {f'Battle/Textures/FieldLow/{field}', f'Battle/Textures/FieldSuperLow/{field}',
                    f'Assets/_Wizard2Resources/3d/Env/Battle/BGb{stage:05}',
                    f'Scenes/BGb{stage:05}_Reduction', f'Scenes/BGb{stage:05}_FullBake_Reduction'}
    raise ValueError('游戏中找不到配置对应的卡牌或场地')


def records(w, groups, single, cards):
    cfg = w.settings()
    roots = dict.fromkeys([cfg['path'], *cfg['previous'], str(w.ROOT/'备份')])
    owners = set()
    for root in roots:
        for folder in Path(root).glob('*'):
            if folder.is_dir() and ((folder.name in cards and 'cards' in groups)
                    or (folder.name in {f'战场-{n}' for n in w.stages.ids} and 'stages' in groups)):
                if single is None or folder.name == str(single):
                    owners.add(folder.name)
    for owner in sorted(owners):
        for name in sorted(allowed(w, owner, cards) & w.assets.keys()):
            path = w.assets[name][0]
            copies = [Path(root)/owner/path.name for root in roots]
            original = min((p for p in copies if p.is_file()), key=lambda p:p.stat().st_mtime_ns, default=None)
            if original and path.is_file():
                yield owner, name, path, original


def differences(w, owner, name, original, current, cards):
    if original == current:
        return
    if name == w.MASTER:
        card = cards[owner]
        before_name=w.game_data.card_name(original,card['id'])
        after_name=w.game_data.card_name(current,card['id'])
        if before_name!=after_name:
            yield dict(kind='card-name',before=before_name),json.dumps(after_name).encode()
        if card['id']==10734120:
            before_summon=w.game_data.summon_name(original)
            after_summon=w.game_data.summon_name(current)
            if before_summon!=after_summon:
                yield dict(kind='summon-name',before=before_summon),json.dumps(after_summon).encode()
        if card['typ']!=1:
            return
        groups = {face['variant'] for face in w.variant_faces(card['art'], card['evo'])}
        for group in sorted(groups):
            before = w.card_fields(original, card, group)
            after = w.card_fields(current, card, group)
            if before != after:
                yield dict(kind='field', group=group, before=before), json.dumps(after).encode()
    elif name.endswith('.pck'):
        base = media_slots(original)
        for key, audio in media_slots(current).items():
            if key in base and audio != base[key]:
                yield dict(kind='voice', media=key, base=digest(base[key])), audio
    else:
        yield dict(kind='resource', base=digest(original)), current


def digest(data):
    return hashlib.sha256(data).hexdigest()


def temporary(parent):
    fd, file = tempfile.mkstemp(dir=parent, suffix='.tmp')
    os.close(fd)
    return Path(file)


class Transaction:
    def __init__(self, scratch):
        self.scratch = scratch
        self.files = {}
        self.backups = {}

    def current(self, target):
        if target in self.files:
            return self.files[target][1].read_bytes()
        return target.read_bytes()

    def put(self, target, data):
        if target not in self.files:
            old = temporary(self.scratch) if target.exists() else None
            if old:
                shutil.copyfile(target, old)
            self.files[target] = old, temporary(self.scratch)
        self.files[target][1].write_bytes(data)

    def commit(self, progress):
        written = []
        try:
            for backup, source in self.backups.items():
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    tmp = temporary(backup.parent)
                    try:
                        shutil.copyfile(source, tmp)
                        os.replace(tmp, backup)
                    finally:
                        tmp.unlink(missing_ok=True)
            for i, (target, (old, new)) in enumerate(self.files.items()):
                target.parent.mkdir(parents=True, exist_ok=True)
                if (old is None) != (not target.exists()) or (old and digest(target.read_bytes()) != digest(old.read_bytes())):
                    raise ValueError('资源已发生变化，请重试')
                tmp = temporary(target.parent)
                try:
                    shutil.copyfile(new, tmp)
                    os.replace(tmp, target)
                finally:
                    tmp.unlink(missing_ok=True)
                written.append(target)
                progress((i+1)/len(self.files))
        except Exception:
            for target in reversed(written):
                old = self.files[target][0]
                if old is None:
                    target.unlink()
                else:
                    shutil.copyfile(old, target)
            raise


def import_resource_zip(w, package, tx, cards, status):
    entries = {}
    for entry in package.infolist():
        if entry.is_dir():
            continue
        parts = entry.filename.replace(chr(92), '/').split('/')
        leaf = parts[-1]
        if len(parts) < 2 or not re.fullmatch(r'[A-Z2-7]{26}', leaf) or parts[-2] != leaf[:2]:
            if leaf.lower().endswith(('.txt', '.md')) or leaf == 'manifest.json':
                continue
            raise ValueError('ZIP 中有无法识别的文件：' + leaf)
        if leaf in entries:
            raise ValueError('ZIP 中有重复的资源编号：' + leaf)
        if entry.file_size > 256*1024*1024:
            raise ValueError('单个资源过大：' + leaf)
        entries[leaf] = entry
    if not entries:
        raise ValueError('ZIP 中没有游戏资源')
    names = {path.name:name for name,(path,_) in w.assets.items() if path.name in entries}
    missing = entries.keys()-names.keys()
    if missing:
        raise ValueError('当前游戏版本缺少资源：' + ', '.join(sorted(missing)))
    owners = {name:[] for name in names.values()}
    for owner in [*cards, *(f'战场-{n}' for n in w.stages.ids)]:
        for name in owners.keys() & allowed(w,owner,cards):
            owners[name].append(owner)
    for i,(leaf,entry) in enumerate(entries.items()):
        name = names[leaf]
        if name == w.MASTER or not owners[name]:
            raise ValueError('该资源需要工坊配置清单：'+name)
        target = w.assets[name][0]
        if not target.is_file():
            raise ValueError('请先在游戏中下载原始资源：'+name)
        value = package.read(entry)
        if name.endswith('.pck'):
            if not media_slots(value):raise ValueError('无效的语音包：'+name)
        else:
            _,seed=w.assets[name]
            env=w.UnityPy.load(w.wbtool.decrypt_bytes(value,w.KEY,seed))
            if not env.objects:raise ValueError('无效的资源包：'+name)
        base = w.prior_backup(target) or target
        for owner in owners[name]:
            tx.backups[Path(w.settings()['path'])/owner/leaf]=base
        tx.put(target,value)
        status(5+70*(i+1)/len(entries),'正在准备 '+name)


def execute(w, action, groups, single, filename, status):
    if not w.P:
        raise ValueError('请先在设置中保存游戏路径')
    cards = {str(c['id']): c for c in w.cards}
    if single is not None and str(single) not in cards:
        raise ValueError('未知卡牌')
    if action not in ('export','import','restore') or (action!='import' and not set(groups) & {'cards','stages'}):
        raise ValueError('请选择卡牌或背景')
    with w.lock, tempfile.TemporaryDirectory(dir=w.DATA) as folder:
        scratch = Path(folder)
        archive = scratch/'payload'
        tx = Transaction(scratch)
        manifest = []
        if action == 'import':
            status(1,'正在读取配置')
            if zipfile.is_zipfile(filename):
                archive=filename
            else:
                crypt_file(filename, archive, True, lambda p:status(2+28*p,'正在读取旧版配置'))
            with zipfile.ZipFile(archive) as package:
                if 'manifest' in package.namelist():
                    manifest = json.loads(package.read('manifest'))
                else:
                    import_resource_zip(w,package,tx,cards,status)
                for i, item in enumerate(manifest):
                    owner, name = item['owner'], item['name']
                    if name not in allowed(w,owner,cards) or name not in w.assets:
                        raise ValueError('当前游戏版本缺少配置中的资源')
                    target = w.assets[name][0]
                    basefile = w.prior_backup(target) or target
                    original = basefile.read_bytes()
                    value = package.read(str(i))
                    kind = item['kind']
                    if kind=='recipe' and owner.startswith('战场-') and name.endswith('_Reduction'):
                        recipe=json.loads(value)
                        if not recipe['src'].startswith('data:image/png;base64,'):
                            raise ValueError('背景预览格式错误')
                        recipe['sceneHash']=item['sceneHash']
                        tx.put(w.DATA/'背景编辑数据'/owner[3:]/'0.json',json.dumps(recipe).encode())
                        continue
                    if kind=='summon-name' and name==w.MASTER and cards[owner]['id']==10734120:
                        if w.game_data.summon_name(original)!=item['before']:
                            raise ValueError('原始召唤物名称版本不匹配')
                        value=w.game_data.rename_summon(tx.current(target),json.loads(value))
                    elif kind=='card-name' and name==w.MASTER:
                        cid=cards[owner]['id']
                        if w.game_data.card_name(original,cid)!=item['before']:
                            raise ValueError('原始卡名版本不匹配')
                        value=w.game_data.rename_card(tx.current(target),cid,json.loads(value))
                    elif kind=='field' and name==w.MASTER:
                        card=cards[owner];group=item['group']
                        if w.card_fields(original,card,group)!=item['before']:
                            raise ValueError('卡牌取景版本不匹配')
                        current=tx.current(target)
                        edit=dict(before=w.card_fields(current,card,group),after=json.loads(value))
                        value=w.patch_card_fields(current,card,{'variants':{str(group):edit}} if group else {'base':edit})
                    elif kind=='voice' and name.endswith('.pck'):
                        key=tuple(item['media'])
                        if digest(media_slots(original).get(key,b''))!=item['base']:
                            raise ValueError('语音版本不匹配')
                        ref=scratch/'voice.wem'
                        ref.with_suffix('.media.json').write_text(json.dumps(dict(bank=key[0],media=key[1])))
                        value=replace_media(tx.current(target),[(ref,value)],original)
                    elif kind=='resource' and name!=w.MASTER and not name.endswith('.pck'):
                        if digest(original)!=item['base']:
                            raise ValueError('原始资源版本不匹配：'+name)
                    else:
                        raise ValueError('无效的配置项')
                    backup=Path(w.settings()['path'])/owner/target.name
                    tx.backups[backup]=basefile
                    tx.put(target,value)
                    status(30+45*(i+1)/len(manifest),'正在准备 '+owner)
        else:
            status(1,'正在检查备份')
            rows=list(records(w,groups,single,cards))
            manual={}
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as package:
                def append(meta,value):
                    package.writestr(str(len(manifest)),value)
                    manifest.append(dict(owner=owner,name=name,**meta))
                for i,(owner,name,target,backup) in enumerate(rows):
                    status(3+65*i/max(1,len(rows)),'正在检查 '+owner)
                    current=tx.current(target)
                    original=backup.read_bytes()
                    for meta,value in differences(w,owner,name,original,current,cards):
                        if action=='export':
                            image_prefixes=('Assets/_Wizard2Resources/Card/Textures/','Card/Header/','Card/IconCrest/','UI/Emblem/')
                            if meta['kind']=='resource' and name.startswith(image_prefixes):
                                image_entry=f'images/{len(manifest)}.png'
                                package.writestr(image_entry,w.current_png(name,Path(name).with_suffix('.png')))
                                meta=dict(meta,image=image_entry)
                            append(meta,value)
                            relative=target.relative_to(w.P).as_posix()
                            if name==w.MASTER:
                                payload=manual.get(relative,original)
                                cid=cards[owner]['id']
                                if meta['kind']=='card-name':
                                    payload=w.game_data.rename_card(payload,cid,json.loads(value))
                                elif meta['kind']=='summon-name':
                                    payload=w.game_data.rename_summon(payload,json.loads(value))
                                elif meta['kind']=='field':
                                    group=meta['group']
                                    edit=dict(before=w.card_fields(payload,cards[owner],group),after=json.loads(value))
                                    payload=w.patch_card_fields(payload,cards[owner],{'variants':{str(group):edit}} if group else {'base':edit})
                                manual[relative]=payload
                            else:
                                manual[relative]=target
                        elif name==w.MASTER and meta['kind']=='summon-name':
                            current=w.game_data.rename_summon(current,meta['before'])
                            tx.put(target,current)
                        elif name==w.MASTER and meta['kind']=='card-name':
                            current=w.game_data.rename_card(current,cards[owner]['id'],meta['before'])
                            tx.put(target,current)
                        elif name==w.MASTER:
                            group=meta['group'];card=cards[owner]
                            edit=dict(before=w.card_fields(current,card,group),after=meta['before'])
                            current=w.patch_card_fields(current,card,{'variants':{str(group):edit}} if group else {'base':edit})
                            tx.put(target,current)
                        else:
                            tx.put(target,original)
                            break
                    if owner.startswith('战场-') and name.endswith('_Reduction') and 'FullBake' not in name:
                        recipe=w.DATA/'背景编辑数据'/owner[3:]/'0.json'
                        if recipe.is_file():
                            info=json.loads(recipe.read_bytes())
                            if action=='restore':
                                tx.put(recipe,b'{}')
                            elif current!=original and (info.get('stamp')==str(target.stat().st_mtime_ns) or info.get('sceneHash')==digest(current)):
                                append(dict(kind='recipe',sceneHash=digest(current)),recipe.read_bytes())
                for relative,payload in manual.items():
                    if isinstance(payload,Path):package.write(payload,relative)
                    else:package.writestr(relative,payload)
                package.writestr('manifest',json.dumps(manifest))
            if action=='export':
                if not manifest:raise ValueError('所选范围没有已写入的更改')
                if filename.suffix.lower()!='.zip':raise ValueError('请选择 .zip 文件')
                tmp=temporary(filename.parent)
                try:
                    status(85,'正在保存 ZIP')
                    shutil.copyfile(archive,tmp)
                    os.replace(tmp,filename)
                finally:tmp.unlink(missing_ok=True)
                return f'已导出 {len(manifest)} 项更改'
        tx.commit(lambda p:status(76+23*p,'正在写入游戏文件'))
        w.card_resource_changed.cache_clear()
        w.material_png.cache_clear()
        return f'已处理 {len(tx.files)} 个文件'
