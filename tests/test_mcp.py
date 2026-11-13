import asyncio,os,sys,subprocess,socket,time,json
from dataclasses import replace
import pytest
from app.agent import Agent
from app.knowledge import Knowledge
from app.security import sign_actor
from app.domain import now
def test_real_mcp_http_identity_idempotency_and_timeout(env):
 cfg,db,d,a,e=env
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 cfg=replace(cfg,mcp_url=f'http://127.0.0.1:{port}/mcp',timeout=2,fault_enabled=True)
 environ=dict(os.environ,ROLEOPS_MODE='local',ROLEOPS_DATA=str(cfg.root),KNOWLEDGE_ROOT=str(cfg.knowledge),MCP_SHARED_SECRET=cfg.mcp_secret,MCP_PORT=str(port),ENABLE_FAULT_INJECTION='true',LANGSMITH_TRACING='false')
 from pathlib import Path
 p=subprocess.Popen([sys.executable,'-m','mcp_server.server'],cwd=Path(__file__).resolve().parents[1],env=environ,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  for _ in range(100):
   try:
    with socket.create_connection(('127.0.0.1',port),.2):break
   except OSError:time.sleep(.1)
  else:raise AssertionError('MCP process did not start')
  from app.ops import check_mcp
  assert asyncio.run(check_mcp(cfg))['tools']==['create_access_request']
  agent=Agent(cfg,d,Knowledge(cfg,db));r=asyncio.run(agent.call_tool(e,'crm','工作使用','real-mcp-key1'));assert r['status']=='pending';assert asyncio.run(agent.call_tool(e,'crm','工作使用','real-mcp-key1'))['id']==r['id']
  async def forged():
   from mcp import ClientSession
   from mcp.client.streamable_http import streamablehttp_client
   async with streamablehttp_client(cfg.mcp_url,headers={'x-roleops-actor':'forged'}) as (read,write,_):
    async with ClientSession(read,write) as session:
     await session.initialize();result=await session.call_tool('create_access_request',{'system_id':'crm','reason':'偽造','idempotency_key':'forged-key1'});return result
  assert asyncio.run(forged()).isError
  db.transact([('fault:mcp',{'delay':5,'expires':now()+60},0)])
  result=asyncio.run(agent.call_tool(e,'crm','逾時示範','real-mcp-key2'));assert result['dispatch_status']=='uncertain'
  _,v=db.get('fault:mcp');db.transact([('fault:mcp',None,v)])
  retry=asyncio.run(agent.call_tool(e,'crm','逾時示範','real-mcp-key2'));assert retry['status']=='pending';assert len(db.scan('request:'))==2
 finally:p.terminate();p.wait(timeout=10)
