"""Resolve the installed game's Direct3D shader programs for a foil material."""
import struct
from dataclasses import dataclass

import lz4.block


@dataclass(frozen=True)
class ShaderPass:
    vertex: bytes
    pixel: bytes
    vertex_parameters: bytes
    pixel_parameters: bytes
    vertex_common: object
    pixel_common: object
    names: dict


def shader_passes(bundle, material):
    """Select the non-instanced, unclipped variant for a standalone card quad."""
    reference = material.mat.m_Shader
    if not reference.m_FileID:
        raise ValueError('闪卡材质没有引用外部着色器')
    cab = material.file.externals[reference.m_FileID-1].path.rsplit('/',1)[-1]
    shader = next(obj.read() for obj in bundle.objects
                  if obj.type.name == 'Shader' and obj.path_id == reference.m_PathID
                  and obj.assets_file.name.lower() == cab.lower())
    platform = shader.platforms.index(4)  # Unity D3D11
    offset = shader.offsets[platform]
    lengths = shader.compressedLengths[platform]
    sizes = shader.decompressedLengths[platform]
    compressed = bytes(shader.compressedBlob)
    segments = [lz4.block.decompress(compressed[start:start+length],uncompressed_size=size)
                for start,length,size in zip(offset,lengths,sizes)]

    def blob(index):
        directory = segments[0]
        if not 0 <= index < struct.unpack_from('<I',directory)[0]:
            raise ValueError('着色器程序索引超出范围')
        start, length, segment = struct.unpack_from('<III',directory,4+index*12)
        return segments[segment][start:start+length]

    def bytecode(index):
        program = blob(index)
        dxbc = program.find(b'DXBC')
        if dxbc < 0 or dxbc + 32 > len(program):
            raise ValueError('着色器程序内缺少 DXBC')
        size = struct.unpack_from('<I',program,dxbc+24)[0]
        if size < 32 or dxbc + size > len(program):
            raise ValueError('DXBC 程序长度无效')
        return program[dxbc:dxbc+size]

    parsed = shader.m_ParsedForm
    keywords = set(material.mat.m_ValidKeywords or ())
    result = []
    for shader_pass in parsed.m_SubShaders[0].m_Passes:
        programs = []
        parameters = []
        for stage in (shader_pass.progVertex,shader_pass.progFragment):
            # The legacy stage array is indexed by GPU program family; D3D
            # variants have GPU types 15–18 (SM4/SM5 vertex/pixel).
            candidates = [(entry, stage.m_ParameterBlobIndices[group_index][entry_index])
                          for group_index,group in enumerate(stage.m_PlayerSubPrograms or ())
                          for entry_index,entry in enumerate(group)
                          if entry.m_GpuProgramType in (15,16,17,18)]
            supported = {i for entry,_ in candidates for i in entry.m_KeywordIndices}
            active = {i for i in supported if parsed.m_KeywordNames[i] in keywords}
            entry = next((item for item in candidates if set(item[0].m_KeywordIndices) == active),None)
            if entry is None:
                raise ValueError('找不到匹配的 Direct3D 着色器变体')
            programs.append(bytecode(entry[0].m_BlobIndex))
            parameters.append(blob(entry[1]))
        result.append(ShaderPass(*programs,*parameters,shader_pass.progVertex.m_CommonParameters,shader_pass.progFragment.m_CommonParameters,{i:n for n,i in shader_pass.m_NameIndices}))
    return result


def parameter_layout(data):
    """Decode the constant and resource records in Unity's player program blob."""
    cursor = 0

    def integers(count):
        nonlocal cursor
        values = struct.unpack_from('<'+'i'*count,data,cursor)
        cursor += 4*count
        return values

    def string():
        nonlocal cursor
        length, = integers(1)
        if not 0 <= length <= len(data)-cursor:
            raise ValueError('无效的着色器参数名称')
        name = data[cursor:cursor+length].decode()
        cursor = (cursor+length+3) & ~3
        return name

    version, buffers, *header = integers(6)
    if version != 202012090 or any(header):
        raise ValueError('未知的着色器参数布局')
    layout = {}
    for _ in range(buffers-1):
        name=string()
        size,count=integers(2)
        fields={}
        for _ in range(count):
            field=string()
            kind,rows,columns,array,_,offset=integers(6)
            fields[field]=(offset,kind,rows,columns,array)
        if integers(1)!=(0,):
            raise ValueError('暂未支持结构化着色器常量')
        layout[name]=(size,fields)
    resources={}
    count,=integers(1)
    for _ in range(count):
        name=string()
        resources[name]=integers(3 if name in layout else 4)
    if cursor!=len(data):
        raise ValueError('着色器参数记录未读完')
    return layout,resources


