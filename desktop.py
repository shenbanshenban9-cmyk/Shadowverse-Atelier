"""Native Windows desktop host for Shadowverse Atelier."""
import os
import sys
from pathlib import Path
import threading
import traceback
import ctypes
import multiprocessing

def main():
    import webview
    import workshop
    if '--resource-test' in sys.argv:
        import json
        results=[]
        for card in workshop.cards[:36]:
            try:
                name='Card/Header/log_'+card['art']
                workshop.game_data.export_texture(workshop,name,workshop.OUT/'卡牌横幅与记牌器小图')
                file=workshop.OUT/'卡牌横幅与记牌器小图'/('log_'+card['art']+'.png')
                assert file.is_file()
                assert workshop.current_png(name,file)[:8]==b'\x89PNG\r\n\x1a\n'
                results.append({'id':card['id'],'thumbnail':True})
            except Exception:
                results.append({'id':card['id'],'error':traceback.format_exc()})
        try:
            slots=workshop.slots(workshop.cards[0])
            for label,name,file in slots:
                if file.suffix!='.wem':
                    workshop.dimensions(name,file)
                    workshop.current_png(name,file)
            results.append({'editorSlots':len(slots)})
            workshop.stages.cover(workshop.stages.ids[0])
            results.append({'background':True})
        except Exception:
            results.append({'editorError':traceback.format_exc()})
        (workshop.DATA/'resource-test.json').write_text(json.dumps(results,ensure_ascii=False),'utf8')
        return
    server=workshop.ThreadingHTTPServer(('127.0.0.1',0),workshop.Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    class Startup:
        def __init__(self):
            self.shown=False
            self.guard=threading.Lock()
            self.transfer_guard=threading.Lock()
            self.transfer_state=dict(percent=0,label='')
        def guide(self):
            folder = workshop.ROOT if getattr(sys,'frozen',False) else workshop.ROOT.parent
            return dict(text=(folder/'初次使用指南.md').read_text('utf-8-sig'),
                        hidden=(workshop.DATA/'guide-hidden').is_file())

        def set_guide_hidden(self, hidden):
            marker = workshop.DATA/'guide-hidden'
            if hidden:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)

        def transfer_progress(self):
            return self.transfer_state
        def transfer(self, action, groups, single):
            from config_pack import execute
            if not self.transfer_guard.acquire(blocking=False):
                raise ValueError('已有任务正在执行')
            try:
                def status(percent,label):
                    self.transfer_state=dict(percent=round(percent),label=label)
                status(0,'正在准备')
                path=None
                if action in ('export','import'):
                    save=action=='export'
                    mode=webview.FileDialog.SAVE if save else webview.FileDialog.OPEN
                    selected=window.create_file_dialog(mode,save_filename=(str(single or '影之工坊配置')+'.zip') if save else '',
                        file_types=('影之工坊 ZIP (*.zip)',) if save else ('影之工坊 ZIP (*.zip)','旧版配置 (*.svatelier)'))
                    if not selected:return dict(cancelled=True)
                    path=Path(selected if isinstance(selected,str) else selected[0])
                    if save and not path.suffix:path=path.with_suffix('.zip')
                result=execute(workshop,action,groups,single,path,status)
                if action in ("import","restore") and workshop.P is not None:
                    workshop.refresh_card_names(workshop.assets[workshop.MASTER][0].read_bytes())
                status(100,result)
                return dict(message=result)
            except Exception as error:
                return dict(error=str(error))
            finally:
                self.transfer_guard.release()
        def message_box(self,message,confirm):
            flags=0x21 if confirm else 0x40
            result=ctypes.windll.user32.MessageBoxW(ctypes.c_void_p(window.native.Handle.ToInt64()),str(message),'影之工坊',flags)
            return result==1
        def choose_directory(self,current):
            candidate=Path(current) if current else Path.home()
            if not candidate.is_dir():candidate=Path.home()
            result=window.create_file_dialog(webview.FileDialog.FOLDER,directory=str(candidate))
            return result[0] if result else None
        def ready(self):
            with self.guard:
                if self.shown:return
                self.shown=True
            from System import Action
            def reveal():
                window.native.Opacity=1
            window.native.Invoke(Action(reveal))
            window.show()
    startup=Startup()
    window=webview.create_window('影之工坊',f'http://127.0.0.1:{server.server_port}/{workshop.TOKEN}/',
        width=1440,height=960,min_size=(1000,700),background_color='#FFFFFF',hidden=False,js_api=startup)
    def prepare_window():
        # Keep WebView2 rendering during startup while the window is visually hidden.
        window.native.Opacity=.01
    window.events.before_show += prepare_window
    def fallback():
        timer=threading.Timer(12,startup.ready)
        timer.daemon=True
        timer.start()
    window.events.loaded += fallback
    if '--smoke-test' in sys.argv:
        def verify():
            import time,json
            started=time.monotonic()
            while time.monotonic()-started<20:
                if window.evaluate_js('window.heroReady===true'):break
                time.sleep(.1)
            try:
                report=window.evaluate_js("JSON.stringify({title:document.title,heading:document.querySelector('h1')?.innerText,settings:typeof preferences,cards:cards.length,heroReady:window.heroReady===true,playerLoaded:!!homeLeaderPlayer?.skeleton})")
                window.evaluate_js("window.testPlayer=homeLeaderPlayer; main.innerHTML='<p>测试</p>'")
                time.sleep(.2)
                window.evaluate_js('home()')
                time.sleep(.2)
                retained=window.evaluate_js('window.testPlayer===homeLeaderPlayer&&!!homeLeaderHost?.isConnected')
                details=json.loads(report)
                details['playerRetained']=retained
                details['gamePath']=workshop.settings().get('gamePath','')
                details['backupPath']=workshop.settings()['path']
                details['startupSeconds']=round(time.monotonic()-started,2)
                details['windowShown']=startup.shown
                (workshop.DATA/'smoke-result.json').write_text(json.dumps(details,ensure_ascii=False),'utf8')
            except Exception:
                (workshop.DATA/'smoke-error.log').write_text(traceback.format_exc(),'utf8')
            finally:
                window.destroy()
        window.events.loaded += lambda:threading.Thread(target=verify,daemon=True).start()
    if '--background-test' in sys.argv:
        def verify_background():
            import time,json
            started=time.monotonic()
            def wait(script,timeout=180):
                limit=time.monotonic()+timeout
                while time.monotonic()<limit:
                    try:
                        if window.evaluate_js(script):return
                    except Exception:pass
                    time.sleep(.1)
                raise TimeoutError(script)
            report={}
            try:
                wait('typeof cards!=="undefined"&&cards.length>0',60)
                window.evaluate_js('category(1)')
                wait("view==='stages'&&main.querySelectorAll('.stage-grid button').length>0")
                report['stageButtons']=window.evaluate_js("main.querySelectorAll('.stage-grid button').length")
                results=[]
                for stage in (1,3,501):
                    begin=time.monotonic();window.evaluate_js(f'openStage({stage})')
                    wait("view==='stage'&&!!main.querySelector('#stageCanvas')&&!main.querySelector('#file').disabled")
                    width=window.evaluate_js("main.querySelector('#stageCanvas').width")
                    if not width:raise AssertionError('画布尺寸为空')
                    results.append(dict(stage=stage,simpleReady=round(time.monotonic()-begin,2)))
                    if stage==1:
                        window.evaluate_js("main.querySelector('#mode').selectedIndex=1;main.querySelector('#mode').dispatchEvent(new Event('change'))")
                        wait("main.querySelector('#layer').options.length>0&&!main.querySelector('#file').disabled")
                        results[-1]['normalMaterialReady']=True
                    window.evaluate_js('back()');wait("view==='stages'")
                report['results']=results;report['ok']=True
            except Exception:
                report['ok']=False;report['error']=traceback.format_exc()
            finally:
                report['elapsed']=round(time.monotonic()-started,2)
                (workshop.DATA/'background-test.json').write_text(json.dumps(report,ensure_ascii=False),'utf8')
                window.destroy()
        window.events.loaded += lambda:threading.Thread(target=verify_background,daemon=True).start()
    if '--voice-test' in sys.argv:
        def verify_voice():
            import time,json
            started=time.monotonic()
            def wait(script,timeout=240):
                limit=time.monotonic()+timeout
                while time.monotonic()<limit:
                    try:
                        if window.evaluate_js(script):return
                    except Exception:pass
                    time.sleep(.1)
                raise TimeoutError(script)
            report={}
            try:
                wait('typeof cards!=="undefined"&&cards.length>0',120)
                results={}
                for cid in (11032110,10734110,10754110,10001110,90044110):
                    index=window.evaluate_js(f'cards.findIndex(c=>String(c.id)===String({cid}))')
                    if index is None or index<0:raise AssertionError(f'找不到卡牌 {cid}')
                    window.evaluate_js(f'edit({index})')
                    wait("view==='edit'&&document.querySelectorAll('.voice-columns .slot b').length>0")
                    rows=json.loads(window.evaluate_js("JSON.stringify([...document.querySelectorAll('.voice-columns section')].flatMap(sec=>[...sec.querySelectorAll('.slot b')].map(node=>({column:sec.querySelector('h3').textContent,label:node.textContent}))))"))
                    play=[row['label'] for row in rows if row['label'].split(' · ')[0]=='出牌时']
                    results[str(cid)]=dict(rows=rows,playLabels=play,duplicate=len(play)-len(set(play)))
                    if results[str(cid)]['duplicate']:raise AssertionError(f'{cid} 仍有两个同名出牌时')
                    window.evaluate_js('back()')
                    wait("view==='library'")
                report['results']=results;report['ok']=True
            except Exception:
                report['ok']=False;report['error']=traceback.format_exc()
            finally:
                report['elapsed']=round(time.monotonic()-started,2)
                (workshop.DATA/'voice-test.json').write_text(json.dumps(report,ensure_ascii=False),'utf8')
                window.destroy()
        window.events.loaded += lambda:threading.Thread(target=verify_voice,daemon=True).start()
    if '--variant-voice-test' in sys.argv:
        def verify_variant_voice():
            import time,json
            started=time.monotonic()
            def wait(script,timeout=240):
                limit=time.monotonic()+timeout
                while time.monotonic()<limit:
                    try:
                        if window.evaluate_js(script):return
                    except Exception:pass
                    time.sleep(.1)
                raise TimeoutError(script)
            report={}
            try:
                wait('typeof cards!=="undefined"&&cards.length>0',120)
                index=window.evaluate_js('cards.findIndex(c=>String(c.id)===String(11024110))')
                if index is None or index<0:raise AssertionError('找不到表里侦探')
                window.evaluate_js(f'edit({index})')
                wait("view==='edit'&&document.querySelectorAll('.voice-columns .slot b').length>0")
                window.evaluate_js('askVoice(1)')
                wait("document.querySelector('dialog')?.open===true&&document.querySelector('dialog').innerText.includes('同步替换异画音频')")
                report['dialog']=window.evaluate_js("document.querySelector('dialog').innerText")
                report['buttons']=json.loads(window.evaluate_js("JSON.stringify([...document.querySelectorAll('dialog button')].map(b=>b.textContent))"))
                window.evaluate_js('modal.close()')
                window.evaluate_js('useVariant(1,null,true)')
                wait("!!document.querySelector('.field-status')",180)
                report['status']=window.evaluate_js("document.querySelector('.field-status').innerText")
                report['changed']=window.evaluate_js('Object.keys(changes).length')
                report['marked']=json.loads(window.evaluate_js("JSON.stringify([...document.querySelectorAll('.voice-columns .slot b')].map(n=>n.textContent).filter(t=>t.endsWith(' · 待写入')))"))
                report['previews']=json.loads(window.evaluate_js("JSON.stringify([...document.querySelectorAll('.voice-columns audio')].map(a=>a.src).filter(s=>s.startsWith('blob:')))"))
                report['ok']=('条异画语音' in report['status'])and report['changed']>=10 and len(report['marked'])>=10 and len(report['previews'])>=10
            except Exception:
                report['ok']=False;report['error']=traceback.format_exc()
            finally:
                report['elapsed']=round(time.monotonic()-started,2)
                (workshop.DATA/'variant-voice-test.json').write_text(json.dumps(report,ensure_ascii=False),'utf8')
                window.destroy()
        window.events.loaded += lambda:threading.Thread(target=verify_variant_voice,daemon=True).start()
    if '--header-test' in sys.argv:
        def verify_header():
            import time,json
            started=time.monotonic()
            report={}
            def wait(script,timeout=120):
                limit=time.monotonic()+timeout
                while time.monotonic()<limit:
                    try:
                        if window.evaluate_js(script):return
                    except Exception:pass
                    time.sleep(.1)
                raise TimeoutError(script)
            try:
                wait('typeof cards!=="undefined"&&cards.length>0')
                report['position']=window.evaluate_js("getComputedStyle(document.querySelector('header')).position")
                report['headerBg']=window.evaluate_js("getComputedStyle(document.querySelector('header')).backgroundColor")
                report['strips']=window.evaluate_js("document.querySelectorAll('#veil i').length")
                report['veilBg']=window.evaluate_js("getComputedStyle(document.querySelector('#veil')).backgroundImage")
                report['veilHeight']=window.evaluate_js("Math.round(document.querySelector('#veil').getBoundingClientRect().height)")
                window.evaluate_js('category(0)')
                wait('document.querySelectorAll(".grid .card").length>0')
                window.evaluate_js('scrollTo(0,700)')
                wait('Math.round(scrollY)>0',30)
                time.sleep(.4)
                report['scrollY']=window.evaluate_js('Math.round(scrollY)')
                report['headerTop']=window.evaluate_js("Math.round(document.querySelector('header').getBoundingClientRect().top)")
                report['headerHeight']=window.evaluate_js("Math.round(document.querySelector('header').getBoundingClientRect().height)")
                report['veilOn']=window.evaluate_js("document.querySelector('#veil').classList.contains('on')")
                report['veilOpacity']=window.evaluate_js("getComputedStyle(document.querySelector('#veil')).opacity")
                report['veilTop']=window.evaluate_js("Math.round(document.querySelector('#veil').getBoundingClientRect().top)")
                report['veilGap']=window.evaluate_js("getComputedStyle(document.querySelector('#veil')).left")
                report['borderBottom']=window.evaluate_js("getComputedStyle(document.querySelector('header')).borderBottomWidth")
                report['ok']=bool(report['position']=='sticky' and report['headerTop']==0 and report['scrollY']>0 and report['veilOn'] and float(report['veilOpacity'])>0.9 and report['strips']==0 and 'gradient' in report['veilBg'] and report['veilHeight']>=report['headerHeight']+60 and report['headerBg']=='rgb(255, 255, 255)' and report['veilTop']==0 and report['borderBottom']=='0px')
                if not report['ok']:raise AssertionError('顶栏未固定或过渡失效')
            except Exception:
                report['ok']=False;report['error']=traceback.format_exc()
            finally:
                report['elapsed']=round(time.monotonic()-started,2)
                (workshop.DATA/'header-test.json').write_text(json.dumps(report,ensure_ascii=False),'utf8')
                window.destroy()
        window.events.loaded += lambda:threading.Thread(target=verify_header,daemon=True).start()
    try:
        webview.start(gui='edgechromium',debug=False,private_mode=False,
            storage_path=str(workshop.DATA/'浏览器缓存'),icon=str(workshop.ROOT/'icon.ico'))
    finally:
        server.shutdown()
        server.server_close()

if __name__=='__main__':
    multiprocessing.freeze_support()
    try:
        main()
    except Exception:
        detail=traceback.format_exc()
        directory=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SZBCraft'
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'startup-error.log').write_text(detail,'utf8')
        ctypes.windll.user32.MessageBoxW(None,'启动失败，请确认已安装 Microsoft Edge WebView2 运行时。\n\n'+detail[-1600:],'影之工坊',0x10)
