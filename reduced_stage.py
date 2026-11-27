"""Build the static backdrop of the reduced field scene in camera coordinates."""
import copy,io,math
from functools import lru_cache
import numpy as np
import cv2
from PIL import Image

@lru_cache(maxsize=1)
def sky_shader():
    import workshop as w
    for obj in w.load_bundle('shader').objects:
        if obj.type.name=='Shader' and obj.read().m_ParsedForm.m_Name=='Skybox/Cubemap':
            return obj.assets_file.name,obj.path_id
    raise ValueError('缺少场地天空盒着色器')

def camera_basis(stage):
    import workshop as w
    env=w.load_bundle(f'Work/FieldTest/BattleViewCameraSetting/BattleView_BGb{stage:05}')
    tr=next(o.read() for o in env.objects if o.type.name=='Transform' and o.read().m_GameObject.read().m_Name=='BattleFieldCamera')
    result=np.eye(3)
    while tr:
        q=tr.m_LocalRotation
        x,y,z,v=q.x,q.y,q.z,q.w
        rot=np.array([[1-2*(y*y+z*z),2*(x*y-z*v),2*(x*z+y*v)],
                      [2*(x*y+z*v),1-2*(x*x+z*z),2*(y*z-x*v)],
                      [2*(x*z-y*v),2*(y*z+x*v),1-2*(x*x+y*y)]])
        result=rot@result
        tr=tr.m_Father.read() if tr.m_Father.m_PathID else None
    return result

def project_faces(raw,stage,size=1024):
    rgb=np.asarray(Image.open(io.BytesIO(raw)).convert('RGBA'))
    h,w=rgb.shape[:2];basis=camera_basis(stage)
    axis=(np.arange(size,dtype=np.float32)+.5)*2/size-1
    import etcpak
    parts=[]
    for face in range(6):
        pixels=np.empty((size,size,4),dtype=np.uint8)
        for row in range(0,size,128):
            u,v=np.meshgrid(axis,axis[row:row+128]);one=np.ones_like(u)
            xyz=[(one,-v,-u),(-one,-v,u),(u,one,v),(u,-one,-v),(u,-v,one),(-u,-v,-one)][face]
            directions=np.stack(xyz,axis=-1)@basis.astype(np.float32)
            depth=np.maximum(directions[:,:,2],.0001)*math.tan(math.radians(60)/2)
            mx=np.clip((.5+directions[:,:,0]/(2*depth*w/h))*w,0,w-1).astype('float32')
            my=np.clip((.5-directions[:,:,1]/(2*depth))*h,0,h-1).astype('float32')
            pixels[row:row+128]=cv2.remap(rgb,mx,my,cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE)
        parts.append(etcpak.compress_bc1(pixels.tobytes(),size,size))
    return b''.join(parts)

def rebuild(n,b,raw):
    import workshop as w
    import re
    stage=int(re.search(r'BGb(\d+)',n)[1]);key=w.assets[n][1]
    crypt=lambda v:w.wbtool.decrypt_bytes(v,w.KEY,key)
    env=w.UnityPy.load(crypt(b))
    settings=next(o.read() for o in env.objects if o.type.name=='RenderSettings')
    ptr=settings.m_SkyboxMaterial
    src=next(o for o in env.objects if o.type.name=='Material' and o.path_id==ptr.m_PathID and (not ptr.m_FileID or o.assets_file.name.lower()==settings.object_reader.assets_file.externals[ptr.m_FileID-1].path.split('/')[-1].lower()))
    mat=src.read();af=src.assets_file
    cubes=[o for o in af.objects.values() if o.type.name=='Cubemap']
    cube=next((o for o in cubes if o.read().m_Name=='CraftStageBackdrop'),None)
    if cube is None:
        cube=copy.copy(cubes[0]);cube.path_id=max(af.objects)+1;af.objects[cube.path_id]=cube
    tex=cube.read();tex.m_Name='CraftStageBackdrop';size=4096 if Image.open(io.BytesIO(raw)).width>1600 else 2048;tex.m_Width=tex.m_Height=size
    tex.m_ColorSpace=1;tex.m_TextureFormat=10;tex.m_MipCount=1;tex.m_ImageCount=6
    tex.image_data=project_faces(raw,stage,size);tex.m_CompleteImageSize=size*size//2
    tex.m_StreamData.path='';tex.m_StreamData.size=tex.m_StreamData.offset=0;tex.save()
    cab,shader=sky_shader();idx=next(i for i,x in enumerate(af.externals,1) if x.path.split('/')[-1].lower()==cab.lower())
    tree=src.read_typetree();tree['m_Shader']={'m_FileID':idx,'m_PathID':shader}
    props=tree['m_SavedProperties'];props['m_TexEnvs']=[('_Tex',{'m_Texture':{'m_FileID':0,'m_PathID':cube.path_id},'m_Scale':{'x':1.,'y':1.},'m_Offset':{'x':0.,'y':0.}})]
    props['m_Floats']=[('_Exposure',1.),('_Rotation',0.)];props['m_Colors']=[('_Tint',dict(r=.5,g=.5,b=.5,a=1.))]
    src.save_typetree(tree)
    for o in env.objects:
        if o.type.name in ('MeshRenderer','SkinnedMeshRenderer','ParticleSystemRenderer','TrailRenderer','LineRenderer','Terrain','BillboardRenderer'):
            r=o.read()
            if r.m_Enabled:r.m_Enabled=False;r.save()
    out=env.file.save(packer='lz4');check=w.UnityPy.load(out)
    assert any(o.type.name=='Cubemap' and o.read().m_Name=='CraftStageBackdrop' and len(o.read().image_data)==6*size*size//2 for o in check.objects)
    return crypt(out)
