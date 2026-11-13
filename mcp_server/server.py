import os,time,asyncio
from mcp.server.fastmcp import FastMCP,Context
from mcp.server.transport_security import TransportSecuritySettings
from app.config import Settings
from app.store import Store
from app.domain import Domain,now
from app.security import verify_actor
from app.observability import emit

cfg=Settings();cfg.validate();domain=Domain(Store(cfg))
mcp=FastMCP('RoleOpsAccess',host=os.getenv('MCP_BIND','127.0.0.1'),port=int(os.getenv('MCP_PORT','8001')),json_response=True,stateless_http=True,transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,allowed_hosts=['127.0.0.1:*','localhost:*','mcp:*'],allowed_origins=['http://localhost:*','http://127.0.0.1:*']))

@mcp.tool()
async def create_access_request(system_id:str,reason:str,idempotency_key:str,ctx:Context)->dict:
    """建立軟體系統存取申請。呼叫身分只接受可信 HTTP 上下文，核准不代表開通。"""
    request=ctx.request_context.request
    if request is None:raise ValueError('HTTP identity required')
    claims=verify_actor(request.headers.get('x-roleops-actor',''),cfg.mcp_secret)
    result=domain.create_request(claims['sub'],system_id,reason,idempotency_key,claims['trace_id'])
    emit(cfg,'mcp','request_created','success',claims['trace_id'],request_id=result['id'])
    fault,_=domain.db.get('fault:mcp')
    if cfg.fault_enabled and fault and fault['expires']>now():await asyncio.sleep(fault['delay'])
    return result

if __name__=='__main__':mcp.run(transport='streamable-http')
