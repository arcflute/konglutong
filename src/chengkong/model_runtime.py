"""Explicitly loaded synthetic-only models. Predictions rank work, never clear it."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path
import numpy as np

from .synthetic import (PAIR_FEATURES,NODE_FEATURES,ACTION_FEATURES,QUANTILE_FEATURES,
    FEATURE_VERSION,pair_features,graph_features,action_features)


def matrix(rows,names):
    result=np.asarray([[row[name] for name in names] for row in rows],dtype=float)
    if not np.isfinite(result).all():raise ValueError('Features must be finite and explicitly masked when unknown')
    return result


def normalize_adjacency(adjacency):
    a=np.asarray(adjacency,dtype=float)
    if a.ndim!=2 or a.shape[0]!=a.shape[1] or not np.isfinite(a).all() or (a<0).any():
        raise ValueError('Expected finite nonnegative square adjacency')
    degree=np.maximum(a.sum(axis=1),1e-12)
    return a/np.sqrt(degree[:,None]*degree[None,:])


class SmallGNN:
    """Trainable message passing: Z=ReLU(XW1+b1), p=sigmoid(D^-1/2 A D^-1/2 Z W2+b2)."""
    def __init__(self,input_dim,hidden=12,seed=0):
        rng=np.random.default_rng(seed)
        self.W1=rng.normal(0,.12,(input_dim,hidden));self.b1=np.zeros(hidden)
        self.W2=rng.normal(0,.12,hidden);self.b2=np.zeros(1)
        self.mean=np.zeros(input_dim);self.scale=np.ones(input_dim)
    def forward(self,features,adjacency):
        x=(np.asarray(features)-self.mean)/self.scale
        a=normalize_adjacency(adjacency);z=x@self.W1+self.b1;h=np.maximum(z,0)
        message=a@h;logits=np.clip(message@self.W2+self.b2[0],-30,30)
        p=1/(1+np.exp(-logits))
        return p,(x,a,z,message)
    def loss_and_gradients(self,features,adjacency,targets):
        p,(x,a,z,message)=self.forward(features,adjacency)
        y=np.asarray(targets);d=2*(p-y)*p*(1-p)/len(p)
        dz=(a.T@np.outer(d,self.W2))*(z>0)
        gradients={'W1':x.T@dz,'b1':dz.sum(axis=0),'W2':message.T@d,'b2':np.array([d.sum()])}
        return float(np.mean((p-y)**2)),gradients
    def fit(self,graphs,epochs=180,learning_rate=.02):
        all_x=np.vstack([matrix(g['node_features'],NODE_FEATURES) for g in graphs])
        self.mean=all_x.mean(0);self.scale=np.maximum(all_x.std(0),1)
        adam_m={n:np.zeros_like(getattr(self,n)) for n in ['W1','b1','W2','b2']}
        adam_v={n:np.zeros_like(v) for n,v in adam_m.items()};losses=[]
        for epoch in range(1,epochs+1):
            gradients={n:np.zeros_like(v) for n,v in adam_m.items()};loss=0
            for graph in graphs:
                value,grads=self.loss_and_gradients(matrix(graph['node_features'],NODE_FEATURES),graph['adjacency'],graph['targets'])
                loss+=value
                for name in gradients:gradients[name]+=grads[name]/len(graphs)
            losses.append(loss/len(graphs))
            for name,g in gradients.items():
                adam_m[name]=.9*adam_m[name]+.1*g;adam_v[name]=.999*adam_v[name]+.001*g*g
                step=learning_rate*(adam_m[name]/(1-.9**epoch))/(np.sqrt(adam_v[name]/(1-.999**epoch))+1e-8)
                setattr(self,name,getattr(self,name)-step)
        return losses
    def save(self,path):
        np.savez(path,**{name:getattr(self,name) for name in ['W1','b1','W2','b2','mean','scale']})
    @classmethod
    def load(cls,path):
        with np.load(path,allow_pickle=False) as data:
            result=cls(data['W1'].shape[0],data['W1'].shape[1])
            for name in data.files:setattr(result,name,data[name])
        return result


class ModelBundle:
    def __init__(self,artifact_dir):
        import xgboost as xgb
        self.path=Path(artifact_dir).resolve()
        self.manifest=json.loads((self.path/'manifest.json').read_text())
        if self.manifest['feature_version']!=FEATURE_VERSION:raise ValueError('Model feature version mismatch')
        self.version=self.manifest['model_version']
        for filename,metadata in self.manifest['artifacts'].items():
            if hashlib.sha256((self.path/filename).read_bytes()).hexdigest()!=metadata['sha256']:
                raise ValueError(f'Model artifact checksum mismatch: {filename}')
        self.sensitivity_model=xgb.XGBRegressor();self.sensitivity_model.load_model(self.path/'sensitivity.json')
        self.ranker=xgb.XGBRanker();self.ranker.load_model(self.path/'ranker.json')
        self.gnn=SmallGNN.load(self.path/'gnn.npz')
        self.lower=xgb.XGBRegressor();self.lower.load_model(self.path/'quantile_lower.json')
        self.upper=xgb.XGBRegressor();self.upper.load_model(self.path/'quantile_upper.json')
        self.calibration=json.loads((self.path/'calibration.json').read_text())
    def _meta(self):
        return {'status':'trained_synthetic','model_version':self.version,'data_kind':'synthetic',
            'allows_check_skipping':False,'safety_authority':'none; full deterministic verification required'}
    def sensitivity(self,world,constraints):
        pairs=[(t,c,pair_features(t,c,world)) for t in world['tasks'] for c in constraints]
        if not pairs:return dict(self._meta(),pair_order=[],scores={})
        predictions=self.sensitivity_model.predict(matrix([p[2] for p in pairs],PAIR_FEATURES))
        scores={f'{t["id"]}:{c["id"]}':float(p) for (t,c,_),p in zip(pairs,predictions)}
        return dict(self._meta(),pair_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores)
    def association(self,world,constraints):
        graph=graph_features(world,constraints)
        if not graph['task_ids']:return dict(self._meta(),task_order=[],scores={})
        p,_=self.gnn.forward(matrix(graph['node_features'],NODE_FEATURES),graph['adjacency'])
        scores=dict(zip(graph['task_ids'],map(float,p)))
        return dict(self._meta(),task_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores)
    def rank_proposals(self,world,constraints,proposals):
        graph=graph_features(world,constraints);scores={}
        for proposal in proposals:
            identifiers=proposal.get('task_ids',graph['task_ids'])
            rows=[action_features(t,proposal['action'],world,constraints,graph) for t in world['tasks'] if t['id'] in identifiers]
            if proposal['action'] not in ('keep','retain','delay','detour'):
                raise ValueError('Action not in trained proposal vocabulary')
            scores[proposal['id']]=float(np.mean(self.ranker.predict(matrix(rows,ACTION_FEATURES)))) if rows else 0.0
        return dict(self._meta(),proposal_order=sorted(scores,key=lambda k:(-scores[k],k)),scores=scores,
            limitation='Mean of task-local proposal scores; joint optimality is not claimed')
    def end_error_interval(self,features):
        x=matrix([features],QUANTILE_FEATURES)
        lo,hi=sorted([float(self.lower.predict(x)[0]),float(self.upper.predict(x)[0])])
        correction=self.calibration['correction_s']
        return dict(self._meta(),interval_s=[lo-correction,hi+correction],uncalibrated_interval_s=[lo,hi],
            target='actual activity end minus recorded planned end, seconds',
            nominal_coverage=self.calibration['nominal_coverage'],
            calibration_groups=self.calibration['independent_template_groups'],
            assumptions='Exchangeable synthetic template families; calibration uses maximum residual per family',
            not_flight_safety_probability=True)
