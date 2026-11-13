import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]));os.environ['ROLEOPS_TEST']='true';os.environ['AWS_ACCESS_KEY_ID']='testing';os.environ['AWS_SECRET_ACCESS_KEY']='testing';os.environ['AWS_DEFAULT_REGION']='us-east-1'
import pytest
from app.config import Settings,PERMISSIONS
from app.store import Store
from app.domain import Domain
@pytest.fixture
def env(tmp_path):
 cfg=Settings(mode='local',root=tmp_path,knowledge=Path(__file__).resolve().parents[1]/'knowledge',mcp_secret='test-secret-'*6);cfg.validate();db=Store(cfg);d=Domain(db)
 admin=d.create_user(None,'admin','管理員','平台','管理員','supervisor','secure-password-123',list(PERMISSIONS),bootstrap=True);a=d.user(admin['id']);employee=d.create_user(a,'employee','王小明','客服部','客服專員','support','secure-password-123');e=d.user(employee['id']);agent=d.save_agent(a,None,dict(name='小智',avatar='🦉',profile='support',prompt_version='A'));d.assign(a,e['id'],agent['id']);e=d.user(e['id']);return cfg,db,d,a,e
