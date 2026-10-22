"""Saved round3 research models and auditable budgeted check ordering."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import time
import numpy as np
from .synthetic_round3 import (FEATURE_VERSION,PAIR_FEATURES,NODE_FEATURES,ACTION_FEATURES,QUANTILE_FEATURES,pair_features,graph_features,action_features)
from .model_runtime import matrix,normalize_adjacency
from .pair_keys import pair_key


class ResidualGNN:
    """Self and neighbor channels remain separate; all channel weights train."""
    def __init__(self,input_dim,hidden=12,seed=0):
        rng=np.random.default_rng(seed);self.W1=rng.normal(0,.15,(input_dim,hidden));self.b1=np.zeros(hidden)
        self.W2=rng.normal(0,.15,hidden);self.Wself=rng.normal(0,.15,hidden);self.b2=np.zeros(1)
        self.mean=np.zeros(input_dim);self.scale=np.ones(input_dim)
    def forward(self,features,adjacency):
        x=(np.asarray(features)-self.mean)/self.scale;a=normalize_adjacency(adjacency)
        z=x@self.W1+self.b1;h=np.maximum(0,z);message=a@h
        logits=np.clip(message@self.W2+h@self.Wself+self.b2[0],-30,30);p=1/(1+np.exp(-logits))
        return p,(x,a,z,h,message)
    def loss_and_gradients(self,features,adjacency,targets):
        p,(x,a,z,h,message)=self.forward(features,adjacency);y=np.asarray(targets);d=2*(p-y)*p*(1-p)/len(p)
        dz=(a.T@np.outer(d,self.W2)+np.outer(d,self.Wself))*(z>0)
        return float(np.mean((p-y)**2)),{'W1':x.T@dz,'b1':dz.sum(0),'W2':message.T@d,'Wself':h.T@d,'b2':np.array([d.sum()])}
    def fit(self,graphs,epochs=150,learning_rate=.015,edge_mode='weighted'):
        allx=np.vstack([matrix(g['node_features'],NODE_FEATURES) for g in graphs]);self.mean=allx.mean(0);self.scale=np.maximum(allx.std(0),1)
        names=['W1','b1','W2','Wself','b2'];m={n:np.zeros_like(getattr(self,n)) for n in names};v={n:x.copy() for n,x in m.items()};losses=[]
        for epoch in range(1,epochs+1):
            grads={n:np.zeros_like(x) for n,x in m.items()};loss=0
            for g in graphs:
                adjacency=g['adjacency'] if edge_mode=='weighted' else np.eye(len(g['targets']))
                value,grad=self.loss_and_gradients(matrix(g['node_features'],NODE_FEATURES),adjacency,g['targets']);loss+=value
                for n in names:grads[n]+=grad[n]/len(graphs)
            losses.append(loss/len(graphs))
            for n in names:
                m[n]=.9*m[n]+.1*grads[n];v[n]=.999*v[n]+.001*grads[n]**2
                setattr(self,n,getattr(self,n)-learning_rate*m[n]/(1-.9**epoch)/(np.sqrt(v[n]/(1-.999**epoch))+1e-8))
        return losses
    def save(self,path):np.savez(path,**{n:getattr(self,n) for n in ['W1','b1','W2','Wself','b2','mean','scale']})
    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:
            model=cls(data['W1'].shape[0],data['W1'].shape[1])
            for key in data.files:setattr(model,key,data[key])
        return model


def interval_scale(features,mode):
    return max(10.0,10+.15*float(features['planned_duration_s'])) if mode=='visible_duration_scale' else 1.0


def finite_group_calibration(residual_groups,alpha):
    if not 0<alpha<1:raise ValueError('alpha must lie in (0,1)')
    scores=sorted(max(vals) for vals in residual_groups.values() if vals);n=len(scores);k=math.ceil((n+1)*(1-alpha))
    if n==0 or k>n:
        return {'status':'insufficient_independent_groups','correction':None,'independent_template_groups':n,'order_statistic_index_1based':k,
            'interval_policy':'unbounded; do not claim finite guarantee','nominal_coverage':1-alpha}
    return {'status':'calibrated_synthetic','correction':float(scores[k-1]),'independent_template_groups':n,'order_statistic_index_1based':k,
        'nominal_coverage':1-alpha,'interval_policy':'finite split-conformal interval under stated family exchangeability'}


class Round3Bundle:
    def __init__(self,artifact_dir):
        import xgboost as xgb
        self.path=Path(artifact_dir).resolve();self.manifest=json.loads((self.path/'manifest.json').read_text());self.version=self.manifest['model_version']
        if self.manifest['feature_version']!=FEATURE_VERSION:raise ValueError('Round3 feature version mismatch')
        for filename,metadata in self.manifest['artifacts'].items():
            if hashlib.sha256((self.path/filename).read_bytes()).hexdigest()!=metadata['sha256']:raise ValueError('Model artifact checksum mismatch: '+filename)
        self.sensitivity_model=xgb.XGBRegressor();self.sensitivity_model.load_model(self.path/'sensitivity.json')
        self.ranker=xgb.XGBRanker();self.ranker.load_model(self.path/'ranker.json');self.gnn=ResidualGNN.load(self.path/'gnn.npz')
        self.lower=xgb.XGBRegressor();self.lower.load_model(self.path/'quantile_lower.json');self.upper=xgb.XGBRegressor();self.upper.load_model(self.path/'quantile_upper.json')
        self.calibration=json.loads((self.path/'calibration.json').read_text())
    def _meta(self):return {'status':'trained_synthetic','model_version':self.version,'data_kind':'synthetic','allows_check_skipping':False,
        'safety_authority':'none; full deterministic verification required','default_enabled':False,'scope':self.manifest['scope']}
    def sensitivity(self,world,constraints):
        pairs=[(t,c,pair_features(t,c,world)) for t in world['tasks'] for c in constraints]
        if not pairs:return dict(self._meta(),pair_order=[],scores={})
        p=self.sensitivity_model.predict(matrix([x[2] for x in pairs],PAIR_FEATURES));scores={pair_key(t['id'],c['id']):float(y) for (t,c,_),y in zip(pairs,p)}
        return dict(self._meta(),pair_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores)
    def association(self,world,constraints):
        graph=graph_features(world,constraints)
        if not graph['task_ids']:return dict(self._meta(),task_order=[],scores={})
        p,_=self.gnn.forward(matrix(graph['node_features'],NODE_FEATURES),graph['adjacency']);scores=dict(zip(graph['task_ids'],map(float,p)))
        return dict(self._meta(),task_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores,edge_types=graph['edge_types'])
    def rank_proposals(self,world,constraints,proposals):
        graph=graph_features(world,constraints);scores={};all_rows=[];groups=[]
        for proposal in proposals:
            if proposal['action'] not in ('keep','retain','delay','detour'):raise ValueError('Unsupported trained action')
            ids=proposal.get('task_ids',graph['task_ids']);rows=[action_features(t,proposal['action'],world,constraints,graph) for t in world['tasks'] if t['id'] in ids]
            groups.append((proposal['id'],len(all_rows),len(rows)));all_rows.extend(rows)
        predictions=self.ranker.predict(matrix(all_rows,ACTION_FEATURES)) if all_rows else []
        for identifier,start,count in groups:scores[identifier]=float(np.mean(predictions[start:start+count])) if count else 0
        return dict(self._meta(),proposal_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores,
            limitation='Local action group trained; mean task scores for joint order. Joint benefit separately measured, never assumed.')
    def end_error_interval(self,features):
        scale=interval_scale(features,self.calibration['scale_mode']);x=matrix([features],QUANTILE_FEATURES)
        lo,hi=sorted([float(self.lower.predict(x)[0])*scale,float(self.upper.predict(x)[0])*scale]);correction=self.calibration['correction']
        return dict(self._meta(),interval_s=[lo-correction*scale,hi+correction*scale] if correction is not None else [None,None],uncalibrated_interval_s=[lo,hi],
            calibration_status=self.calibration['status'],target='actual first activity end minus recorded planned end, seconds',
            nominal_coverage=self.calibration['nominal_coverage'],calibration_groups=self.calibration['independent_template_groups'],
            assumptions='Exchangeable synthetic independent families; within-family maximum nonconformity; single scalar outcome only',not_flight_safety_probability=True,
            application='Evidence/defer advisory only; never releases activity occupancy')


def priority_checks(world,constraints,model_bundle=None,*,max_pairs=None,timeout_s=None):
    """Real checks ordered BEFORE evaluation; unprocessed pairs remain not_checked.

