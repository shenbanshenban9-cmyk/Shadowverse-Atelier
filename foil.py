"""Read and transplant premium card materials without changing the face texture."""
import base64
import copy
import io
import math
import random
import sqlite3
from contextlib import closing
from PIL import Image


def name(art):
    return f'Card/Materials/{int(art) + 10}_M'


class Material:
    def __init__(self, w, resource, original):
        self.w = w
        self.resource = resource
        self.env = (w.load_original_bundle if original else w.load_bundle)(resource)
        self.mat, self.main = w.material_info(self.env)
        self.file = self.mat.object_reader.assets_file
        self.files = {}
        self.slots = dict(self.mat.m_SavedProperties.m_TexEnvs)

    def texture(self, ptr):
        file = self.file
        if ptr.m_FileID:
            cab = file.externals[ptr.m_FileID-1].path.split('/')[-1].lower()
            if cab not in self.files:
                with closing(sqlite3.connect((self.w.P/'meta').as_uri()+'?mode=ro', uri=True)) as db:
                    deps = db.execute('select b.n from r join a on a.i=r.f join a b on b.i=r.t where a.n=?', (self.resource,)).fetchall()
                for dep, in deps:
                    if dep in (self.resource,'shader') or dep not in self.w.assets:
                        continue
                    for obj in self.w.load_bundle(dep).objects:
                        self.files[obj.assets_file.name.lower()] = obj.assets_file
                    if cab in self.files:
                        break
            file = self.files[cab]
        return file.objects[ptr.m_PathID].read()


# Use the donor material's flower, star, dust and light textures with its
# existing movement modes. Speeds scale those modes independently per layer.
PRESETS = {
    'common': {
        '_Back2': ((1.0,.64,.48), .52),    # drifting flowers
        '_Front1': ((.86,.62,.29), .76),   # fine gold dust
        '_Front3': ((.34,.21,.11), .68),   # soft flare
    },
    'common:frost': {
        '_Back3': ((.35,.64,.83), .57),    # bubbles
        '_Back4': ((.61,.84,1.0), .70),    # blinking stars
        '_Front2': ((.36,.61,.85), .61),   # luminous dust
    },
    'common:silk': {
        '_Back1': ((.76,.51,.86), .53),    # feathers
        '_Back2': ((.70,.38,.65), .69),    # flowers
        '_Middle1': ((.42,.24,.50), .46),  # flowing light
        '_Front2': ((.49,.37,.64), .77),   # soft particles
    },
}


def preset_mask(style):
    size = 128
    pixels = []
    for row in range(size):
        y = row / (size - 1)
        for col in range(size):
            x = col / (size - 1)
            if style == 'common':
                weight = math.exp(-((x-.14)/.32)**2-((y-.82)/.34)**2)
            elif style == 'common:frost':
                weight = max(math.exp(-((x-.88)/.24)**2-((y-.24)/.29)**2),
                             math.exp(-((x-.10)/.25)**2-((y-.76)/.31)**2))
            else:
                weight = math.exp(-((y-.80+.15*x)/.16)**2)
            value = round(235 * weight)
            pixels.append((value,value,value,255))
    image = Image.new('RGBA',(size,size))
    image.putdata(pixels)
    return image


def apply_preset(material, style):
    layers = PRESETS[style]
    active = {layer + "Tex" for layer in layers} | {"_MainTex", "_MaskTex"}
    for key,env in material.slots.items():
        if key not in active:
            env.m_Texture.m_PathID = env.m_Texture.m_FileID = 0
    props = material.mat.m_SavedProperties
    values = dict(props.m_Floats)
    for key in values:
        if 'Intensity' in key or key.endswith('_Distortion'):
            values[key] = 0.
        if key.startswith('_Flag') and key.endswith('MoveType') and key[5:-8] not in {layer[1:] for layer in layers}:
            values[key] = -1.
    colors = dict(props.m_Colors)
    for layer,(tint,speed) in layers.items():
        for param in ('ScrollU','ScrollV','Rotate','FlashSpeed'):
            key = layer + param
            if key in values:
                values[key] *= speed
        colors[layer+'Color'].r,colors[layer+'Color'].g,colors[layer+'Color'].b = tint
    if style == 'common:frost':
        # The new tile spans three old repeats on each axis.
        scale = material.slots['_Back3Tex'].m_Scale
        scale.x /= 3
        scale.y /= 3
        for axis in ('U','V'):
            values['_Back3Scroll'+axis] /= 3
    props.m_Floats = list(values.items())


