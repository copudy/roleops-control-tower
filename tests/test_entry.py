from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app.web import create_app
from deploy.create_cloudfront import configuration, CACHE_DISABLED, ALL_EXCEPT_HOST

PUBLIC = 'https://roleops-fixture.invalid.cloudfront.net'
SECRET = 'a' * 64


def public_client(env):
    cfg, *_ = env
    app = create_app(replace(cfg, public_origin=PUBLIC, origin_secret=SECRET))
    return TestClient(app, base_url=PUBLIC)


def test_public_entry_rejects_direct_and_wrong_secret(env):
    client = public_client(env)
    for path in ['/', '/healthz', '/api/status', '/static/app.js']:
        assert client.get(path).status_code == 403
        assert client.get(path, headers={'x-roleops-origin': 'wrong'}).status_code == 403
        assert client.get(path, headers={'x-roleops-origin': SECRET}).status_code == 200


def test_https_cookie_origin_csrf_and_logout(env):
    client = public_client(env)
    headers = {'x-roleops-origin': SECRET, 'origin': PUBLIC, 'host': 'ec2-1-2-3-4.compute-1.amazonaws.com'}
    login = client.post('/api/auth/login', json={'login': 'admin', 'password': 'secure-password-123'}, headers=headers)
    assert login.status_code == 200
    cookie = login.headers['set-cookie']
    assert 'Secure' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert login.headers['cache-control'] == 'no-store'
    assert login.headers['strict-transport-security'] == 'max-age=86400'
    writes = dict(headers, **{'x-csrf-token': login.json()['csrf']})
    assert client.post('/api/conversations', json={'title': '測試'}, headers=writes).status_code == 200
    for origin in ['http://roleops-fixture.invalid.cloudfront.net', 'https://evil.example', 'https://roleops-fixture.invalid.cloudfront.net.evil.example', PUBLIC + ':444']:
        assert client.post('/api/conversations', json={'title': '拒絕'}, headers={**writes, 'origin': origin}).status_code == 403
    assert client.post('/api/conversations', json={'title': '拒絕'}, headers=headers).status_code == 403
    logout = client.post('/api/auth/logout', headers=writes)
    assert logout.status_code == 200 and 'Secure' in logout.headers['set-cookie']
    assert client.get('/api/me', headers=headers).status_code == 401


def test_origin_config_fails_closed(env):
    cfg, *_ = env
    for url in ['', 'http://roleops-fixture.invalid.cloudfront.net', 'https://roleops-fixture.invalid.cloudfront.net:443', 'https://roleops-fixture.invalid.cloudfront.net/path', 'https://REPLACE.cloudfront.net', 'https://roleops-fixture.invalid.cloudfront.net@evil.example']:
        with pytest.raises(ValueError):
            replace(cfg, mode='aws', public_origin=url, origin_secret=SECRET, bucket='test', queue='test').validate()
    with pytest.raises(ValueError):
        replace(cfg, public_origin=PUBLIC, origin_secret='short').validate()


def test_login_throttle_uses_cloudfront_appended_viewer_address(env):
    client = public_client(env)
    shared = {'x-roleops-origin': SECRET, 'origin': PUBLIC}
    bad = {'login': 'admin', 'password': 'wrong-password'}
    for _ in range(10):
        assert client.post('/api/auth/login', json=bad, headers={**shared, 'x-forwarded-for': 'spoofed, 192.0.2.1'}).status_code == 401
    # Spoofing the first header entry does not reset the viewer's limiter.
    assert client.post('/api/auth/login', json=bad, headers={**shared, 'x-forwarded-for': 'different, 192.0.2.1'}).status_code == 429
    assert client.post('/api/auth/login', json=bad, headers={**shared, 'x-forwarded-for': '192.0.2.2'}).status_code == 401


def test_cloudfront_configuration_contract():
    fixture_instance_id = 'i-' + '1234567890abcdef0'
    cfg = configuration('ec2-1-2-3-4.compute-1.amazonaws.com', SECRET, fixture_instance_id, 'isolated-test')
    behavior = cfg['DefaultCacheBehavior']
    assert behavior['ViewerProtocolPolicy'] == 'https-only'
    assert behavior['CachePolicyId'] == CACHE_DISABLED
    assert behavior['OriginRequestPolicyId'] == ALL_EXCEPT_HOST
    assert set(behavior['AllowedMethods']['Items']) == {'GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE'}
    assert all(item['ErrorCachingMinTTL'] == 0 for item in cfg['CustomErrorResponses']['Items'])
    assert not cfg['Logging']['Enabled'] and not cfg['Logging']['IncludeCookies']
    assert cfg['ViewerCertificate'] == {'CloudFrontDefaultCertificate': True}
    assert cfg['Origins']['Items'][0]['CustomOriginConfig']['HTTPPort'] == 8000
    with pytest.raises(ValueError):
        configuration('other.example.com', SECRET, 'i-test', 'test')


@pytest.mark.parametrize('guard', ['wrong-project', 'duplicate', 'cancel'])
def test_entry_creation_stops_before_mutation(monkeypatch, guard):
    import deploy.create_cloudfront as deployment
    calls = []
    def fake(*args):
        calls.append(args)
        if args[0] == 'sts': return {'Account': '123456789012'}
        if args[0] == 'ec2':
            return {'Reservations': [{'Instances': [{'PublicDnsName': 'ec2-1-2-3-4.compute-1.amazonaws.com',
                'State': {'Name': 'running'}, 'Tags': [{'Key': 'Project', 'Value': 'Other' if guard == 'wrong-project' else 'RoleOps'},
                    {'Key': 'Environment', 'Value': 'Demo'}]}]}]}
        if args[1] == 'list-distributions':
            return {'DistributionList': {'Items': [{'Comment': 'RoleOps Demo i-1234567890abcdef0', 'Id': 'existing'}] if guard == 'duplicate' else []}}
        raise AssertionError('No mutating API should run')
    monkeypatch.setattr(deployment, 'aws', fake)
    monkeypatch.setattr('sys.argv', ['create', '--instance-id', 'i-1234567890abcdef0', '--owner', 'test'])
    monkeypatch.setattr(deployment.getpass, 'getpass', lambda _: SECRET)
    monkeypatch.setattr('builtins.input', lambda _: 'CANCEL')
    with pytest.raises(SystemExit): deployment.main()
    assert not any('create-distribution-with-tags' in args for args in calls)