A full invocation preserves every pair. This helper does not replace joint task
or candidate verification. Its timing includes model inference and feature work.
"""
    from .analysis import check_task
    started=time.perf_counter();pairs={pair_key(t['id'],c['id']):(t,c) for t in world['tasks'] for c in constraints}
    if len(pairs)!=len(world['tasks'])*len(constraints):raise ValueError('Duplicate task or constraint IDs cannot define complete checking obligations')
    infer=time.perf_counter()
    if model_bundle is not None:order=model_bundle.sensitivity(world,constraints)['pair_order'];source=model_bundle.version
    else:
        fs={k:pair_features(t,c,world) for k,(t,c) in pairs.items()}
        order=sorted(pairs,key=lambda k:(-int(fs[k]['geometry_missing']),-int(fs[k]['bbox_clearance_m']==0 and fs[k]['temporal_overlap_s']>0),k));source='bbox_unknown_rule'
    # A malformed predictor must never silently omit an obligation.
    if len(order)!=len(pairs) or set(order)!=set(pairs):raise ValueError('Check ordering must be a permutation of all required pairs')
    inference_seconds=time.perf_counter()-infer
    reports={t['id']:check_task(t,[],world) for t in world['tasks']};executed=[];first=None;pending=[]
    for key in order:
        t,c=pairs[key]
        if (max_pairs is not None and len(executed)>=max_pairs) or (timeout_s is not None and time.perf_counter()-started>=timeout_s):pending.append(key);continue
        item=check_task(t,[c],world);report=reports[t['id']]
        for field in ('conflicts','unchecked','associations'):report[field].extend(item[field])
        for field in ('screened_out_pairs','continuous_pairs_checked'):report[field]+=item[field]
        executed.append({'pair_id':key,'status':item['status'],'elapsed_s':time.perf_counter()-started})
        if item['conflicts'] and first is None:first=time.perf_counter()-started
    for key in pending:
        t,c=pairs[key];reports[t['id']]['unchecked'].append({'constraint_id':c['id'],'reason':'priority_check_budget_exhausted','pair_id':key})
    for report in reports.values():
        report['checks_completed']=not report['unchecked'];report['status']='conflict' if report['conflicts'] else 'not_checked' if report['unchecked'] else 'clear'
        report['scope']='All task/constraint pairs scheduled; pending obligations explicitly unchecked; independent joint review still required'
    return {'reports':reports,'order':order,'executed':executed,'unchecked_pair_ids':pending,'strategy':source,'inference_seconds':inference_seconds,
        'first_conflict_seconds':first,'total_seconds':time.perf_counter()-started,'required_pairs':len(pairs),'completed_pairs':len(executed),
        'all_pairs_checked':not pending,'allows_check_skipping':False}