def frost_bubbles(texture):
    original = texture.image.convert('RGB').resize((256,256))
    ring = original.crop((131,132,252,251))
    glint = original.crop((113,17,155,59))
    size = 768
    tile = Image.new('RGB',(size,size))
    rng = random.Random(48219)
    centers = []
    # Distances wrap at all four edges, just as the game scrolls the texture.
    for i in range(36):
        diameter = rng.randint(67,112) if i < 18 else rng.randint(18,38)
        for attempt in range(2048):
            x,y = rng.randrange(size),rng.randrange(size)
            if all(math.hypot(min(abs(x-a),size-abs(x-a)), min(abs(y-b),size-abs(y-b)))
                   > (diameter+d)*.5+9 for a,b,d in centers):
                break
        else:
            continue
        centers.append((x,y,diameter))
        sprite = (ring if i < 18 else glint).resize((diameter,diameter),Image.Resampling.LANCZOS)
        for dx in (-size,0,size):
            for dy in (-size,0,size):
                tile.paste(sprite,(x-diameter//2+dx,y-diameter//2+dy))
    return tile.convert('RGBA')


def source(w, art, action):
    common = action in PRESETS
    resource = name('108641201' if common else art if action in ('current','original') else action)
    material = Material(w, resource, common or action == 'original')
    if common:
        apply_preset(material, action)
    return material, common


def replacement(w, art, action):
    target = name(art)
    path,key = w.assets[target]
    if action == 'original':
        return (w.prior_backup(path) or path).read_bytes()
    dst = Material(w,target,True)
    src,common = source(w,art,action)
    def shader_key(m):
        p=m.mat.m_Shader
        return (m.file.externals[p.m_FileID-1].path if p.m_FileID else m.file.name,p.m_PathID)
    if shader_key(src)!=shader_key(dst):
        raise ValueError('两种闪卡使用不同着色器，未写入更改')
    template = next(o for o in dst.file.objects.values() if o.type.name=='Texture2D')
    copies = {}
    for slot,env in src.slots.items():
        ptr = env.m_Texture
        if slot=='_MainTex':
            env.m_Texture = dst.main
        elif ptr.m_PathID:
            identity = (ptr.m_FileID,ptr.m_PathID,slot if common else '')
            if identity not in copies:
                tex = src.texture(ptr)
                obj = copy.copy(template)
                obj.path_id = next(i for i in range(1,len(dst.file.objects)+2) if i not in dst.file.objects)
                if common and slot=='_MaskTex':
                    tex.set_image(preset_mask(action),mipmap_count=1)
                elif action == 'common:frost' and slot == '_Back3Tex':
                    tex.set_image(frost_bubbles(tex),mipmap_count=tex.m_MipCount or 1)
                else:
                    tex.image_data = tex.get_image_data()
                    if tex.m_StreamData:
                        tex.m_StreamData.path=''
                        tex.m_StreamData.size=tex.m_StreamData.offset=0
                tex.object_reader=obj
                dst.file.objects[obj.path_id]=obj
                tex.save()
                copies[identity]=obj.path_id
            ptr.m_FileID,ptr.m_PathID=0,copies[identity]
    for attr in ('m_SavedProperties','m_ValidKeywords','m_InvalidKeywords','m_ShaderKeywords','disabledShaderPasses'):
        if hasattr(src.mat,attr):
            setattr(dst.mat,attr,getattr(src.mat,attr))
    dst.mat.save()
    return w.wbtool.decrypt_bytes(dst.env.file.save(packer='lz4'),w.KEY,key)


def thumbnail(image):
    image.thumbnail((384,384))
    data=io.BytesIO()
    image.save(data,'PNG')
    return 'data:image/png;base64,'+base64.b64encode(data.getvalue()).decode()


def preview(w,art,action):
    src,common=source(w,art,action)
    layers=[]
    floats=dict(src.mat.m_SavedProperties.m_Floats)
    colors=dict(src.mat.m_SavedProperties.m_Colors)
    keywords=set(src.mat.m_ValidKeywords or ()) if src.mat.m_ValidKeywords is not None else None
    optional_layers={
        '_Back3':'USE_BACK3_4', '_Back4':'USE_BACK3_4',
        '_Front3':'USE_FRONT3_4', '_Front4':'USE_FRONT3_4',
        '_Middle1':'USE_MIDDLE1_2', '_Middle2':'USE_MIDDLE1_2',
        '_Middle3':'USE_MIDDLE3_4', '_Middle4':'USE_MIDDLE3_4',
    }
    for slot,env in src.slots.items():
        if not slot.startswith(('_Front','_Back','_Middle')) or not env.m_Texture.m_PathID:
            continue
        key=slot[:-3]
        required=optional_layers.get(key)
        if keywords is not None and required and required not in keywords:
            continue
        color=colors.get(key+'Color')
        texture=src.texture(env.m_Texture)
        image=frost_bubbles(texture) if action=='common:frost' and slot=='_Back3Tex' else texture.image.convert('RGBA')
        if color:
            bands=image.split()
            image=Image.merge('RGBA',tuple(band.point(lambda value: min(255,round(value*max(0,gain)))) for band,gain in zip(bands,(color.r,color.g,color.b)))+(bands[3],))
        layers.append(dict(image=thumbnail(image),kind=key,
            u=floats.get(key+'ScrollU',0),v=floats.get(key+'ScrollV',0),rotation=floats.get(key+'Rotate',0),
            offset=[env.m_Offset.x,env.m_Offset.y],angle=floats.get(key+'ScrollAngle',0),
            flash=(keywords is None or 'USE_FLASH' in keywords) and bool(floats.get(key+'_Flash',0)),speed=floats.get(key+'FlashSpeed',1),
            minimum=floats.get(key+'FlashMin',0),maximum=floats.get(key+'FlashMax',1),
            spiral=floats.get(key+'Spiral',0),
            warp=int(floats.get(key+'_Distortion',0)),
            motion=int(floats.get(key+'_MoveType',1)),
            fixed=floats.get(key+'FixedRotate',0),
            blend=int(floats.get(key+'_RenderType',1)),
            scale=[env.m_Scale.x,env.m_Scale.y],alpha=color.a if color else 1))
    mask=src.slots.get('_MaskTex')
    mask_image = preset_mask(action) if common else src.texture(mask.m_Texture).image if mask and mask.m_Texture.m_PathID else None
    sub=src.slots.get('_MaskTexSub')
    secondary=thumbnail(src.texture(sub.m_Texture).image.convert('RGB')) if sub and sub.m_Texture.m_PathID else None
    distortion=[]
    for i in (1,2):
        key=f'_Distortion{i}'
        env=src.slots.get(key+'Tex')
        if not env or not env.m_Texture.m_PathID or (keywords is not None and f'USE_DISTORTION{i}' not in keywords):
            distortion.append(None)
            continue
        texture=src.texture(env.m_Texture)
        distortion.append(dict(image=thumbnail(texture.image.convert('RGB')),
            radial='_ring' in texture.m_Name.lower(),spiral=floats.get(key+'Spiral',0),
            scale=[env.m_Scale.x,env.m_Scale.y],offset=[env.m_Offset.x,env.m_Offset.y],
            u=floats.get(key+'ScrollU',0),v=floats.get(key+'ScrollV',0),
            angle=floats.get(key+'ScrollAngle',0),rotation=floats.get(key+'Rotate',0),
            motion=int(floats.get(key+'_MoveType',1)),fixed=0,
            intensity=[floats.get(key+'IntensityU',0),floats.get(key+'IntensityV',0)]))
    return dict(layers=layers,mask=thumbnail(mask_image.convert('RGB')) if mask_image else None,
                secondary=secondary,distortion=distortion)


def native_preview(w, art, action, face):
    from foil_native import render_frames, shader_passes

    material,common=source(w,art,action)
    program=shader_passes(w.load_bundle('shader'),material)[0]
    overrides={}
    if common:
        overrides['_MaskTex']=preset_mask(action)
        if action=='common:frost':
            entry=material.slots['_Back3Tex']
            overrides['_Back3Tex']=frost_bubbles(material.texture(entry.m_Texture))
    face=face.convert('RGBA').resize((320,386),Image.Resampling.LANCZOS)
    frames=list(render_frames(program,material,face,(i/24 for i in range(72)),overrides))
    if not frames[0].getbbox():
        raise ValueError('原版着色器输出为空')
    result=io.BytesIO()
    frames[0].save(result,'WEBP',save_all=True,append_images=frames[1:],duration=42,loop=0,quality=87,method=2)
    return result.getvalue()
