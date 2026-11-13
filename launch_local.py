"""Local-only launcher: never creates AWS resources or calls Bedrock."""
import os,sys,subprocess,secrets,time
from pathlib import Path
from dotenv import load_dotenv
def main():
 root=Path(__file__).resolve().parent;os.chdir(root);envfile=root/'.env.local'
 if not envfile.exists():envfile.write_text('ROLEOPS_MODE=local\nMCP_SHARED_SECRET='+secrets.token_hex(32)+'\nLANGSMITH_TRACING=false\nENABLE_FAULT_INJECTION=true\n',encoding='utf-8')
 load_dotenv(envfile,override=True);os.environ['ROLEOPS_MODE']='local';os.environ['ROLEOPS_DATA']=str(root/'runtime');os.environ['KNOWLEDGE_ROOT']=str(root/'knowledge');os.environ['PYTHONUTF8']='1'
 os.environ['EMBED_PROVIDER']='default'
 os.environ['GENERATION_PROVIDER']='bedrock'
 commands=[['-m','mcp_server.server'],['-m','indexer.worker'],['-m','uvicorn','app.web:app','--host','127.0.0.1','--port','8000','--no-access-log']];processes=[];streams=[];(root/'runtime').mkdir(exist_ok=True)
 try:
  for name,args in zip(['mcp','indexer','web'],commands):
   stream=(root/'runtime'/f'{name}.log').open('a',encoding='utf-8');streams.append(stream);processes.append(subprocess.Popen([sys.executable,*args],stdout=stream,stderr=stream,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
  print('RoleOps 本機測試：http://localhost:8000；資料保存在 runtime。按 Ctrl+C 停止服務。')
  while True:
   for process in processes:
    if process.poll() is not None:raise RuntimeError('服務停止；請查 runtime 日誌。')
   time.sleep(1)
 except KeyboardInterrupt:print('停止服務。')
 finally:
  for process in processes:
   if process.poll() is None:process.terminate()
  for process in processes:process.wait(timeout=10)
  for stream in streams:stream.close()
if __name__=='__main__':main()
