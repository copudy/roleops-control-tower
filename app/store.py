"""CAS transactions in SQLite (local) and the existing DynamoDB table (AWS)."""
import json, sqlite3, threading
from pathlib import Path
from contextlib import contextmanager

class Conflict(Exception): pass

class Store:
    def __init__(self,settings):
        self.settings=settings; self.lock=threading.RLock()
        if settings.mode=='local':
            self.path=settings.root/'roleops.sqlite3'; self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connection() as db: db.execute('CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL)')
        else:
            import boto3
            self.table=boto3.resource('dynamodb',region_name=settings.region).Table(settings.table)
            self.client=self.table.meta.client
    @contextmanager
    def connection(self):
        db=sqlite3.connect(self.path,timeout=20);db.execute('PRAGMA journal_mode=WAL')
        try: yield db;db.commit()
        except: db.rollback();raise
        finally: db.close()
    def get(self,key):
        if self.settings.mode=='local':
            with self.connection() as db: row=db.execute('SELECT value,version FROM records WHERE key=?',(key,)).fetchone()
            return (json.loads(row[0]),row[1]) if row else (None,0)
        row=self.table.get_item(Key={'PK':key,'SK':'DATA'},ConsistentRead=True).get('Item')
        return (json.loads(row['payload']),int(row['version'])) if row else (None,0)
    def scan(self,prefix=''):
        if self.settings.mode=='local':
            with self.connection() as db: rows=db.execute('SELECT key,value,version FROM records').fetchall()
            return [(k,json.loads(v),n) for k,v,n in rows if k.startswith(prefix)]
        rows=[];args={'ConsistentRead':True}
        while True:
            result=self.table.scan(**args)
            rows.extend((x['PK'],json.loads(x['payload']),int(x['version'])) for x in result.get('Items',[]) if x.get('SK')=='DATA' and x['PK'].startswith(prefix))
            if 'LastEvaluatedKey' not in result:break
            args['ExclusiveStartKey']=result['LastEvaluatedKey']
        return rows
    def transact(self,changes):
        # (key, replacement-or-None, expected_version). No blind overwrites.
        if not changes:return
        if len(changes)>90:raise ValueError('Transaction too large')
        if len({x[0] for x in changes}) != len(changes):raise ValueError('Duplicate transaction key')
        if self.settings.mode=='local':
            with self.lock,self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                for key,value,version in changes:
                    row=db.execute('SELECT version FROM records WHERE key=?',(key,)).fetchone()
                    if (row[0] if row else 0)!=version:raise Conflict(key)
                for key,value,version in changes:
                    if value is None:db.execute('DELETE FROM records WHERE key=?',(key,))
                    else:db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(key,json.dumps(value,ensure_ascii=False),version+1))
            return
        from botocore.exceptions import ClientError
        items=[]
        for key,value,version in changes:
            condition='attribute_not_exists(PK)' if version==0 else '#v=:v'
            args={'TableName':self.settings.table,'ConditionExpression':condition}
            if version:args.update(ExpressionAttributeNames={'#v':'version'},ExpressionAttributeValues={':v':version})
            if value is None:items.append({'Delete':dict(args,Key={'PK':key,'SK':'DATA'})})
            else:items.append({'Put':dict(args,Item={'PK':key,'SK':'DATA','payload':json.dumps(value,ensure_ascii=False),'version':version+1})})
        try:self.client.transact_write_items(TransactItems=items)
        except ClientError as e:
            if e.response['Error']['Code'] in ('TransactionCanceledException','ConditionalCheckFailedException'):raise Conflict('Concurrent update') from e
            raise
