"""Postprocess immutable evaluation records without running or tuning any model."""
import argparse,json,hashlib
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('result');p.add_argument('--output',default='configs/round4-evaluation-artifacts.json');a=p.parse_args()
path=Path(a.result);r=json.loads(path.read_text());summary=r['summary'];groups={}
for name,stats in summary['strategies'].items():
    seqs=[s for s in r['records'] if s['strategy']==name]
    groups[name]={**stats,'complete_sequence_success_count':sum(all(row['joint_feasible'] for row in s['rows']) for s in seqs),
        'complete_sequence_denominator':len(seqs),'complete_sequence_success_definition':'All four arrival decisions have a full joint candidate within the shared compute budget; no real-flight or all-history safety claim.',
        'warm_runtime_s':stats['total_seconds'],
        'cold_runtime_plus_once_model_load_s':stats['total_seconds']+(r['model_load_seconds_once'] if 'learned' in name else 0)+r['adapter_load_seconds'].get(name,0)}
refids={s['template_id'] for s in r['records'] if s['strategy']=='high_budget_reference'}
matched={}
for name in groups:
    rows=[x for s in r['records'] if s['strategy']==name and s['template_id'] in refids for x in s['rows']]
    matched[name]={'decision_denominator':len(rows),'joint_feasible_decisions':sum(x['joint_feasible'] for x in rows)}
out={'schema_version':'round4-evaluation-artifacts-v1','primary_result':str(path.resolve()),
    'primary_result_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'protocol_identity_sha256':r['protocol_sha256'],'source_snapshot':'/root/autodl-tmp/chengkong-data/round4/evaluation-source-topology-v3',
    'source_manifest':'/root/autodl-tmp/chengkong-data/round4/evaluation-source-topology-v3/SOURCE-MANIFEST.json',
    'data_manifest':'/root/autodl-tmp/chengkong-data/round4/topology_sequences/data-manifest.json',
    'previous_protocol_correction':'/root/autodl-tmp/chengkong-data/round4/protocol-correction.json',
    'first_topology_result_retained':'/root/autodl-tmp/chengkong-data/round4/topology_evaluation/final-results.json',
    'first_topology_result_limitation':'Concurrent QA and candidate timing/identity bug; retained observed data, not main runtime result. Corrected-source idle reproduction did not tune models or rules.',
    'model_version':r['model_version'],'training_status':'not_retrained','model_status':'out_of_domain_stress_for_motion_and_sequences',
    'calibration_status':'no_new_calibration; no old coverage claim in new domain','parent_topologies':3,'parameter_sequences':6,'decisions_per_strategy':24,
    'task_occurrences_per_strategy':48,'unique_tasks_per_strategy':12,'model_load_seconds_once':r['model_load_seconds_once'],
    'reference_seconds':r['reference_seconds'],'reference_parent_count':r['reference_parent_count'],'elapsed_evaluation_seconds':r['elapsed_seconds'],
    'strategies':groups,'matched_reference_subset':matched,'all_strategy_common_success_decisions':summary['all_strategy_common_success_decisions'],
    'common_success':summary['common_success'],
    'limits':['Three deliberately hand-defined synthetic parent topologies; no iid field-population inference.',
              'Decision success counts future maintenance candidates, not actual aircraft execution.',
              'Full-sequence success is four successful maintenance decisions, not whole-trajectory safety probability.',
              'Evidence queries and wait are zero in the corrected topology test; active-evidence benefit not established.',
              'Common-success costs are cumulative from the common initial plan; absolute cost also saved.',
              'Warm runtimes omit once-per-loaded-model cold cost, which is explicitly added separately.']}
Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False))
print(json.dumps({'output':a.output,'sequence_success':{k:[v['complete_sequence_success_count'],v['complete_sequence_denominator']] for k,v in groups.items()}},ensure_ascii=False))
