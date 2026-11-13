"""Run in AWS CloudShell only; this module has no import-time AWS calls."""
import argparse
import getpass
import json
import os
import re
import subprocess
import tempfile
import uuid

CACHE_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad'
ALL_EXCEPT_HOST = 'b689b0a8-53d0-40ab-baf2-68738e2966ac'


def configuration(dns, secret, instance_id, caller_reference):
    if not re.fullmatch(r'ec2-[\d-]+\.compute-1\.amazonaws\.com', dns):
        raise ValueError('A us-east-1 EC2 Public IPv4 DNS name is required')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', secret):
        raise ValueError('Use an independently generated 64-character random hex secret')
    return {
        'CallerReference': caller_reference,
        'Aliases': {'Quantity': 0},
        'DefaultRootObject': '',
        'Origins': {'Quantity': 1, 'Items': [{
            'Id': 'roleops-ec2', 'DomainName': dns,
            'CustomHeaders': {'Quantity': 1, 'Items': [
                {'HeaderName': 'X-RoleOps-Origin', 'HeaderValue': secret}]},
            'CustomOriginConfig': {
                'HTTPPort': 8000, 'HTTPSPort': 443,
                'OriginProtocolPolicy': 'http-only',
                'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']},
                'OriginReadTimeout': 60, 'OriginKeepaliveTimeout': 5},
            'ConnectionAttempts': 3, 'ConnectionTimeout': 10}]},
        'DefaultCacheBehavior': {
            'TargetOriginId': 'roleops-ec2',
            'ViewerProtocolPolicy': 'https-only',
            'AllowedMethods': {'Quantity': 7,
                              'Items': ['GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE'],
                              'CachedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD']}},
            'CachePolicyId': CACHE_DISABLED,
            'OriginRequestPolicyId': ALL_EXCEPT_HOST,
            'TrustedSigners': {'Enabled': False, 'Quantity': 0},
            'TrustedKeyGroups': {'Enabled': False, 'Quantity': 0},
            'Compress': True},
        'CacheBehaviors': {'Quantity': 0},
        'CustomErrorResponses': {'Quantity': 7, 'Items': [
            {'ErrorCode': code, 'ErrorCachingMinTTL': 0}
            for code in [400, 403, 404, 500, 502, 503, 504]]},
        'Comment': 'RoleOps Demo ' + instance_id,
        'Logging': {'Enabled': False, 'IncludeCookies': False, 'Bucket': '', 'Prefix': ''},
        'PriceClass': 'PriceClass_100',
        'Enabled': True,
        'ViewerCertificate': {'CloudFrontDefaultCertificate': True},
        'Restrictions': {'GeoRestriction': {'RestrictionType': 'none', 'Quantity': 0}},
        'HttpVersion': 'http2', 'IsIPV6Enabled': True,
    }


def aws(*args):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--output', 'json', '--no-cli-pager'],
                            text=True, capture_output=True)
    if result.returncode:
        # Never echo an origin secret or a complete distribution configuration.
        error = re.sub(r'[a-fA-F0-9]{64}', '[secret omitted]', result.stderr)
        raise RuntimeError(error.strip()[:1500])
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'i-[a-f0-9]{8,17}', args.instance_id):
        raise SystemExit('Invalid instance ID')
    if not args.owner or len(args.owner) > 64:
        raise SystemExit('Owner must contain 1-64 characters')
    caller = aws('sts', 'get-caller-identity')
    instance = aws('ec2', 'describe-instances', '--instance-ids', args.instance_id)['Reservations'][0]['Instances'][0]
    tags = {item['Key']: item['Value'] for item in instance.get('Tags', [])}
    if tags.get('Project') != 'RoleOps' or tags.get('Environment') != 'Demo':
        raise SystemExit('The instance must be tagged Project=RoleOps and Environment=Demo')
    if instance['State']['Name'] != 'running':
        raise SystemExit('Start the named RoleOps instance first')
    dns = instance.get('PublicDnsName', '')
    existing = aws('cloudfront', 'list-distributions').get('DistributionList', {}).get('Items', [])
    matches = [item for item in existing if item.get('Comment') == 'RoleOps Demo ' + args.instance_id]
    if matches:
        raise SystemExit('A distribution for this instance already exists: ' + ', '.join(item['Id'] for item in matches))
    print('Account:', caller['Account'], '\nInstance:', args.instance_id, '\nOrigin DNS:', dns)
    print('Create one pay-as-you-go CloudFront distribution; PriceClass_100; no WAF, ALB or domain purchase.')
    print('This synthetic-data Demo uses HTTP on the restricted origin connection, not end-to-end TLS.')
    secret = getpass.getpass('Origin secret (64 random hex characters; save the same value in EC2 .env): ').strip()
    config = configuration(dns, secret, args.instance_id, 'roleops-' + uuid.uuid4().hex)
    if input('After checking account, cost and instance, type CREATE: ').strip() != 'CREATE':
        raise SystemExit('Cancelled; no resource created')
    payload = {'DistributionConfig': config, 'Tags': {'Items': [
        {'Key': 'Project', 'Value': 'RoleOps'}, {'Key': 'Environment', 'Value': 'Demo'},
        {'Key': 'Owner', 'Value': args.owner}, {'Key': 'DeleteAfter', 'Value': '2026-10-15'}]}}
    file_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', delete=False) as handle:
            file_name = handle.name
            os.chmod(file_name, 0o600)
            json.dump(payload, handle)
        result = aws('cloudfront', 'create-distribution-with-tags',
                     '--distribution-config-with-tags', 'file://' + file_name)['Distribution']
        record = {'id': result['Id'], 'arn': result['ARN'], 'url': 'https://' + result['DomainName'],
                  'origin_dns': dns, 'instance_id': args.instance_id, 'account': caller['Account']}
        record_path = os.path.expanduser('~/roleops-cloudfront-record.json')
        # Exclusive create avoids overwriting a resource record for another project run.
        if os.path.exists(record_path):
            record_path = os.path.expanduser('~/roleops-cloudfront-' + result['Id'] + '.json')
        with open(record_path, 'x', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        os.chmod(record_path, 0o600)
        print('Distribution:', record['id'], '\nHTTPS URL:', record['url'], '\nRecord:', record_path)
        print('Wait for deployment in CloudFront Console; then set EC2 PUBLIC_ORIGIN and deploy the application.')
    finally:
        if file_name and os.path.exists(file_name):
            os.remove(file_name)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as exc:
        raise SystemExit(str(exc))
