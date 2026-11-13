"""Fixed RoleOps CloudWatch queries and an administrator-only snapshot."""
from datetime import datetime, timezone
import json
import math
import re
import time

from .showcase_content import atomic_json

WINDOWS = {'1h': (3600, 60), '24h': (86400, 300)}
ALARM_NAMES = (
    'roleops-ec2-statuscheck', 'roleops-index-failure', 'roleops-mcp-timeout',
    'roleops-selfcheck-heartbeat',
)
CASES = (
    dict(id='ROLEOPS-BUG-20261026-001', date='2026-10-26', category='來源治理',
         title='g5 來源配對與索引驗證', status='隔離驗證通過（A）',
         impact='舊交接規則綁定 g3，g5 來源配對缺少完整核對。',
         action='補上配對收據、回復 VersionId 和索引檢查。',
         verification='隔離來源循環通過。原事件時點的正式交易與同鍵讀回仍待驗。',
         source='AI 問答與來源治理故障調查及修復紀錄，第 15 節'),
    dict(id='ROLEOPS-BUG-20261024-003', date='2026-10-24', category='監控',
         title='MCP 逾時告警與稀疏資料', status='配置與寄信分項追蹤',
         impact='逾時只在事件發生時送點，無資料時需要能預設告警。',
         action='補齊預建及唯讀查回步驟，分開記錄指標、配置與通知。',
         verification='原事件記錄告警建立受理的使用者回報。自然逾時與實際寄信另行觀測。',
         source='Bug 調查與修復報告，Step 19.3 監控建置驗收'),
    dict(id='ROLEOPS-BUG-20261022-002', date='2026-10-22', category='入口',
         title='CloudFront HTTPS 入口恢復', status='入口恢復（B）',
         impact='CloudFront 回源驗證設定不一致，網站入口無法正常載入。',
         action='同步既有回源驗證設定，重新查 HTTPS 入口。',
         verification='使用者回報狀態 API 和登入頁恢復，驗證範圍為 HTTPS 入口。',
         source='Bug 調查與修復報告，CloudFront 入口事件'),
)


def metric_specs(namespace, instance):
    def custom(name, component, task, stat='Sum'):
        return namespace, name, {'Component': component, 'TaskType': task}, stat

    return {
        'heartbeat': custom('SelfCheckHeartbeat', 'app', 'self_check'),
        'ec2': ('AWS/EC2', 'StatusCheckFailed', {'InstanceId': instance}, 'Maximum'),
        'queue': ('AWS/SQS', 'ApproximateAgeOfOldestMessage',
                  {'QueueName': 'roleops-document-events'}, 'Maximum'),
        'dlq': ('AWS/SQS', 'ApproximateNumberOfMessagesVisible',
                {'QueueName': 'roleops-document-dlq'}, 'Maximum'),
        'index_ok': custom('IndexUpdateSuccess', 'indexer', 'index_update'),
        'index_failure': custom('IndexUpdateFailure', 'indexer', 'index_update'),
        'ai_ok': custom('TaskSuccess', 'app', 'query'),
        'input_tokens': custom('InputTokens', 'app', 'query'),
        'output_tokens': custom('OutputTokens', 'app', 'query'),
        'mcp_ok': custom('TaskSuccess', 'app', 'access_request'),
        'mcp_ms': custom('MCPLatency', 'app', 'access_request', 'Average'),
    }


def read_window(client, specs, now, span, period):
    end = int(now) // period * period
    start = end - span
    queries = [
        {'Id': key, 'MetricStat': {
            'Metric': {'Namespace': ns, 'MetricName': name, 'Dimensions': [
                {'Name': dimension, 'Value': value} for dimension, value in dimensions.items()
            ]}, 'Period': period, 'Stat': stat}}
        for key, (ns, name, dimensions, stat) in specs.items()
    ]
    series = {key: {'status': 'error', 'points': []} for key in specs}
    try:
        response = client.get_metric_data(
            StartTime=datetime.fromtimestamp(start, timezone.utc),
            EndTime=datetime.fromtimestamp(end, timezone.utc),
            MetricDataQueries=queries,
        )
        # A normal query returns at most 3,168 points. Never present a truncated result as complete.
        if not response.get('NextToken'):
            for row in response.get('MetricDataResults', []):
                key = row.get('Id')
                timestamps, values = row.get('Timestamps', []), row.get('Values', [])
                if key not in series or row.get('StatusCode') != 'Complete' or len(timestamps) != len(values):
                    continue
                points = sorted(
                    [int(at.timestamp()), value] for at, value in zip(timestamps, values)
                    if start <= at.timestamp() < end and math.isfinite(value)
                )
                series[key] = {'status': 'ok' if points else 'empty', 'points': points}
    except Exception:
        # The API returns finite status labels only. AWS errors may contain resource identifiers.
        pass
    return dict(start=start, end=end, period=period, series=series)


def refresh(cfg, client, instance, now=time.time):
    if not re.fullmatch(r'i-[0-9a-f]{17}', instance):
        return
    sampled_at = now()
    specs = metric_specs(cfg.metric_namespace, instance)
    windows = {key: read_window(client, specs, sampled_at, *params)
               for key, params in WINDOWS.items()}
    alarms = dict.fromkeys(ALARM_NAMES, 'unavailable')
    try:
        response = client.describe_alarms(AlarmNames=list(ALARM_NAMES))
        if not response.get('NextToken'):
            alarms = dict.fromkeys(ALARM_NAMES, 'missing')
            for row in response.get('MetricAlarms', []):
                name, state = row.get('AlarmName'), row.get('StateValue')
                if name in alarms and state in ('OK', 'ALARM', 'INSUFFICIENT_DATA'):
                    alarms[name] = state
    except Exception:
        pass
    atomic_json(cfg.root / 'maintenance-dashboard.json',
                dict(sampled_at=sampled_at, windows=windows, alarms=alarms))


def snapshot(cfg, window, clock=time.time):
    try:
        data = json.loads((cfg.root / 'maintenance-dashboard.json').read_bytes())
        sampled_at = data['sampled_at']
        age = clock() - sampled_at
        if age < 0 or age > 900:
            return dict(status='stale', sampled_at=sampled_at)
        return dict(status='fresh', sampled_at=sampled_at,
                    metrics=data['windows'][window], alarms=data['alarms'])
    except (OSError, ValueError, KeyError, TypeError):
        return dict(status='unavailable')
