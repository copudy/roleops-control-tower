from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from fastapi import FastAPI

from app.domain import Denied, Domain, Invalid
from app.extension import mount
from app.maintenance_dashboard import refresh, snapshot


class CloudWatch:
    def __init__(self):
        self.calls = []

    def get_metric_data(self, **request):
        self.calls.append(request)
        at = request['StartTime']
        return {'MetricDataResults': [
            dict(Id='ec2', StatusCode='Complete', Timestamps=[at], Values=[0]),
            dict(Id='index_failure', StatusCode='Complete', Timestamps=[], Values=[]),
            dict(Id='mcp_ms', StatusCode='PartialData', Timestamps=[at], Values=[10]),
        ]}

    def describe_alarms(self, **request):
        self.alarm_request = request
        return {'MetricAlarms': [
            dict(AlarmName='roleops-ec2-statuscheck', StateValue='OK'),
            dict(AlarmName='unrelated-alarm', StateValue='ALARM'),
        ]}


class MonitoringTests(unittest.TestCase):
    def test_snapshot_preserves_zero_gaps_and_errors(self):
        with TemporaryDirectory(dir=Path.cwd() if os.name == 'nt' else None) as folder:
            cfg = SimpleNamespace(root=Path(folder), metric_namespace='RoleOps')
            client = CloudWatch()
            now = datetime(2026, 11, 4, 8, 30, tzinfo=timezone.utc).timestamp()
            self.assertEqual(snapshot(cfg, '1h')['status'], 'unavailable')
            refresh(cfg, client, 'i-0af28bbdd1ce0240d', now=lambda: now)
            data = snapshot(cfg, '1h', clock=lambda: now + 1)
            self.assertEqual(data['status'], 'fresh')
            rows = data['metrics']['series']
            self.assertEqual(rows['ec2']['points'][0][1], 0)
            self.assertEqual(rows['index_failure']['status'], 'empty')
            self.assertEqual(rows['mcp_ms'], {'status': 'error', 'points': []})
            self.assertEqual(data['alarms']['roleops-selfcheck-heartbeat'], 'missing')
            self.assertNotIn('unrelated-alarm', data['alarms'])
            self.assertEqual(snapshot(cfg, '24h', clock=lambda: now + 1)['metrics']['period'], 300)
            self.assertEqual(snapshot(cfg, '1h', clock=lambda: now + 901)['status'], 'stale')
            for request in client.calls:
                queries = {row['Id']: row['MetricStat'] for row in request['MetricDataQueries']}
                self.assertEqual(queries['mcp_ms']['Stat'], 'Average')
                self.assertIn({'Name': 'TaskType', 'Value': 'access_request'},
                              queries['mcp_ok']['Metric']['Dimensions'])

    def test_new_routes_require_both_permissions(self):
        app = FastAPI()
        roles = {'admin': ['ops:read', 'people:manage'], 'ops': ['ops:read']}

        def auth(request):
            role = request.role
            if role not in roles:
                raise Denied('Unauthenticated')
            return {'permissions': roles[role]}

        with TemporaryDirectory(dir=Path.cwd() if os.name == 'nt' else None) as folder:
            mount(app, auth, Domain(None), None, SimpleNamespace(root=Path(folder)))
            routes = {route.path: route.endpoint for route in app.routes}
            for path in ('/api/engineering/monitoring', '/api/engineering/incidents'):
                for role in (None, 'ops'):
                    with self.assertRaises(Denied):
                        routes[path](SimpleNamespace(role=role))
                self.assertTrue(routes[path](SimpleNamespace(role='admin')))
            with self.assertRaises(Invalid):
                routes['/api/engineering/monitoring'](SimpleNamespace(role='admin'), '7d')
            self.assertFalse(routes['/api/selfcheck'](SimpleNamespace(role='ops'))['fresh'])