def stage_bindings(blob, common, names):
    layout, resources = parameter_layout(blob)
    for buffer in common.m_ConstantBuffers:
        name = names[buffer.m_NameIndex]
        size, fields = layout.setdefault(name,(buffer.m_Size,{}))
        if size < buffer.m_Size:
            layout[name] = (buffer.m_Size,fields)
        for value in buffer.m_VectorParams:
            fields.setdefault(names[value.m_NameIndex],
                              (value.m_Index,value.m_Type,1,value.m_Dim,value.m_ArraySize))
        for value in buffer.m_MatrixParams:
            fields.setdefault(names[value.m_NameIndex],
                              (value.m_Index,value.m_Type,value.m_RowCount,4,value.m_ArraySize))
    for bind in common.m_ConstantBufferBindings:
        resources.setdefault(names[bind.m_NameIndex],(1,bind.m_Index,bind.m_ArraySize))
    return layout,resources


def render_frames(program, material, face, times, overrides=None):
    """Render a card quad with the installed vertex and pixel programs."""
    import ctypes as c
    from PIL import Image

    ptr, uint, flt = c.c_void_p, c.c_uint, c.c_float
    from contextlib import ExitStack

    def call(obj, index, result, types, *args):
        table = c.cast(obj,c.POINTER(c.POINTER(ptr)))[0]
        return c.WINFUNCTYPE(result,ptr,*types)(table[index])(obj,*args)

    def check(hr):
        if hr < 0:
            raise OSError(f'Direct3D 11: 0x{hr & 0xffffffff:08X}')

    class Initial(c.Structure):
        _fields_=[('data',ptr),('pitch',uint),('slice',uint)]

    class Element(c.Structure):
        _fields_=[('name',c.c_char_p),('index',uint),('format',uint),
                  ('slot',uint),('offset',uint),('classification',uint),('rate',uint)]

    class Mapping(c.Structure):
        _fields_=[('data',ptr),('pitch',uint),('depth',uint)]

    class Sampler(c.Structure):
        _fields_=[('filter',uint),('u',uint),('v',uint),('w',uint),
                  ('bias',flt),('anisotropy',uint),('comparison',uint),
                  ('border',flt*4),('minlod',flt),('maxlod',flt)]

    with ExitStack() as resources:
        def retain(obj):
            resources.callback(call,obj,2,c.c_ulong,[])
            return obj

        dev,ctx=ptr(),ptr()
        create=c.WinDLL('d3d11').D3D11CreateDevice
        create.argtypes=[ptr,uint,ptr,uint,ptr,uint,uint,c.POINTER(ptr),ptr,c.POINTER(ptr)]
        hr=create(None,1,None,0,None,0,7,c.byref(dev),None,c.byref(ctx))
        if hr<0:
            check(create(None,5,None,0,None,0,7,c.byref(dev),None,c.byref(ctx)))
        retain(dev)
        retain(ctx)

        def make(index,types,*args):
            obj=ptr()
            check(call(dev,index,c.c_long,[*types,c.POINTER(ptr)],*args,c.byref(obj)))
            return retain(obj)

        def set_context(index,types,*args):
            call(ctx,index,None,types,*args)

        def bind(index,slot,obj):
            set_context(index,[uint,uint,ptr],slot,1,c.byref(obj))

        def texture(width,height,usage,flags,cpu,image=None):
            desc=(uint*11)(width,height,1,1,28,1,0,usage,flags,cpu,0)
            initial=None
            if image is not None:
                raw=c.create_string_buffer(image.convert('RGBA').tobytes())
                initial=Initial(c.cast(raw,ptr),width*4,0)
            return make(5,[ptr,ptr],desc,c.byref(initial) if initial else None)

        for index,command,code in ((12,11,program.vertex),(15,9,program.pixel)):
            shader=make(index,[ptr,c.c_size_t,ptr],code,len(code),None)
            set_context(command,[ptr,ptr,uint],shader,None,0)
        count=struct.unpack_from('<I',program.vertex,28)[0]
        offsets=struct.unpack_from('<'+'I'*count,program.vertex,32)
        signature=next(program.vertex[o+8:] for o in offsets if program.vertex[o:o+4]==b'ISGN')
        count,start=struct.unpack_from('<II',signature)
        elements=[]
        for offset in range(start,start+count*24,24):
            name,index,_,kind,_,_=struct.unpack_from('<6I',signature,offset)
            semantic=signature[name:signature.index(b'\0',name)]
            elements.append(Element(semantic,index,{1:3,2:4,3:2}[kind],0,len(elements)*16,0,0))
        inputs=(Element*len(elements))(*elements)
        layout=make(11,[ptr,uint,ptr,c.c_size_t],inputs,len(inputs),program.vertex,len(program.vertex))
        set_context(17,[ptr],layout)

        vertices=[]
        for x,y,u,v in [(-1,1,0,0),(1,1,1,0),(-1,-1,0,1),(1,-1,1,1)]:
            for entry in elements:
                if entry.name==b'POSITION':
                    value=(x,y,0,1)
                elif entry.name==b'TEXCOORD' and entry.index==0:
                    value=(u,v,0,0)
                elif entry.name==b'COLOR':
                    value=(1,1,1,1)
                else:
                    value=(0,0,0,0)
                vertices.extend(value)
        vertex_data=(flt*len(vertices))(*vertices)
        desc=(uint*6)(c.sizeof(vertex_data),0,1,0,0,0)
        initial=Initial(c.cast(vertex_data,ptr),0,0)
        vb=make(3,[ptr,ptr],desc,c.byref(initial))
        set_context(18,[uint,uint,ptr,ptr,ptr],0,1,c.byref(vb),c.byref(uint(len(elements)*16)),c.byref(uint(0)))
        set_context(24,[uint],5)

        width,height=face.size
        target=texture(width,height,0,32,0)
        staging=texture(width,height,3,0,0x20000)
        view=make(9,[ptr,ptr],target,None)
        set_context(33,[uint,ptr,ptr],1,c.byref(view),None)
        viewport=(flt*6)(0,0,width,height,0,1)
        set_context(44,[uint,ptr],1,viewport)

        props=material.mat.m_SavedProperties
        floats=dict(props.m_Floats)
        values={name:(value,) for name,value in floats.items()}
        for name,color in props.m_Colors:
            values[name]=(color.r,color.g,color.b,color.a)
        for name,env in material.slots.items():
            values[name+'_ST']=(env.m_Scale.x,env.m_Scale.y,env.m_Offset.x,env.m_Offset.y)
        identity=(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1)
        for name in ('unity_ObjectToWorld','unity_MatrixVP','unity_WorldToObject'):
            values[name]=identity
        buffers=[]
        for blob,common,command in ((program.vertex_parameters,program.vertex_common,7),
                                    (program.pixel_parameters,program.pixel_common,16)):
            layouts,bindings=stage_bindings(blob,common,program.names)
            for name,(size,fields) in layouts.items():
                if name not in bindings:
                    continue
                data=bytearray((size+15)&~15)
                for field,(offset,kind,rows,columns,_) in fields.items():
                    value=values.get(field)
                    if value is None:
                        continue
                    expected=rows*columns
                    if offset+expected*4<=len(data):
                        struct.pack_into('<'+'f'*expected,data,offset,*value[:expected])
                desc=(uint*6)(len(data),0,4,0,0,0)
                buffer=make(3,[ptr,ptr],desc,None)
                bind(command,bindings[name][1],buffer)
                buffers.append((buffer,data,fields.get('_Time')))
            if command!=16:
                continue
            for name,binding in bindings.items():
                if len(binding)!=4:
                    continue
                _,slot,sampler,dimension=binding
                if dimension!=4:
                    continue
                entry=material.slots.get(name)
                obj=material.texture(entry.m_Texture) if entry and entry.m_Texture.m_PathID and name!='_MainTex' else None
                image=face if name=='_MainTex' else obj.image if obj else Image.new('RGBA',(1,1),(0,0,0,255))
                if overrides and name in overrides:
                    image=overrides[name]
                tex=texture(*image.size,0,8,0,image)
                srv=make(7,[ptr,ptr],tex,None)
                bind(8,slot,srv)
                if sampler>=0:
                    address=3 if name=='_MainTex' else 1
                    settings=Sampler(0x15,address,address,address,0,1,1,(flt*4)(),0,0)
                    state=make(23,[ptr],c.byref(settings))
                    bind(10,sampler,state)

        for time in times:
            for buffer,data,time_slot in buffers:
                if time_slot:
                    struct.pack_into('<4f',data,time_slot[0],time/20,time,time*2,time*3)
                memory=(c.c_ubyte*len(data)).from_buffer(data)
                set_context(48,[ptr,uint,ptr,ptr,uint,uint],buffer,0,None,memory,0,0)
            set_context(50,[ptr,ptr],view,(flt*4)(0,0,0,1))
            set_context(13,[uint,uint],4,0)
            set_context(47,[ptr,ptr],staging,target)
            mapping=Mapping()
            check(call(ctx,14,c.c_long,[ptr,uint,uint,uint,ptr],staging,0,1,0,c.byref(mapping)))
            try:
                frame=c.string_at(mapping.data,mapping.pitch*height)
            finally:
                set_context(15,[ptr,uint],staging,0)
            yield Image.frombytes('RGBA',(width,height),frame,'raw','RGBA',mapping.pitch,1).convert('RGB')
