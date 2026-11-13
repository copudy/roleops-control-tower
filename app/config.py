import os
from pathlib import Path
from dataclasses import dataclass

@dataclass
class Settings:
    mode: str = os.getenv('ROLEOPS_MODE', 'local')
    public_origin: str = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    origin_secret: str = os.getenv('ORIGIN_SHARED_SECRET', '')
    region: str = os.getenv('AWS_REGION', 'us-east-1')
    table: str = os.getenv('DDB_TABLE', 'roleops-main')
    bucket: str = os.getenv('S3_BUCKET', '')
    queue: str = os.getenv('SQS_QUEUE_URL', '')
    root: Path = Path(os.getenv('ROLEOPS_DATA', './runtime'))
    knowledge: Path = Path(os.getenv('KNOWLEDGE_ROOT', './knowledge'))
    mcp_url: str = os.getenv('MCP_URL', 'http://127.0.0.1:8001/mcp')
    mcp_secret: str = os.getenv('MCP_SHARED_SECRET', '')
    model: str = os.getenv('BEDROCK_MODEL_ID', 'amazon.nova-micro-v1:0')
    generation_provider: str = os.getenv('GENERATION_PROVIDER', 'groq')
    groq_model: str = os.getenv('GROQ_MODEL_ID', 'openai/gpt-oss-20b')
    groq_key_file: Path = Path(os.getenv('GROQ_API_KEY_FILE', '/run/secrets/groq_api_key'))
    generation_timeout: float = float(os.getenv('GENERATION_TIMEOUT_SECONDS', '45'))
    metrics_enabled: bool = os.getenv('ROLEOPS_METRICS_ENABLED','true').lower()=='true'
    metric_namespace: str = os.getenv('METRIC_NAMESPACE', 'RoleOps')
    embed_provider: str = os.getenv('EMBED_PROVIDER', 'e5')
    e5_model_dir: Path = Path(os.getenv('E5_MODEL_DIR', '/models'))
    e5_threshold: float = float(os.getenv('E5_RELEVANCE_THRESHOLD', '0.8355926997521477'))
    embed_model: str = 'intfloat/multilingual-e5-small' if os.getenv('EMBED_PROVIDER','e5') == 'e5' else os.getenv('BEDROCK_EMBED_MODEL_ID', 'amazon.titan-embed-text-v2:0')
    timeout: float = float(os.getenv('MCP_TIMEOUT_SECONDS', '5'))
    fault_enabled: bool = os.getenv('ENABLE_FAULT_INJECTION', 'false').lower() == 'true'
    embed_dimensions: int = 384 if os.getenv('EMBED_PROVIDER','e5') == 'e5' else int(os.getenv('BEDROCK_EMBED_DIMENSIONS', '1024'))
    embed_normalize: bool = os.getenv('BEDROCK_EMBED_NORMALIZE', 'true').lower() == 'true'
    embed_min_interval: float = float(os.getenv('EMBED_MIN_INTERVAL_SECONDS', '2'))
    embed_max_attempts: int = int(os.getenv('EMBED_MAX_ATTEMPTS', '4'))
    embed_backoff_base: float = float(os.getenv('EMBED_BACKOFF_BASE_SECONDS', '2'))
    embed_backoff_cap: float = float(os.getenv('EMBED_BACKOFF_CAP_SECONDS', '30'))
    embed_retry_budget: float = float(os.getenv('EMBED_RETRY_BUDGET_SECONDS', '90'))
    embed_connect_timeout: float = float(os.getenv('EMBED_CONNECT_TIMEOUT_SECONDS', '5'))
    embed_read_timeout: float = float(os.getenv('EMBED_READ_TIMEOUT_SECONDS', '20'))
    index_lock_timeout: float = float(os.getenv('INDEX_LOCK_TIMEOUT_SECONDS', '5'))
    reconcile_interval: float = float(os.getenv('INDEX_RECONCILE_INTERVAL_SECONDS', '60'))
    index_cooldown_base: float = float(os.getenv('INDEX_COOLDOWN_BASE_SECONDS', '300'))
    index_cooldown_cap: float = float(os.getenv('INDEX_COOLDOWN_CAP_SECONDS', '1800'))
    index_max_failures: int = int(os.getenv('INDEX_MAX_CONSECUTIVE_FAILURES', '5'))
    origin_secret_file: str = os.getenv('ORIGIN_SHARED_SECRET_FILE','')
    mcp_secret_file: str = os.getenv('MCP_SHARED_SECRET_FILE','')
    def __post_init__(self):
        from .private_secret import read_secret
        if self.origin_secret_file:self.origin_secret=read_secret(self.origin_secret_file)
        if self.mcp_secret_file:self.mcp_secret=read_secret(self.mcp_secret_file)
    def validate(self):
        import math
        if self.generation_provider not in ('bedrock','groq'): raise ValueError('Invalid GENERATION_PROVIDER')
        if self.groq_model != 'openai/gpt-oss-20b': raise ValueError('Unverified Groq model')
        if not math.isfinite(self.generation_timeout) or not 0 < self.generation_timeout <= 60: raise ValueError('Invalid generation timeout')
        if self.metric_namespace not in ('RoleOps','RoleOpsVerify'): raise ValueError('Invalid metric namespace')
        for name in ('embed_min_interval', 'embed_backoff_base', 'embed_backoff_cap',
                     'embed_retry_budget', 'embed_connect_timeout', 'embed_read_timeout',
                     'index_lock_timeout', 'reconcile_interval', 'index_cooldown_base', 'index_cooldown_cap'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0: raise ValueError(name + ' must be finite and positive')
        if self.embed_provider not in ('default','e5'): raise ValueError('Invalid EMBED_PROVIDER')
        if self.embed_provider == 'e5':
            if self.embed_dimensions != 384 or not self.embed_normalize: raise ValueError('E5 requires normalized 384-dimensional vectors')
            if not math.isfinite(self.e5_threshold) or not -1 <= self.e5_threshold <= 1: raise ValueError('Invalid E5 threshold')
        elif self.embed_dimensions not in (256,512,1024): raise ValueError('Invalid Titan V2 dimensions')
        if not 1 <= self.embed_max_attempts <= 10: raise ValueError('EMBED_MAX_ATTEMPTS must be 1..10')
        if not 1 <= self.index_max_failures <= 20: raise ValueError('INDEX_MAX_CONSECUTIVE_FAILURES must be 1..20')
        if self.embed_backoff_cap < self.embed_backoff_base or self.index_cooldown_cap < self.index_cooldown_base:
            raise ValueError('Backoff cap must be at least its base')
        if self.mode not in ('local','aws'): raise ValueError('ROLEOPS_MODE must be local or aws')
        if self.region != 'us-east-1': raise ValueError('This release uses us-east-1')
        if len(self.mcp_secret) < 32 or self.mcp_secret.startswith('REPLACE'): raise ValueError('MCP_SHARED_SECRET requires at least 32 characters')
        if self.mode == 'aws' and self.table in ('roleops-verify-restore-test','roleops-restore-test'):raise ValueError('Restore-only table cannot serve an application')
        if self.mode == 'aws' and (self.generation_provider!='groq' or self.embed_provider!='e5'):raise ValueError('AWS release requires Groq 20b and local E5 384')
        if self.mode == 'aws' and (not self.bucket or not self.queue): raise ValueError('S3_BUCKET and SQS_QUEUE_URL required')
        if self.mode == 'aws' and not self.public_origin: raise ValueError('PUBLIC_ORIGIN required in AWS mode')
        if self.public_origin:
            from urllib.parse import urlparse
            u=urlparse(self.public_origin)
            if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.cloudfront.net') or u.netloc!=u.hostname or u.path or u.query or u.fragment or 'replace' in u.hostname.lower(): raise ValueError('PUBLIC_ORIGIN requires the exact HTTPS CloudFront distribution URL')
            if len(self.origin_secret)<32 or self.origin_secret.startswith('REPLACE'): raise ValueError('ORIGIN_SHARED_SECRET requires at least 32 characters')
        self.root.mkdir(parents=True, exist_ok=True)

ROLES = {
 'support': {'knowledge':['policy','support','handover'], 'systems':['crm']},
 'it': {'knowledge':['policy','it','handover','maintenance'], 'systems':['crm']},
 'supervisor': {'knowledge':['policy','support','it','handover','operations'], 'systems':['crm','finance_reporting']},
}
ROLES['maintenance'] = {'knowledge':list(ROLES['it']['knowledge']), 'systems':['crm']} # legacy alias
ROLES['supervisor']['knowledge'].append('maintenance')
AGENT_PROFILES = dict(ROLES)
PERMISSIONS = ('people:manage','agent:manage','request:approve','chat:audit:read','chat:retain:manage','ops:read')
