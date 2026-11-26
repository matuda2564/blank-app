import subprocess,sys,time,os,re,urllib.request
env=dict(os.environ,MC_IDLE_EXIT="8",MC_WORK="/tmp/crash2/work_idle")
code='''
import sys,webbrowser
webbrowser.open=lambda u,*a,**k: print("URL "+u,flush=True)
sys.path.insert(0,".")
import launcher; launcher.main()
'''
p=subprocess.Popen(["/tmp/mc/venv/bin/python","-W","ignore","-c",code],cwd="/tmp/ghw/blank-app/mitsumori",env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
url=None
for _ in range(60):
    l=p.stdout.readline()
    if l.startswith("URL "): url=l[4:].strip(); break
print("URL取得:",bool(url))
port=re.search(r":(\d+)/",url).group(1); tok=re.search(r"t=([^&#]+)",url).group(1)
def hb():
    r=urllib.request.Request(f"http://127.0.0.1:{port}/api/state",headers={"X-MC-Token":tok}); urllib.request.urlopen(r,timeout=5).read()
t0=time.time()
for _ in range(7): hb(); time.sleep(2)   # 14秒間、画面が開いている
print("画面が開いている間(14秒>8秒の期限)も、動いている:",p.poll() is None)
t1=time.time()
while p.poll() is None and time.time()-t1<40: time.sleep(1)
print("画面が閉じて、約%d秒後に、自動で終了した: "%(time.time()-t1), p.poll() is not None, "(終了コード",p.poll(),")")
