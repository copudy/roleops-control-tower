import argparse
from app.config import Settings
from app.store import Store
from app.domain import now

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True);t=sub.add_parser('timeout');t.add_argument('--delay',type=int,default=8);t.add_argument('--ttl',type=int,default=60);sub.add_parser('clear');args=p.parse_args();cfg=Settings();cfg.validate()
 if not cfg.fault_enabled:raise SystemExit('ENABLE_FAULT_INJECTION=false')
 db=Store(cfg);_,v=db.get('fault:mcp')
 if args.action=='clear':db.transact([('fault:mcp',None,v)])
 else:
  if not 1<=args.delay<=15 or not 1<=args.ttl<=300:raise SystemExit('Delay 1–15s; TTL 1–300s')
  db.transact([('fault:mcp',{'delay':args.delay,'expires':now()+args.ttl},v)])
 print('Fault control updated')
if __name__=='__main__':main()
