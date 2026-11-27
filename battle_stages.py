from pathlib import Path
from PIL import Image
import io,base64,math

def source_crop(recipe,aspect):
    raw=base64.b64decode(recipe['src'].split(',',1)[1],validate=True)
    image=Image.open(io.BytesIO(raw)).convert('RGB')
    zoom=float(recipe['zoom']);x=float(recipe['x']);y=float(recipe['y'])
    if not (1<=zoom<=5 and 0<=x<=1 and 0<=y<=1):raise ValueError('无效构图')
    width=min(image.width,image.height*aspect)/zoom;height=width/aspect
    left=(image.width-width)*x;top=(image.height-height)*y
    size=(max(1,round(width)),max(1,round(height)))
    result=image.resize(size,Image.Resampling.LANCZOS,box=(left,top,min(image.width,left+width),min(image.height,top+height)))
    white=float(recipe['white'])/100
    if not 0<=white<=1:raise ValueError('无效白化程度')
    if white:result=Image.blend(result,Image.new('RGB',size,'white'),white)
    buf=io.BytesIO();result.save(buf,'PNG');return buf.getvalue()

class BattleStages:
    def __init__(self, assets, export):
        self.root = export / '战场背景'
        self.ids = sorted(int(n.rsplit('_',1)[1]) for n in assets if n.startswith('UI/BattleStage/utx_pict_stage_') and n.rsplit('_',1)[1].isdigit())

    def layers(self, stage, include_scene=False):
        import workshop as w
        import game_data
        if stage not in self.ids:
            raise ValueError('未知场地')
        game_data.prepare_stage(w,stage)
        field = f'Field{stage:02}'
        scene = f'BGb{stage:05}'
        layers = []
        for quality in ('FieldLow','FieldSuperLow'):
            layers.append((f'Battle/Textures/{quality}/{field}', self.root/quality/field/(field+'.png')))
        bundle = 'Assets/_Wizard2Resources/3d/Env/Battle/'+scene
        if include_scene and bundle in w.assets:
            source,seed=w.assets[bundle]
            names=game_data.texture_names(str(source),seed,w.KEY,source.stat().st_mtime_ns)
            for name in names:
                if name.lower().endswith(('_nrm','_normal','_mask')):continue
                layers.append((bundle,self.root/'Battle'/scene/(name+'.png')))
        return layers

    def cover(self, stage):
        import workshop as w
        import game_data
        if stage not in self.ids:raise ValueError('未知场地')
        game_data.export_texture(w,f'UI/BattleStage/utx_pict_stage_{stage}',self.root/'BattleStage'/f'utx_pict_stage_{stage}')
        name=f'utx_pict_stage_{stage}'
        return (self.root/'BattleStage'/name/(name+'.png')).read_bytes()

    def groups(self, stage, index, data, recipe=None):
        if index!=0:raise ValueError('只支持天空盒替换')
        template=self.layers(stage)[0][1]
        if recipe:
            with Image.open(template) as image:
                data=source_crop(recipe,image.width/image.height)
        return {f'Scenes/BGb{stage:05}{suffix}':[(template,data)]
                for suffix in ('_Reduction','_FullBake_Reduction')}
