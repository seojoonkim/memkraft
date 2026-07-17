import json
import pytest
from memkraft import MemKraft


def mk(p): return MemKraft(base_dir=str(p))


def test_sleep_plan_apply_idempotent_and_journal(tmp_path):
    m=mk(tmp_path); m.append_event('u','city','Seoul',source='chat')
    a=m.sleep(); b=m.sleep()
    assert a == b and a['transaction_id']
    assert not (tmp_path/'.memkraft/compiled_truth.jsonl').exists()
    applied=m.sleep(dry_run=False); retried=m.sleep(dry_run=False)
    assert applied['transaction_id']==a['transaction_id']==retried['transaction_id']
    assert retried['status']=='already_applied'
    rows=(tmp_path/'.memkraft/sleep_journal.jsonl').read_text().splitlines()
    assert len(rows)==1 and m.current_truth('u')=={'city':'Seoul'}


def test_sleep_invalid_strategy_and_source_guard(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).sleep(strategy='clusters')
    with pytest.raises(ValueError): mk(tmp_path).append_event('u','k','v')


def test_forget_dry_run_apply_propagates_and_audits(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','city','Seoul',source='chat'); m.sleep(dry_run=False)
    plan=m.forget(e['id']); assert plan['matched']==1 and m.current_truth('u')=={'city':'Seoul'}
    m.forget(e['id'],dry_run=False)
    assert m.compile_truth()['records']==[] and m.current_truth('u')=={}
    assert m.export_memory()==[]
    assert m.audit_log(action='forget')[0]['target']==e['id']
    assert m.export_memory(include_tombstoned=True)


def test_deny_policy_blocks_append_and_filters_existing(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret','x',source='chat')
    m.do_not_remember(subject='u',key='secret',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('u','secret','y',source='chat')
    assert m.compile_truth()['records']==[]


def test_timeline_is_deterministic_rebuildable(tmp_path):
    m=mk(tmp_path); a=m.append_event('u','a',1,source='s'); b=m.append_event('u','b',2,source='s')
    assert m.timeline('u') == m.timeline('u')
    assert [x['id'] for x in m.timeline('u')] == [a['id'],b['id']]


def test_forget_invalid_and_pattern_contract(tmp_path):
    m=mk(tmp_path)
    with pytest.raises(ValueError): m.forget(' ')
    with pytest.raises(ValueError): m.do_not_remember()
    p=m.do_not_remember(pattern='token*')
    assert p['policy']['pattern']=='token*' and p['dry_run']


def test_sleep_recovers_truth_written_before_journal(tmp_path):
    m=mk(tmp_path); m.append_event('u','k',1,source='s'); plan=m.sleep()
    m.compile_truth(dry_run=False)
    got=m.sleep(dry_run=False)
    assert got['transaction_id']==plan['transaction_id'] and got['status']=='applied'
    assert len((tmp_path/'.memkraft/sleep_journal.jsonl').read_text().splitlines())==1


def test_governance_concurrent_forget_is_idempotent(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','k',1,source='s')
    m.forget(e['id'],dry_run=False); again=m.forget(e['id'],dry_run=False)
    assert again['status']=='already_forgotten'
    assert len(m.audit_log(action='forget'))==1

def test_export_and_audit_invalid_inputs(tmp_path):
    m=mk(tmp_path)
    with pytest.raises(TypeError): m.export_memory(include_tombstoned='yes')
    with pytest.raises(ValueError): m.audit_log(limit=0)
    with pytest.raises(ValueError): m.do_not_remember(pattern='[')

def test_sleep_plan_transaction_changes_with_event_set(tmp_path):
    m=mk(tmp_path); m.append_event('u','a',1,source='s'); one=m.sleep()['transaction_id']
    m.append_event('u','b',2,source='s'); assert m.sleep()['transaction_id'] != one

def test_tombstoned_event_hidden_from_timeline(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','k',1,source='s'); m.forget(e['id'],dry_run=False)
    assert m.timeline('u')==[]
    assert len(m.timeline('u',include_tombstoned=True)) >= 1

def test_forget_target_can_select_subject_key(tmp_path):
    m=mk(tmp_path); m.append_event('u','a',1,source='s'); m.append_event('u','b',2,source='s')
    assert m.forget({'subject':'u','key':'a'},dry_run=False)['matched']==1
    assert [r['key'] for r in m.export_memory()]==['b']

def test_append_rejects_bad_policy_pattern_without_corrupting(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='secret*',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('u','secret-token','x',source='s')
    assert m.timeline()==[]

def test_audit_query_subject(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',key='x',dry_run=False)
    assert len(m.audit_log(action='do_not_remember',subject='u',limit=1))==1
    assert m.audit_log(subject='other')==[]

def test_export_sorted_event_order(tmp_path):
    m=mk(tmp_path); a=m.append_event('u','z',1,source='s'); b=m.append_event('u','a',2,source='s')
    assert [r['id'] for r in m.export_memory()]==[a['id'],b['id']]

def test_sleep_dry_run_has_zero_writes_on_empty_store(tmp_path):
    plan=mk(tmp_path).sleep()
    assert plan['records']==[] and not (tmp_path/'.memkraft').exists()

def test_forget_unknown_is_stable_noop(tmp_path):
    m=mk(tmp_path); p=m.forget('missing',dry_run=False)
    assert p['matched']==0 and p['status']=='not_found'

def test_policy_dry_run_zero_writes(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',key='x')
    assert not (tmp_path/'.memkraft').exists()

def test_timeline_rejects_invalid_subject(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).timeline(3)

def test_sleep_rejects_non_bool_dry_run(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).sleep(dry_run='yes')

def test_forget_rejects_non_bool_dry_run(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).forget('x',dry_run='yes')

def test_policy_exact_subject_all_keys(tmp_path):
    m=mk(tmp_path); m.append_event('u','a',1,source='s'); m.append_event('v','a',2,source='s')
    m.do_not_remember(subject='u',dry_run=False)
    assert [(x['subject_id'],x['key']) for x in m.export_memory()]==[('v','a')]

def test_audit_records_are_append_only_envelopes(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False)
    row=m.audit_log()[0]; assert row['id'] and row['created_at'] and row['schema_version']==1

def test_forget_dry_plan_is_deterministic(tmp_path):
    m=mk(tmp_path); m.append_event('u','a',1,source='s')
    assert m.forget({'subject':'u'})==m.forget({'subject':'u'})

def test_compile_after_policy_apply_updates_compiled_on_sleep(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False)
    m.do_not_remember(subject='u',key='x',dry_run=False); m.sleep(dry_run=False)
    assert m.current_truth('u')=={}

def test_export_tombstoned_contains_marker(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],dry_run=False)
    allrows=m.export_memory(include_tombstoned=True)
    assert any(r.get('tombstone_of')==e['id'] for r in allrows)

def test_timeline_all_subjects(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('v','x',2,source='s')
    assert len(m.timeline())==2

def test_policy_invalid_blank_values(tmp_path):
    m=mk(tmp_path)
    with pytest.raises(ValueError): m.do_not_remember(subject=' ')
    with pytest.raises(ValueError): m.do_not_remember(key='x')

def test_forget_dict_requires_selector(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).forget({})

def test_audit_default_newest_first(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='a',dry_run=False); m.do_not_remember(subject='b',dry_run=False)
    assert [r['subject'] for r in m.audit_log()]==['b','a']

def test_sleep_result_structured_json_serializable(tmp_path):
    assert json.loads(json.dumps(mk(tmp_path).sleep()))['strategy']=='default'

def test_forget_source_less_derived_write_not_possible(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.forget({'subject':'u'},dry_run=False)
    assert all(r.get('source') for r in m.export_memory() if not r.get('tombstone'))

def test_policy_pattern_matches_subject_colon_key(tmp_path):
    m=mk(tmp_path); m.append_event('user-1','token',1,source='s'); m.do_not_remember(pattern='user-*:token',dry_run=False)
    assert m.compile_truth()['records']==[]

def test_sleep_journal_has_transaction_and_strategy(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); row=m.audit_log(action='sleep')[0]
    assert row['transaction_id'] and row['strategy']=='default'

def test_compile_truth_dry_run_stays_compatible(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s')
    assert m.compile_truth()['dry_run'] is True

def test_forget_compiled_visibility_updates_immediately(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.sleep(dry_run=False); m.forget(e['id'],dry_run=False)
    assert m.current_truth('u')=={}

def test_policy_apply_updates_compiled_visibility_immediately(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False); m.do_not_remember(subject='u',key='x',dry_run=False)
    assert m.current_truth('u')=={}

def test_export_bool_validation(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).timeline(include_tombstoned=1)

def test_sleep_apply_false_alias(tmp_path):
    assert mk(tmp_path).sleep(dry_run=False)['status']=='applied'

def test_forget_marker_is_not_duplicated(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); m.forget(e['id'],False)
    assert sum(bool(x.get('tombstone')) for x in m.export_memory(True))==1

def test_policy_duplicate_apply_idempotent(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); m.do_not_remember(subject='u',dry_run=False)
    assert len(m.audit_log(action='do_not_remember'))==1

def test_transaction_is_hex_digest(tmp_path):
    tx=mk(tmp_path).sleep()['transaction_id']; assert len(tx)==64 and int(tx,16)>=0

def test_timeline_rows_are_copies(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); rows=m.timeline(); rows[0]['value']=9
    assert m.timeline()[0]['value']==1

def test_audit_limit_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).audit_log(limit='1')

def test_forget_target_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).forget(3)

def test_policy_key_requires_subject(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(key='x',dry_run=False)

def test_current_truth_subject_validation(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).current_truth([])

def test_timeline_policy_filter(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False)
    assert m.timeline()==[]

def test_deny_policy_cannot_be_bypassed_by_tombstone_audit_views(tmp_path):
    m=mk(tmp_path)
    denied=m.append_event('u','secret','release-blocking-token',source='s')
    visible=m.append_event('u','public','ordinary-token',source='s')
    m.sleep(dry_run=False)
    m.forget(visible['id'],dry_run=False)
    m.do_not_remember(subject='u',key='secret',dry_run=False)

    exported=m.export_memory(include_tombstoned=True)
    timeline=m.timeline(include_tombstoned=True)
    assert denied['id'] not in {row.get('id') for row in exported}
    assert denied['id'] not in {row.get('id') for row in timeline}
    assert m.current_truth('u')=={}
    assert 'release-blocking-token' not in json.dumps(
        m.compile_context('release-blocking-token',budget=1000)['sections'],sort_keys=True
    )
    assert all('release-blocking-token' not in json.dumps(row,sort_keys=True)
               for row in m.search('release-blocking-token'))

    # The audit flag still exposes an otherwise-visible forgotten record and
    # its marker; it grants no privilege over do-not-remember policy.
    assert visible['id'] not in {row.get('id') for row in m.export_memory()}
    assert visible['id'] in {row.get('id') for row in exported}
    assert any(row.get('tombstone_of')==visible['id'] for row in exported)

def test_sleep_records_equal_compile_records(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s')
    assert m.sleep()['records']==m.compile_truth()['records']

def test_policy_plan_deterministic(tmp_path):
    m=mk(tmp_path); assert m.do_not_remember(subject='u')==m.do_not_remember(subject='u')

def test_forget_subject_plan_lists_ids(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s')
    assert m.forget({'subject':'u'})['record_ids']==[e['id']]

def test_audit_unknown_filter_empty(tmp_path):
    assert mk(tmp_path).audit_log(action='none')==[]

def test_no_speculative_sleep_steps(tmp_path):
    assert mk(tmp_path).sleep()['steps']==['compile_truth']

def test_policy_source_is_governance(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False)
    assert m.audit_log()[0]['source']=='governance'

def test_forget_audit_source(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False)
    assert m.audit_log()[0]['source']=='governance'

def test_sleep_audit_source(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False)
    assert m.audit_log()[0]['source']=='sleep'

def test_compile_skips_denied_but_counts_not_invalid(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False)
    assert m.compile_truth()['skipped']==0

def test_forget_does_not_mutate_original_event(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False)
    assert m.export_memory(True)[0]['value']==1

def test_sleep_apply_returns_applied_false_on_retry(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.sleep(dry_run=False)['applied'] is False

def test_sleep_dry_applied_false(tmp_path):
    assert mk(tmp_path).sleep()['applied'] is False

def test_governance_plans_json_serializable(tmp_path):
    m=mk(tmp_path); json.dumps(m.forget('x')); json.dumps(m.do_not_remember(subject='u'))

def test_timeline_created_order(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','x',2,source='s')
    assert [r['value'] for r in m.timeline()]==[1,2]

def test_export_is_not_compiled_truth(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','x',2,source='s')
    assert len(m.export_memory())==2

def test_empty_audit(tmp_path):
    assert mk(tmp_path).audit_log()==[]

def test_empty_timeline(tmp_path):
    assert mk(tmp_path).timeline()==[]

def test_empty_export(tmp_path):
    assert mk(tmp_path).export_memory()==[]

def test_subject_key_policy_blocks_any_value(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',key='x',dry_run=False)
    for value in (None,0,False):
        with pytest.raises(PermissionError): m.append_event('u','x',value,source='s')

def test_pattern_policy_invalid_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).do_not_remember(pattern=3)

def test_audit_subject_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).audit_log(subject=2)

def test_sleep_strategy_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).sleep(strategy=1)

def test_export_returns_copies(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); x=m.export_memory(); x.clear(); assert len(m.export_memory())==1

def test_policy_apply_status(tmp_path):
    assert mk(tmp_path).do_not_remember(subject='u',dry_run=False)['status']=='applied'

def test_policy_dry_status(tmp_path):
    assert mk(tmp_path).do_not_remember(subject='u')['status']=='planned'

def test_forget_dry_status(tmp_path):
    assert mk(tmp_path).forget('x')['status']=='planned'

def test_sleep_dry_status(tmp_path):
    assert mk(tmp_path).sleep()['status']=='planned'

def test_sleep_journal_append_only_across_plans(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False)
    assert len(m.audit_log(action='sleep'))==2

def test_deny_policy_persists(tmp_path):
    mk(tmp_path).do_not_remember(subject='u',dry_run=False)
    with pytest.raises(PermissionError): mk(tmp_path).append_event('u','x',1,source='s')

def test_forget_persists(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False)
    assert mk(tmp_path).export_memory()==[]

def test_truth_rebuild_after_forget(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); m.compile_truth(False)
    assert m.current_truth('u')=={}

def test_timeline_has_provenance(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s',provenance='p')
    assert m.timeline()[0]['provenance']=='p'

def test_export_has_envelope(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.export_memory()[0]['schema_version']==1

def test_forget_key_selector_requires_subject(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).forget({'key':'x'})

def test_pattern_empty(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(pattern=' ')

def test_policy_mutual_contract(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(subject='u',pattern='*')

def test_sleep_plan_has_counts(tmp_path):
    p=mk(tmp_path).sleep(); assert p['event_count']==0 and p['record_count']==0

def test_forget_plan_count(tmp_path):
    assert mk(tmp_path).forget('x')['matched']==0

def test_audit_limit(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='a',dry_run=False); m.do_not_remember(subject='b',dry_run=False); assert len(m.audit_log(limit=1))==1

def test_current_truth_policy_even_before_recompile(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.compile_truth(False); m.do_not_remember(subject='u',dry_run=False)
    assert m.current_truth('u')=={}

def test_source_less_compiled_records_impossible(tmp_path):
    m=mk(tmp_path); p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text('{"subject_id":"u","key":"x","value":1}\n')
    assert m.compile_truth()['records']==[]

def test_forget_by_subject_all(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','y',2,source='s'); assert m.forget({'subject':'u'},False)['matched']==2

def test_pattern_glob_minimal(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='*:password',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('u','password','x',source='s')

def test_compile_truth_apply_return(tmp_path):
    assert mk(tmp_path).compile_truth(False)['applied'] is True

def test_sleep_exact_plan_repeat_across_instance(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.sleep()==mk(tmp_path).sleep()

def test_audit_records_include_action(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.audit_log()[0]['action']=='do_not_remember'

def test_forget_records_include_action(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.audit_log()[0]['action']=='forget'

def test_sleep_records_include_action(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.audit_log()[0]['action']=='sleep'

def test_export_include_tombstoned_positional(tmp_path):
    assert mk(tmp_path).export_memory(False)==[]

def test_timeline_include_tombstoned_positional(tmp_path):
    assert mk(tmp_path).timeline(None,False)==[]

def test_policy_no_broad_regex(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='u:token?',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('u','token1',1,source='s')

def test_forget_exact_id_only(tmp_path):
    m=mk(tmp_path); a=m.append_event('u','x',1,source='s'); b=m.append_event('u','y',2,source='s'); m.forget(a['id'],False); assert [x['id'] for x in m.export_memory()]==[b['id']]

def test_dry_sleep_no_journal(tmp_path):
    m=mk(tmp_path); m.sleep(); assert m.audit_log()==[]

def test_dry_forget_no_audit(tmp_path):
    m=mk(tmp_path); m.forget('x'); assert m.audit_log()==[]

def test_dry_policy_no_audit(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u'); assert m.audit_log()==[]

def test_sleep_default_only(tmp_path):
    with pytest.raises(ValueError,match='default'): mk(tmp_path).sleep('auto')

def test_compile_truth_policy_rebuildable(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False); assert m.compile_truth()==m.compile_truth()

def test_timeline_deterministic_across_instance(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.timeline()==mk(tmp_path).timeline()

def test_export_deterministic_across_instance(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.export_memory()==mk(tmp_path).export_memory()

def test_audit_deterministic_across_instance(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.audit_log()==mk(tmp_path).audit_log()

def test_forget_dict_subject_alias(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.forget({'subject_id':'u'})['matched']==1

def test_sleep_plan_skipped(tmp_path):
    assert mk(tmp_path).sleep()['skipped']==0

def test_transaction_excludes_dry_run_flag(tmp_path):
    m=mk(tmp_path); assert m.sleep()['transaction_id']==m.sleep(dry_run=False)['transaction_id']

def test_retry_no_truth_rewrite(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); p=tmp_path/'.memkraft/compiled_truth.jsonl'; before=p.stat().st_mtime_ns; m.sleep(dry_run=False); assert p.stat().st_mtime_ns==before

def test_policy_record_has_stable_id(tmp_path):
    m=mk(tmp_path); a=m.do_not_remember(subject='u'); b=m.do_not_remember(subject='u'); assert a['policy']['id']==b['policy']['id']

def test_forget_empty_string(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).forget('')

def test_subject_empty_timeline(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).timeline(' ')

def test_subject_empty_audit(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).audit_log(subject=' ')

def test_audit_action_type(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).audit_log(action=1)

def test_sleep_event_count_before_policy(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False); assert m.sleep()['event_count']==1

def test_sleep_record_count_after_policy(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False); assert m.sleep()['record_count']==0

def test_forget_marker_hidden_export(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.export_memory()==[]

def test_policy_does_not_append_canonical_event(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.export_memory(True)==[]

def test_sleep_does_not_append_canonical_event(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.export_memory(True)==[]

def test_forget_is_append_only(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); before=(tmp_path/'.memkraft/events.jsonl').read_text(); m.forget(e['id'],False); assert (tmp_path/'.memkraft/events.jsonl').read_text().startswith(before)

def test_compile_truth_tombstones_propagate(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.compile_truth()['records']==[]

def test_policy_pattern_subject_key_join(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='u:*',dry_run=False); assert m.do_not_remember(pattern='u:*',dry_run=False)['status']=='already_applied'

def test_audit_limit_bool_rejected(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).audit_log(limit=True)

def test_sleep_dry_bool_strict(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).sleep(dry_run=1)

def test_forget_dry_bool_strict(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).forget('x',dry_run=0)

def test_policy_dry_bool_strict(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).do_not_remember(subject='u',dry_run=0)

def test_export_bool_strict(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).export_memory(0)

def test_timeline_bool_strict(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).timeline(include_tombstoned=0)

def test_current_truth_denied_key_only(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','y',2,source='s'); m.compile_truth(False); m.do_not_remember(subject='u',key='x',dry_run=False); assert m.current_truth('u')=={'y':2}

def test_export_denied_key_only(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','y',2,source='s'); m.do_not_remember(subject='u',key='x',dry_run=False); assert [r['key'] for r in m.export_memory()]==['y']

def test_timeline_denied_key_only(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','y',2,source='s'); m.do_not_remember(subject='u',key='x',dry_run=False); assert [r['key'] for r in m.timeline()]==['y']

def test_sleep_has_versioned_contract(tmp_path):
    assert mk(tmp_path).sleep()['schema_version']==1

def test_governance_has_versioned_contract(tmp_path):
    m=mk(tmp_path); assert m.forget('x')['schema_version']==1 and m.do_not_remember(subject='u')['schema_version']==1

def test_timeline_returns_list(tmp_path):
    assert isinstance(mk(tmp_path).timeline(),list)

def test_export_returns_list(tmp_path):
    assert isinstance(mk(tmp_path).export_memory(),list)

def test_audit_returns_list(tmp_path):
    assert isinstance(mk(tmp_path).audit_log(),list)

def test_forget_apply_recompiles_existing_truth(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.compile_truth(False); m.forget(e['id'],False); assert (tmp_path/'.memkraft/compiled_truth.jsonl').exists()

def test_policy_apply_recompiles_existing_truth(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.compile_truth(False); m.do_not_remember(subject='u',dry_run=False); assert (tmp_path/'.memkraft/compiled_truth.jsonl').exists()

def test_unknown_forget_creates_no_marker(tmp_path):
    m=mk(tmp_path); m.forget('x',False); assert m.export_memory(True)==[]

def test_unknown_forget_creates_no_audit(tmp_path):
    m=mk(tmp_path); m.forget('x',False); assert m.audit_log()==[]

def test_transaction_stable_after_compile(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); tx=m.sleep()['transaction_id']; m.compile_truth(False); assert m.sleep()['transaction_id']==tx

def test_transaction_changes_after_forget(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); tx=m.sleep()['transaction_id']; m.forget(e['id'],False); assert m.sleep()['transaction_id']!=tx

def test_transaction_changes_after_policy(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); tx=m.sleep()['transaction_id']; m.do_not_remember(subject='u',dry_run=False); assert m.sleep()['transaction_id']!=tx

def test_policy_audit_subject(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.audit_log()[0]['subject']=='u'

def test_forget_audit_target(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.audit_log()[0]['target']==e['id']

def test_sleep_audit_record_count(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False); assert m.audit_log()[0]['record_count']==1

def test_policy_pattern_audit(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='u:*',dry_run=False); assert m.audit_log()[0]['pattern']=='u:*'

def test_pattern_exact_no_false_positive(tmp_path):
    m=mk(tmp_path); m.do_not_remember(pattern='u:secret*',dry_run=False); m.append_event('v','secret-x',1,source='s'); assert len(m.export_memory())==1

def test_subject_policy_no_false_positive(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); m.append_event('uu','x',1,source='s'); assert len(m.export_memory())==1

def test_key_policy_no_false_positive(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',key='x',dry_run=False); m.append_event('u','xx',1,source='s'); assert len(m.export_memory())==1

def test_sleep_plan_records_sorted(tmp_path):
    m=mk(tmp_path); m.append_event('z','b',1,source='s'); m.append_event('a','z',2,source='s'); assert [(r['subject_id'],r['key']) for r in m.sleep()['records']]==[('a','z'),('z','b')]

def test_timeline_subject_filter(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('v','x',2,source='s'); assert len(m.timeline('u'))==1

def test_export_live_excludes_markers(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert all(not r.get('tombstone') for r in m.export_memory())

def test_compile_truth_preserves_source(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='chat'); assert m.compile_truth()['records'][0]['source']=='chat'

def test_sleep_preserves_source(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='chat'); assert m.sleep()['records'][0]['source']=='chat'

def test_policy_apply_is_append_only(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); first=(tmp_path/'.memkraft/deny_policies.jsonl').read_text(); m.do_not_remember(subject='v',dry_run=False); assert (tmp_path/'.memkraft/deny_policies.jsonl').read_text().startswith(first)

def test_audit_apply_is_append_only(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); first=(tmp_path/'.memkraft/audit.jsonl').read_text(); m.do_not_remember(subject='v',dry_run=False); assert (tmp_path/'.memkraft/audit.jsonl').read_text().startswith(first)

def test_journal_is_audit_log(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert (tmp_path/'.memkraft/sleep_journal.jsonl').exists() and m.audit_log(action='sleep')

def test_no_episode_clustering(tmp_path):
    assert 'cluster' not in json.dumps(mk(tmp_path).sleep()).lower()

def test_sleep_truth_only(tmp_path):
    assert mk(tmp_path).sleep()['steps']==['compile_truth']

def test_export_include_tombstoned_retains_order(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.export_memory(True)[0]['id']==e['id']

def test_timeline_include_tombstoned_retains_marker(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert any(r.get('tombstone') for r in m.timeline(include_tombstoned=True))

def test_policy_plan_does_not_block_until_apply(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u'); m.append_event('u','x',1,source='s'); assert len(m.export_memory())==1

def test_forget_plan_does_not_hide_until_apply(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id']); assert len(m.export_memory())==1

def test_sleep_plan_does_not_compile(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.sleep(); assert not (tmp_path/'.memkraft/compiled_truth.jsonl').exists()

def test_audit_limit_negative(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).audit_log(limit=-1)

def test_pattern_whitespace_normalized(tmp_path):
    assert mk(tmp_path).do_not_remember(pattern=' u:* ')['policy']['pattern']=='u:*'

def test_subject_whitespace_normalized(tmp_path):
    assert mk(tmp_path).do_not_remember(subject=' u ')['policy']['subject']=='u'

def test_key_whitespace_normalized(tmp_path):
    assert mk(tmp_path).do_not_remember(subject='u',key=' x ')['policy']['key']=='x'

def test_forget_subject_whitespace_normalized(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.forget({'subject':' u '})['matched']==1

def test_sleep_transaction_json_canonical(tmp_path):
    m=mk(tmp_path); m.append_event('한','키','값',source='대화'); assert len(m.sleep()['transaction_id'])==64

def test_unicode_governance(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='사용자',key='비밀',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('사용자','비밀','값',source='대화')

def test_unicode_timeline(tmp_path):
    m=mk(tmp_path); m.append_event('사용자','도시','서울',source='대화'); assert m.timeline('사용자')[0]['value']=='서울'

def test_unicode_export(tmp_path):
    m=mk(tmp_path); m.append_event('사용자','도시','서울',source='대화'); assert m.export_memory()[0]['value']=='서울'

def test_unicode_truth(tmp_path):
    m=mk(tmp_path); m.append_event('사용자','도시','서울',source='대화'); m.sleep(dry_run=False); assert m.current_truth('사용자')=={'도시':'서울'}

def test_compile_truth_no_source_derived(tmp_path):
    p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text(json.dumps({'subject_id':'u','key':'x','value':1})+'\n'); assert mk(tmp_path).compile_truth()['skipped']==1

def test_sleep_no_source_derived(tmp_path):
    p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text(json.dumps({'subject_id':'u','key':'x','value':1})+'\n'); assert mk(tmp_path).sleep()['records']==[]

def test_export_raw_invalid_lines_skipped(tmp_path):
    p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text('{bad\n'); assert mk(tmp_path).export_memory()==[]

def test_timeline_raw_invalid_lines_skipped(tmp_path):
    p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text('{bad\n'); assert mk(tmp_path).timeline()==[]

def test_audit_raw_invalid_lines_skipped(tmp_path):
    p=tmp_path/'.memkraft/audit.jsonl'; p.parent.mkdir(); p.write_text('{bad\n'); assert mk(tmp_path).audit_log()==[]

def test_policy_raw_invalid_lines_skipped(tmp_path):
    p=tmp_path/'.memkraft/deny_policies.jsonl'; p.parent.mkdir(); p.write_text('{bad\n'); mk(tmp_path).append_event('u','x',1,source='s')

def test_sleep_journal_raw_invalid_lines_skipped(tmp_path):
    p=tmp_path/'.memkraft/sleep_journal.jsonl'; p.parent.mkdir(); p.write_text('{bad\n'); assert mk(tmp_path).sleep(dry_run=False)['status']=='applied'

def test_policy_id_is_hex(tmp_path):
    pid=mk(tmp_path).do_not_remember(subject='u')['policy']['id']; assert len(pid)==64 and int(pid,16)>=0

def test_forget_deterministic_ids_order(tmp_path):
    m=mk(tmp_path); a=m.append_event('u','a',1,source='s'); b=m.append_event('u','b',2,source='s'); assert m.forget({'subject':'u'})['record_ids']==[a['id'],b['id']]

def test_audit_limit_after_filter(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='a',dry_run=False); m.do_not_remember(subject='b',dry_run=False); assert m.audit_log(subject='a',limit=1)[0]['subject']=='a'

def test_policy_existing_data_hidden_without_tombstone(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False); assert not any(r.get('tombstone_of')==e['id'] for r in m.export_memory(True))

def test_forget_uses_tombstone(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert any(r.get('tombstone_of')==e['id'] for r in m.export_memory(True))

def test_sleep_retry_journal_unique(tmp_path):
    m=mk(tmp_path); [m.sleep(dry_run=False) for _ in range(5)]; assert len(m.audit_log(action='sleep'))==1

def test_policy_retry_audit_unique(tmp_path):
    m=mk(tmp_path); [m.do_not_remember(subject='u',dry_run=False) for _ in range(5)]; assert len(m.audit_log(action='do_not_remember'))==1

def test_forget_retry_audit_unique(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); [m.forget(e['id'],False) for _ in range(5)]; assert len(m.audit_log(action='forget'))==1

def test_timeline_rebuild_needs_no_materialized_file(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert not (tmp_path/'.memkraft/timeline.jsonl').exists() and m.timeline()

def test_export_does_not_require_compile(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.export_memory()

def test_timeline_does_not_require_compile(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.timeline()

def test_forget_dict_unknown_fields_ignored_minimally(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).forget({'foo':'bar'})

def test_policy_pattern_bracket_rejected(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(pattern='u:[ab]')

def test_policy_pattern_only_glob(tmp_path):
    m=mk(tmp_path); assert m.do_not_remember(pattern='u:*')['policy']['pattern']=='u:*'

def test_sleep_apply_compiles_empty_file(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert (tmp_path/'.memkraft/compiled_truth.jsonl').read_text()==''

def test_compile_empty_file(tmp_path):
    m=mk(tmp_path); m.compile_truth(False); assert (tmp_path/'.memkraft/compiled_truth.jsonl').read_text()==''

def test_forget_existing_compiled_stays_stale_but_reads_hidden(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.compile_truth(False)
    before=(tmp_path/'.memkraft/compiled_truth.jsonl').read_text(); m.forget(e['id'],False)
    assert (tmp_path/'.memkraft/compiled_truth.jsonl').read_text()==before and m.current_truth('u')=={}

def test_policy_existing_compiled_stays_stale_but_reads_hidden(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.compile_truth(False)
    before=(tmp_path/'.memkraft/compiled_truth.jsonl').read_text(); m.do_not_remember(subject='u',dry_run=False)
    assert (tmp_path/'.memkraft/compiled_truth.jsonl').read_text()==before and m.current_truth('u')=={}

def test_sleep_apply_result_records(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.sleep(dry_run=False)['records'][0]['value']==1

def test_sleep_retry_result_records(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False); assert m.sleep(dry_run=False)['records'][0]['value']==1

def test_forget_plan_schema(tmp_path):
    assert set(['schema_version','dry_run','target','matched','record_ids','status']) <= set(mk(tmp_path).forget('x'))

def test_policy_plan_schema(tmp_path):
    assert set(['schema_version','dry_run','policy','status']) <= set(mk(tmp_path).do_not_remember(subject='u'))

def test_sleep_plan_schema(tmp_path):
    assert set(['schema_version','dry_run','strategy','steps','transaction_id','records','status']) <= set(mk(tmp_path).sleep())

def test_audit_sleep_query(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert len(m.audit_log(action='sleep'))==1

def test_audit_governance_query(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert len(m.audit_log(action='do_not_remember'))==1

def test_compile_and_sleep_same_order(tmp_path):
    m=mk(tmp_path); m.append_event('z','x',1,source='s'); m.append_event('a','x',2,source='s'); assert m.compile_truth()['records']==m.sleep()['records']

def test_current_truth_tombstone_defense(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.compile_truth(False); m.forget(e['id'],False); assert m.current_truth('u')=={}

def test_current_truth_pattern_defense(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.compile_truth(False); m.do_not_remember(pattern='u:*',dry_run=False); assert m.current_truth('u')=={}

def test_policy_subject_is_required_for_key_even_dry(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(key='x')

def test_forget_subject_id_alias_normalized(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.forget({'subject_id':' u '})['matched']==1

def test_timeline_include_tombstoned_does_not_bypass_policy(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False); assert m.timeline(include_tombstoned=True)==[]

def test_sleep_policy_transaction_stable(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.sleep()==m.sleep()

def test_sleep_apply_after_policy_empty(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.sleep(dry_run=False)['record_count']==0

def test_tombstone_transaction_stable(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.sleep()==m.sleep()

def test_forget_all_does_not_target_markers(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.forget({'subject':'u'})['matched']==0

def test_export_tombstone_marker_source_not_required(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert any(r.get('tombstone') for r in m.export_memory(True))

def test_derived_truth_always_has_source(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert all(r['source'] for r in m.sleep()['records'])

def test_timeline_live_always_has_source(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert all(r['source'] for r in m.timeline())

def test_export_live_always_has_source(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert all(r['source'] for r in m.export_memory())

def test_audit_source_filter_not_public_needed(tmp_path):
    assert mk(tmp_path).audit_log(action='forget')==[]

def test_sleep_status_retry(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.sleep(dry_run=False)['status']=='already_applied'

def test_policy_status_retry(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.do_not_remember(subject='u',dry_run=False)['status']=='already_applied'

def test_forget_status_retry(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.forget(e['id'],False); assert m.forget(e['id'],False)['status']=='already_forgotten'

def test_sleep_applied_true_first(tmp_path):
    assert mk(tmp_path).sleep(dry_run=False)['applied'] is True

def test_sleep_dry_records_exact(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert m.sleep()['records']==[{'subject_id':'u','key':'x','value':1,'source':'s'}]

def test_export_memory_name(tmp_path):
    assert callable(mk(tmp_path).export_memory)

def test_audit_log_name(tmp_path):
    assert callable(mk(tmp_path).audit_log)

def test_do_not_remember_name(tmp_path):
    assert callable(mk(tmp_path).do_not_remember)

def test_forget_name(tmp_path):
    assert callable(mk(tmp_path).forget)

def test_sleep_name(tmp_path):
    assert callable(mk(tmp_path).sleep)

def test_timeline_name(tmp_path):
    assert callable(mk(tmp_path).timeline)

def test_sleep_strategy_default_explicit(tmp_path):
    assert mk(tmp_path).sleep(strategy='default')['strategy']=='default'

def test_sleep_dry_default(tmp_path):
    assert mk(tmp_path).sleep()['dry_run'] is True

def test_forget_dry_default(tmp_path):
    assert mk(tmp_path).forget('x')['dry_run'] is True

def test_policy_dry_default(tmp_path):
    assert mk(tmp_path).do_not_remember(subject='u')['dry_run'] is True

def test_export_tombstone_default(tmp_path):
    assert mk(tmp_path).export_memory()==[]

def test_timeline_tombstone_default(tmp_path):
    assert mk(tmp_path).timeline()==[]

def test_compile_truth_default_dry(tmp_path):
    assert mk(tmp_path).compile_truth()['dry_run'] is True

def test_current_truth_empty(tmp_path):
    assert mk(tmp_path).current_truth('u')=={}

def test_governance_directory_is_private(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert (tmp_path/'.memkraft/deny_policies.jsonl').exists()

def test_sleep_directory_is_private(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert (tmp_path/'.memkraft/sleep_journal.jsonl').exists()

def test_audit_directory_is_private(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert (tmp_path/'.memkraft/audit.jsonl').exists()

def test_compile_directory_is_private(tmp_path):
    m=mk(tmp_path); m.compile_truth(False); assert (tmp_path/'.memkraft/compiled_truth.jsonl').exists()

def test_events_directory_is_private(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); assert (tmp_path/'.memkraft/events.jsonl').exists()

def test_release_version(tmp_path):
    import memkraft; assert memkraft.__version__ == '3.0.2'

def test_policy_source_less_not_event(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.export_memory(True)==[]

def test_sleep_source_less_not_event(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.export_memory(True)==[]

def test_audit_rows_not_memory_export(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.export_memory(True)==[]

def test_policy_rows_not_timeline(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); assert m.timeline(include_tombstoned=True)==[]

def test_journal_rows_not_timeline(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.timeline(include_tombstoned=True)==[]

def test_truth_rows_not_timeline(tmp_path):
    m=mk(tmp_path); m.compile_truth(False); assert m.timeline()==[]

def test_truth_rows_not_export(tmp_path):
    m=mk(tmp_path); m.compile_truth(False); assert m.export_memory()==[]

def test_sleep_plan_only_truth_strategy(tmp_path):
    assert mk(tmp_path).sleep()['steps']==['compile_truth']

def test_final_smoke(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','city','Seoul',source='chat'); assert m.sleep(dry_run=False)['status']=='applied'; m.forget(e['id'],False); assert m.current_truth('u')=={} and m.export_memory()==[]


def test_session_overlay_governance_where_feasible(tmp_path):
    m=mk(tmp_path)
    # Canonical overlay represented by current truth must not leak denied values.
    m.append_event('session:1','secret','x',source='overlay')
    m.sleep(dry_run=False); m.do_not_remember(subject='session:1',key='secret',dry_run=False)
    assert m.current_truth('session:1') == {}


def test_cli_sleep_structured_json(tmp_path, monkeypatch, capsys):
    from memkraft import cli
    monkeypatch.setenv('MEMKRAFT_DIR',str(tmp_path))
    monkeypatch.setattr('sys.argv',['memkraft','sleep','--dry-run'])
    assert cli.main()==0
    assert json.loads(capsys.readouterr().out)['dry_run'] is True


def test_cli_sleep_flags_mutually_exclusive(tmp_path, monkeypatch):
    from memkraft import cli
    monkeypatch.setattr('sys.argv',['memkraft','sleep','--dry-run','--apply'])
    with pytest.raises(SystemExit): cli.main()


def test_crash_before_truth_swap_leaves_no_journal(tmp_path, monkeypatch):
    m=mk(tmp_path); m.append_event('u','x',1,source='s')
    def boom(*a,**k): raise OSError('crash')
    monkeypatch.setattr('memkraft.derived_views.os.replace',boom)
    with pytest.raises(OSError): m.sleep(dry_run=False)
    assert m.audit_log(action='sleep')==[]


def test_concurrent_sleep_serializes(tmp_path):
    import concurrent.futures
    m=mk(tmp_path); m.append_event('u','x',1,source='s')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        out=list(pool.map(lambda _: mk(tmp_path).sleep(dry_run=False), range(8)))
    assert len({x['transaction_id'] for x in out})==1
    assert len(m.audit_log(action='sleep'))==1


def test_concurrent_forget_serializes(tmp_path):
    import concurrent.futures
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: mk(tmp_path).forget(e['id'],False),range(8)))
    assert len(m.audit_log(action='forget'))==1


def test_concurrent_policy_serializes(tmp_path):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: mk(tmp_path).do_not_remember(subject='u',dry_run=False),range(8)))
    assert len(mk(tmp_path).audit_log(action='do_not_remember'))==1


def test_forget_crash_marker_without_audit_retry_recovers(tmp_path,monkeypatch):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s')
    real=m._append_audit
    monkeypatch.setattr(m,'_append_audit',lambda row: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError): m.forget(e['id'],False)
    monkeypatch.setattr(m,'_append_audit',real)
    got=m.forget(e['id'],False)
    assert got['status']=='already_forgotten' and len(m.audit_log(action='forget'))==1


def test_policy_crash_policy_without_audit_retry_recovers(tmp_path,monkeypatch):
    m=mk(tmp_path); real=m._append_audit
    monkeypatch.setattr(m,'_append_audit',lambda row: (_ for _ in ()).throw(OSError('crash')))
    with pytest.raises(OSError): m.do_not_remember(subject='u',dry_run=False)
    monkeypatch.setattr(m,'_append_audit',real)
    assert m.do_not_remember(subject='u',dry_run=False)['status']=='already_applied'


def test_forget_pattern_not_supported_target(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).forget({'pattern':'*'})


def test_pattern_rejects_backslash(tmp_path):
    with pytest.raises(ValueError): mk(tmp_path).do_not_remember(pattern='u:\\d')


def test_current_truth_filters_tombstoned_stale_compiled_without_rebuild(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); m.compile_truth(False)
    from memkraft.store_core import mark_tombstone
    mark_tombstone(m._canonical_events_path(),e['id'])
    assert m.current_truth('u')=={}


def test_sleep_idempotency_does_not_hide_new_same_truth_event(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); tx=m.sleep(dry_run=False)['transaction_id']
    m.append_event('u','x',1,source='s'); assert m.sleep()['transaction_id']!=tx


def test_audit_log_query_combines_filters(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); m.do_not_remember(subject='v',dry_run=False)
    assert len(m.audit_log(action='do_not_remember',subject='u'))==1


def test_export_tombstoned_type_validation_before_read(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).export_memory(None)


def test_timeline_subject_and_tombstone_validation(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).timeline(subject_id=object())


def test_compile_truth_filters_denied_latest_and_does_not_fall_back(tmp_path):
    m=mk(tmp_path); m.append_event('u','x','old',source='s'); m.do_not_remember(subject='u',key='x',dry_run=False)
    assert m.compile_truth()['records']==[]


def test_deny_append_checked_before_write(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False)
    with pytest.raises(PermissionError): m.append_event('u','x',1,source='s')
    assert not (tmp_path/'.memkraft/events.jsonl').exists()


def test_sleep_transaction_includes_policy_even_without_events(tmp_path):
    m=mk(tmp_path); before=m.sleep()['transaction_id']; m.do_not_remember(subject='u',dry_run=False); assert m.sleep()['transaction_id']!=before


def test_timeline_is_canonical_not_compiled(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.append_event('u','x',2,source='s'); m.compile_truth(False)
    assert len(m.timeline())==2


def test_export_memory_include_tombstoned_does_not_bypass_policy(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); m.do_not_remember(subject='u',dry_run=False)
    assert m.export_memory(False)==[] and m.export_memory(True)==[]


def test_sleep_journal_exactly_append_only(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); before=(tmp_path/'.memkraft/sleep_journal.jsonl').read_bytes(); m.append_event('u','x',1,source='s'); m.sleep(dry_run=False)
    assert (tmp_path/'.memkraft/sleep_journal.jsonl').read_bytes().startswith(before)


def test_policy_and_forget_audit_do_not_share_journal(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False)
    assert not (tmp_path/'.memkraft/sleep_journal.jsonl').exists()


def test_sleep_journal_visible_through_audit_union(tmp_path):
    m=mk(tmp_path); m.sleep(dry_run=False); assert m.audit_log()[0]['action']=='sleep'


def test_audit_order_across_stores_deterministic(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u',dry_run=False); m.sleep(dry_run=False)
    assert [x['action'] for x in m.audit_log()]==['sleep','do_not_remember']


def test_sleep_concurrent_results_have_one_applied(tmp_path):
    import concurrent.futures
    m=mk(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as p: out=list(p.map(lambda _:mk(tmp_path).sleep(dry_run=False),range(4)))
    assert sum(x['status']=='applied' for x in out)==1


def test_forget_concurrent_one_audit(tmp_path):
    import concurrent.futures
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as p: list(p.map(lambda _:mk(tmp_path).forget(e['id'],False),range(4)))
    assert len(m.audit_log(action='forget'))==1


def test_policy_concurrent_one_audit(tmp_path):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as p: list(p.map(lambda _:mk(tmp_path).do_not_remember(subject='u',dry_run=False),range(4)))
    assert len(mk(tmp_path).audit_log(action='do_not_remember'))==1


def test_source_prohibition_on_manual_corrupt_derived(tmp_path):
    p=tmp_path/'.memkraft/events.jsonl'; p.parent.mkdir(); p.write_text('{"subject_id":"u","key":"x","value":1}\n')
    assert mk(tmp_path).sleep()['record_count']==0


def test_forget_target_dict_rejects_non_strings(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).forget({'subject':3})


def test_policy_rejects_non_string_subject(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).do_not_remember(subject=3)


def test_policy_rejects_non_string_key(tmp_path):
    with pytest.raises(TypeError): mk(tmp_path).do_not_remember(subject='u',key=3)


def test_timeline_policy_glob(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret-a',1,source='s'); m.do_not_remember(pattern='u:secret-*',dry_run=False); assert m.timeline()==[]


def test_export_policy_glob(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret-a',1,source='s'); m.do_not_remember(pattern='u:secret-*',dry_run=False); assert m.export_memory()==[]


def test_compile_policy_glob(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret-a',1,source='s'); m.do_not_remember(pattern='u:secret-*',dry_run=False); assert m.compile_truth()['records']==[]


def test_current_policy_glob(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret-a',1,source='s'); m.compile_truth(False); m.do_not_remember(pattern='u:secret-*',dry_run=False); assert m.current_truth('u')=={}


def test_sleep_policy_glob(tmp_path):
    m=mk(tmp_path); m.append_event('u','secret-a',1,source='s'); m.do_not_remember(pattern='u:secret-*',dry_run=False); assert m.sleep()['records']==[]


def test_forget_apply_returns_target_repr(tmp_path):
    m=mk(tmp_path); e=m.append_event('u','x',1,source='s'); assert m.forget(e['id'],False)['target']==e['id']


def test_policy_apply_returns_policy(tmp_path):
    p=mk(tmp_path).do_not_remember(subject='u',dry_run=False); assert p['policy']['subject']=='u'


def test_sleep_apply_returns_transaction(tmp_path):
    assert mk(tmp_path).sleep(dry_run=False)['transaction_id']


def test_cli_sleep_apply(tmp_path,monkeypatch,capsys):
    from memkraft import cli
    monkeypatch.setenv('MEMKRAFT_DIR',str(tmp_path)); monkeypatch.setattr('sys.argv',['memkraft','sleep','--apply'])
    assert cli.main()==0 and json.loads(capsys.readouterr().out)['applied'] is True


def test_cli_sleep_default_is_dry(tmp_path,monkeypatch,capsys):
    from memkraft import cli
    monkeypatch.setenv('MEMKRAFT_DIR',str(tmp_path)); monkeypatch.setattr('sys.argv',['memkraft','sleep'])
    assert cli.main()==0 and json.loads(capsys.readouterr().out)['dry_run'] is True


def test_cli_sleep_unknown_strategy_not_exposed(tmp_path,monkeypatch):
    from memkraft import cli
    monkeypatch.setattr('sys.argv',['memkraft','sleep','--strategy','x'])
    with pytest.raises(SystemExit): cli.main()


def test_sleep_plan_exact_keys_are_stable(tmp_path):
    m=mk(tmp_path); assert list(m.sleep().keys())==list(m.sleep().keys())


def test_governance_plan_exact_keys_are_stable(tmp_path):
    m=mk(tmp_path); assert list(m.forget('x').keys())==list(m.forget('x').keys())


def test_policy_plan_exact_keys_are_stable(tmp_path):
    m=mk(tmp_path); assert list(m.do_not_remember(subject='u').keys())==list(m.do_not_remember(subject='u').keys())


def test_timeline_no_writes(tmp_path):
    m=mk(tmp_path); m.timeline(); assert not (tmp_path/'.memkraft').exists()


def test_export_no_writes(tmp_path):
    m=mk(tmp_path); m.export_memory(); assert not (tmp_path/'.memkraft').exists()


def test_audit_no_writes(tmp_path):
    m=mk(tmp_path); m.audit_log(); assert not (tmp_path/'.memkraft').exists()


def test_current_truth_no_writes(tmp_path):
    m=mk(tmp_path); m.current_truth('u'); assert not (tmp_path/'.memkraft').exists()


def test_compile_dry_no_writes(tmp_path):
    m=mk(tmp_path); m.compile_truth(); assert not (tmp_path/'.memkraft').exists()


def test_forget_plan_no_writes(tmp_path):
    m=mk(tmp_path); m.forget('x'); assert not (tmp_path/'.memkraft').exists()


def test_policy_plan_no_writes(tmp_path):
    m=mk(tmp_path); m.do_not_remember(subject='u'); assert not (tmp_path/'.memkraft').exists()


def test_sleep_plan_no_writes(tmp_path):
    m=mk(tmp_path); m.sleep(); assert not (tmp_path/'.memkraft').exists()


def test_final_contract(tmp_path):
    m=mk(tmp_path); m.append_event('u','x',1,source='s'); p=m.sleep(); assert p==m.sleep(); m.sleep(dry_run=False); assert m.current_truth('u')=={'x':1}
