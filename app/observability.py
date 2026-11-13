import json,logging,datetime
from functools import lru_cache
log=logging.getLogger('roleops');logging.basicConfig(level=logging.INFO,format='%(message)s')

def generation_error_code(error):
    """Finite operational codes only; exception/model text is never a log field."""
    code=str(error)
    allowed={
        'incomplete_subquestion_coverage','invalid_answer_json','answer_schema',
        'answer_status','answer_types','answer_limit','unsupported_proposition',
        'duplicate_proposition','insufficient_with_claims','supported_without_evidence',
        'invalid_suggestion','tool_in_guidance_answer','missing_or_unrequested_native_proposal',
        'proposal_not_user_authorized_parameters','unauthorized_request','invalid_request_reason',
        'unapproved_source_version','scope_changed','payload_not_derived_from_trusted_context',
    }
    if code in {'provider_transport','provider_invalid_json','credential_unavailable_or_unsafe'}:return code
    if code.startswith('provider_http_') and code[14:] in {'400','401','403','404','408','409','413','422','429','500','502','503','504'}:return code
    if code.startswith('output_contract_') and code[16:] in allowed:return code
    return 'generation_response_contract'

@lru_cache(maxsize=2)
def metric_client(region):
    import boto3
    from botocore.config import Config
    return boto3.client('cloudwatch',region_name=region,
                        config=Config(connect_timeout=3,read_timeout=5,
                                      retries={'mode':'standard','total_max_attempts':2}))
def emit(cfg,component,action,outcome,trace_id,metrics=None,**fields):
    log.info(json.dumps(dict(timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),component=component,action=action,outcome=outcome,trace_id=trace_id,**fields),ensure_ascii=False))
    if cfg.mode=='aws' and metrics and getattr(cfg,'metrics_enabled',True):
        try:
            task_type={'mcp_call':'access_request','rebuild':'index_update'}.get(action,action)
            metric_client(cfg.region).put_metric_data(Namespace=cfg.metric_namespace,MetricData=[{'MetricName':k,'Value':v,'Unit':'Milliseconds' if k=='MCPLatency' or k.endswith('Ms') else 'Count','Dimensions':[{'Name':'Component','Value':component},{'Name':'TaskType','Value':task_type}]} for k,v in metrics.items() if v is not None])
        except Exception:log.error(json.dumps({'component':component,'action':'metric_delivery','outcome':'failure','trace_id':trace_id}))
