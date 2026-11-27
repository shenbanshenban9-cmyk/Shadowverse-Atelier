# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys, os
sys.path.insert(0,str(Path(SPECPATH)/"lib"))
from PyInstaller.utils.hooks import collect_all
root=Path(SPECPATH)
public_assets=os.environ.get('ATELIER_PUBLIC_ASSETS')
assets=Path(public_assets) if public_assets else root
datas=[(str(root/name),'.') for name in ['background.js','field.js','voice.js','foil.js','candidate.bin']]
datas += [(str(assets/name),'.') for name in ['index.html','icon.ico']]
datas += [(str(root/'hero1223'/name),'hero1223') for name in ['spine-player.min.js','spine-player.min.css','LICENSE']]
datas += [(str(root/name),name) for name in ['audio','编码器许可证']]
datas.append((str(root.parent/'初次使用指南.md'),'.'))
binaries=[]
hidden=['webview.platforms.edgechromium','webview.platforms.winforms','pip._vendor.msgpack','reduced_stage','audio_encoder','header_render']
for package in ['UnityPy','texture2ddecoder','etcpak','astc_encoder','archspec','fmod_toolkit','pyfmodex','lz4','webview','Cryptodome']:
    d,b,h=collect_all(package)
    datas+=d;binaries+=b;hidden+=h
a=Analysis([str(root/'desktop.py')],pathex=[str(root),str(root/'lib')],binaries=binaries,datas=datas,hiddenimports=hidden,
    excludes=['tkinter','matplotlib','pandas','scipy','IPython','pytest','PyQt5','PyQt6','PySide2','PySide6'])
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='影之工坊',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,icon=str(assets/'icon.ico'))
