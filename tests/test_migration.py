from concurrent.futures import ThreadPoolExecutor

from app.modules.m8_migration import service


def test_due_basis_states_adopted_convention(client, query):
    data = client.get('/api/migration', params=query).json()
    assert data['status'] == 'MATCHED' and data['publishable'] is True
    assert set(data['due_basis']) == {'opening', 'restated', 'migration', 'closing'}
    assert '到期日' in data['due_basis']['closing']
    assert data['unexplained'] == {'totals_cents': 0, 'migration_net_cents': 0, 'buckets': [], 'amount_cents': 0}
    assert data['published'] is None


def test_each_bucket_balances_opening_late_migration_period_closing(client, query):
    data = client.get('/api/migration', params=query).json()
    for row in data['buckets']:
        assert row['opening_cents'] + row['late_cents'] + row['migration_cents'] + row['period_cents'] == row['closing_cents']
        assert row['difference_cents'] == 0
    assert sum(row['migration_cents'] for row in data['buckets']) == 0
    assert sum(row['closing_cents'] for row in data['buckets']) == 340065


def test_migration_references_opening_restated_and_closing_vouchers(client, query):
    data = client.get('/api/migration', params=query).json()
    record = next(row for row in data['migrations'] if row['invoice_id'] == 'AR-1001')
    assert record['due'] == {'opening': '2025-12-20', 'restated': '2026-01-20', 'closing': '2026-01-20'}
    assert record['bucket_path'] == {'opening': 'd01_30', 'restated': 'current', 'aged_to': 'd01_30', 'closing': 'd01_30'}
    assert record['causes'] == ['DUE_CORRECTED', 'LATE_REVISION', 'AGED']
    assert (record['opening_version']['event_id'], record['opening_version']['line']) == ('A-V1', 4)
    assert (record['restated_version']['event_id'], record['restated_version']['line']) == ('A-V2', 11)
    assert record['closing_version']['event_id'] == 'A-V2'
    assert [voucher['event_id'] for voucher in record['vouchers']] == ['REV-PAST', 'A-P1']
    assert record['balance_cents'] == {'opening': 125050, 'restated': 115050, 'closing': 90025}


def test_affected_set_and_cause_classification(client, query):
    data = client.get('/api/migration', params=query).json()
    causes = {row['invoice_id']: row['causes'] for row in data['migrations']}
    assert set(causes) == {'AR-1001', 'AR-1002', 'AR-1003', 'AR-1004', 'AR-1005', 'AR-1008', 'AR-1009'}
    assert causes['AR-1002'] == ['LATE_ARRIVAL', 'AGED']
    assert causes['AR-1003'] == ['AGED']
    assert causes['AR-1004'] == ['ENTERED']
    assert causes['AR-1005'] == ['AGED', 'CLEARED']
    assert causes['AR-1008'] == ['AGED']
    assert causes['AR-1009'] == ['AGED']


def test_publish_is_idempotent_and_rerun_adds_no_records(client, query):
    first = client.post('/api/migration', json=query).json()
    assert first['created'] is True and first['status'] == 'MATCHED' and first['affected'] == 7
    publish_id = first['manifest']['publish_id']
    again = client.post('/api/migration', json=query).json()
    assert again['created'] is False and again['manifest'] == first['manifest']
    preview = client.get('/api/migration', params=query).json()
    assert preview['published']['publish_id'] == publish_id
    rerun = client.post(f'/api/migration/{publish_id}/rerun').json()
    assert rerun['created'] is False and rerun['manifest'] == first['manifest']
    assert len(client.get('/api/migration/runs').json()['runs']) == 1
    detail = client.get(f'/api/migration/{publish_id}').json()
    assert detail['manifest'] == first['manifest']
    assert detail['result']['migrations'] == preview['migrations']


def test_equivalent_utc_params_reuse_same_batch(client, query):
    first = client.post('/api/migration', json=query).json()
    second = client.post('/api/migration', json={**query, 'known': '2026-02-06T04:00Z'}).json()
    assert second['created'] is False and second['manifest'] == first['manifest']
    assert len(client.get('/api/migration/runs').json()['runs']) == 1


def test_distinct_period_has_distinct_batch(client, query):
    first = client.post('/api/migration', json=query).json()
    second = client.post('/api/migration', json={**query, 'effective': '2026-01-30'}).json()
    assert first['manifest']['publish_id'] != second['manifest']['publish_id']
    assert len(client.get('/api/migration/runs').json()['runs']) == 2


def test_unexplained_difference_blocks_publish(client, query, monkeypatch):
    real = service.period_book

    def doctored(data, period):
        book = real(data, period)
        book['bridge']['status'] = 'DIFFERENCE'
        book['bridge']['buckets'][0]['difference_cents'] = 500
        return book

    monkeypatch.setattr(service, 'period_book', doctored)
    reply = client.post('/api/migration', json=query)
    assert (reply.status_code, reply.json()['code']) == (409, 'UNEXPLAINED_DIFFERENCE')
    preview = client.get('/api/migration', params=query).json()
    assert preview['publishable'] is False and preview['unexplained']['amount_cents'] == 500
    assert preview['unexplained']['buckets'] == [{'bucket': 'current', 'label': '未逾期', 'difference_cents': 500}]
    assert client.get('/api/migration/runs').json()['runs'] == []


def test_tampered_batch_rejects_rerun_without_overwrite(client, query):
    manifest = client.post('/api/migration', json=query).json()['manifest']
    publish_id = manifest['publish_id']
    with client.app.state.store.connect() as connection:
        connection.execute('UPDATE migration_runs SET result_digest=? WHERE publish_id=?', ('bad-digest', publish_id))
    reply = client.post(f'/api/migration/{publish_id}/rerun')
    assert (reply.status_code, reply.json()['code']) == (409, 'MIGRATION_MISMATCH')
    assert client.get(f'/api/migration/{publish_id}').json()['manifest']['result_digest'] == 'bad-digest'


def test_rule_version_change_blocks_rerun(client, query, monkeypatch):
    manifest = client.post('/api/migration', json=query).json()['manifest']
    monkeypatch.setattr(service, 'RULE_VERSION', 'ar-bitemporal-2')
    reply = client.post(f"/api/migration/{manifest['publish_id']}/rerun")
    assert (reply.status_code, reply.json()['code']) == (409, 'RULE_VERSION_CHANGED')


def test_missing_batch_and_source_have_distinct_codes(client, query):
    source = client.post('/api/migration', json={**query, 'source_id': 'missing'})
    run = client.post('/api/migration/missing/rerun')
    assert [(reply.status_code, reply.json()['code']) for reply in (source, run)] == [(404, 'SOURCE_NOT_FOUND'), (404, 'RUN_NOT_FOUND')]


def test_concurrent_publish_leaves_one_committed_batch(client, query):
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(lambda _: client.post('/api/migration', json=query), range(4)))
    assert [reply.status_code for reply in replies] == [200] * 4
    runs = [reply.json() for reply in replies]
    assert sum(run['created'] for run in runs) == 1
    assert len({run['manifest']['publish_id'] for run in runs}) == 1
    assert len(client.get('/api/migration/runs').json()['runs']) == 1
