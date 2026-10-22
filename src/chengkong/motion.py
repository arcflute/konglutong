"""Bounded tower-crane kinematics and conservative continuous-time checking.

No dynamics or physical measurement is inferred. Angles are unwrapped degrees;
load z uses the world's metre height datum. A midpoint solid expanded by V*dt/2
contains the solid at every time in the cell (Hausdorff speed bound). Checking
the *whole aircraft segment* against that solid prevents between-sample misses.
"""
from copy import deepcopy
import math

from .contracts import (ContractError, number, mapping, sequence, stable_id,
                        vector, parse_interval)
from .geometry import segment_occupancy, interpolate, UnsupportedGeometry

COMPONENTS = ('tower', 'boom', 'rope', 'load')
REQUIRED = ('base_m', 'tower_height_m', 'tower_radius_m', 'boom_length_m',
            'boom_radius_m', 'trolley_radius_m', 'rope_radius_m', 'load_radius_m',
            'angle_range_deg', 'load_z_range_m', 'max_slew_deg_per_s',
            'max_hoist_mps', 'initial_pose', 'program', 'source_ids', 'activity_id')


def validate_motion(model):
    """Missing fields stay unknown; malformed known values reject the update."""
    if model is None:
        return 'unknown'
    mapping(model, 'motion_model')
    if model.get('schema_version') != 'crane-motion-v1' or model.get('kind') != 'tower_crane':
        return 'unsupported'
    if any(model.get(key) is None for key in REQUIRED):
        return 'unknown'
    vector(model['base_m'], 'motion.base_m')
    stable_id(model['activity_id'], 'motion.activity_id')
    if not sequence(model['source_ids'], 'motion.source_ids'):
        raise ContractError('motion.source_ids must provide parameter provenance')
    for source in model['source_ids']:
        stable_id(source, 'motion.source_id')
    for key in REQUIRED:
        if key.endswith('_m') and key != 'base_m' or key in ('max_slew_deg_per_s', 'max_hoist_mps'):
            if key not in ('angle_range_deg', 'load_z_range_m'):
                number(model[key], 'motion.'+key, minimum=0)
    if model['tower_height_m'] <= 0 or model['boom_length_m'] <= 0:
        raise ContractError('motion tower height and boom length must be positive')
    if any(model[key] <= 0 for key in ('tower_radius_m','boom_radius_m','rope_radius_m','load_radius_m')):
        raise ContractError('declared physical component radii must be positive')
    if model['trolley_radius_m'] > model['boom_length_m']:
        raise ContractError('trolley must lie on the boom')
    for key in ('angle_range_deg', 'load_z_range_m'):
        vector(model[key], 'motion.'+key, 2)
        if model[key][0] > model[key][1]:
            raise ContractError('motion ranges must be ordered')
    if model['angle_range_deg'][1]-model['angle_range_deg'][0] > 360:
        raise ContractError('v1 supports at most one unwrapped 360-degree slew range')
    pivot_z = model['base_m'][2]+model['tower_height_m']
    if not model['base_m'][2] <= model['load_z_range_m'][0] <= model['load_z_range_m'][1] <= pivot_z:
        raise ContractError('load z range must lie between declared base and boom height')
    def pose(value):
        mapping(value, 'motion.pose')
        for key, bounds in (('angle_deg', 'angle_range_deg'), ('load_z_m', 'load_z_range_m')):
            v = number(value.get(key), 'motion.pose.'+key)
            if not model[bounds][0] <= v <= model[bounds][1]:
                raise ContractError('motion pose exceeds declared range: '+key)
    pose(model['initial_pose'])
    if model.get('parking_pose') is not None:
        pose(model['parking_pose'])
    program = sequence(model['program'], 'motion.program')
    if not program:
        return 'unknown'
    previous, previous_end = model['initial_pose'], -1.
    for row in program:
        lo, hi = parse_interval(row.get('interval_s'), 'motion.program.interval_s')
        if lo < previous_end:
            raise ContractError('motion program segments must be ordered without overlap')
        pose(row.get('start_pose')); pose(row.get('end_pose'))
        if row['start_pose'] != previous:
            raise ContractError('motion program must be continuous; gaps hold the preceding pose')
        for key, speed in (('angle_deg', 'max_slew_deg_per_s'), ('load_z_m', 'max_hoist_mps')):
            if abs(row['end_pose'][key]-row['start_pose'][key]) > model[speed]*(hi-lo)+1e-9:
                raise ContractError('motion program exceeds declared speed limit: '+key)
        previous, previous_end = row['end_pose'], hi
    if model.get('outside_program_policy', 'conservative_range') not in ('conservative_range', 'hold_last_pose'):
        raise ContractError('unsupported outside_program_policy')
    if model.get('activity_policy', 'recorded_envelope') not in ('recorded_envelope', 'moving_components_with_clearance'):
        raise ContractError('unsupported motion activity_policy')
    if model.get('activity_policy') == 'moving_components_with_clearance':
        number(model.get('activity_clearance_m'), 'activity_clearance_m', minimum=0)
    step = number(model.get('max_time_step_s', 1.), 'max_time_step_s', minimum=1e-4)
    inflation = number(model.get('max_cell_inflation_m', 0.5), 'max_cell_inflation_m', minimum=1e-5)
    if step > 5 or inflation > 5:
        raise ContractError('motion numerical limits: step <= 5 s and cell inflation <= 5 m')
    return 'supported'


def _program_pose(model, clock):
    previous = model['initial_pose']
    for row in model['program']:
        low, high = row['interval_s']
        if clock < low:
            return deepcopy(previous)
        if clock <= high:
            u = (clock-low)/(high-low)
            return {k: row['start_pose'][k]+u*(row['end_pose'][k]-row['start_pose'][k]) for k in ('angle_deg','load_z_m')}
        previous = row['end_pose']
    if model['program'] and model.get('outside_program_policy', 'conservative_range') == 'conservative_range':
        return None
    return deepcopy(previous)


def pose_at(descriptor, at_s):
    """Return bounded rule pose, or None when only the declared range is known."""
    model = descriptor['model']
    transition = descriptor.get('parking_transition')
    if transition is not None and at_s >= transition['at_s']:
        initial, target = transition['from_pose'], transition['target_pose']
        if initial is None or target is None:
            return None
        result = {}
        for key, speed in (('angle_deg','max_slew_deg_per_s'),('load_z_m','max_hoist_mps')):
            diff = target[key]-initial[key]
            result[key] = initial[key]+math.copysign(min(abs(diff),model[speed]*(at_s-transition['at_s'])),diff)
        return result
    shift, paused, held, last_pause = 0., False, None, None
    for command in descriptor.get('commands', []):
        at = command['at_s']
        if at > at_s:
            break
        kind = command['kind']
        if kind == 'pause' and not paused:
            held = _program_pose(model, at-shift)
            paused, last_pause = True, at
        elif kind == 'resume' and paused:
            shift += at-last_pause
            paused = False
        elif kind == 'complete':
            initial = held if paused else _program_pose(model, at-shift)
            target = model.get('parking_pose')
            if initial is None or target is None:
                return None
            delta = at_s-at
            result = {}
            for key, speed in (('angle_deg','max_slew_deg_per_s'),('load_z_m','max_hoist_mps')):
                diff = target[key]-initial[key]
                result[key] = initial[key]+math.copysign(min(abs(diff), model[speed]*delta), diff)
            return result
    return deepcopy(held) if paused else _program_pose(model, at_s-shift)


def motion_descriptor(model, world, facility_id=None, parking_transition=None):
    aid = model.get('activity_id') if isinstance(model, dict) else None
    kinds = {'activity_paused':'pause', 'activity_resumed':'resume', 'activity_completion_confirmed':'complete'}
    commands = [{'at_s': e['effective_at_s'], 'kind':kinds[e['kind']]} for e in world.get('observed_events', [])
                if e['kind'] in kinds and e.get('activity_id') == aid and e['effective_at_s'] <= world['as_of_s']]
    activity = next((a for a in world['activities'] if a['id'] == aid), None)
    if activity and activity['completion_status'] == 'confirmed' and not any(c['kind'] == 'complete' for c in commands):
        commands.append({'at_s':activity['completion_confirmed_at_s'], 'kind':'complete'})
    # Same-time semantics match construction replay: pause/resume, then completion.
    commands.sort(key=lambda c:(c['at_s'], c['kind']=='complete'))
    status = validate_motion(model)
    if status == 'supported' and (activity is None or facility_id is not None and activity['facility_id'] != facility_id):
        status = 'unknown'
    return {'schema_version':'crane-motion-occupancy-v1', 'model':deepcopy(model), 'commands':commands,
            'motion_status':status, 'parking_transition':deepcopy(parking_transition),
            'kinematic_assumption':'declared deterministic rule, not measured equipment telemetry'}


def component_speed(descriptor):
    model, part = descriptor['model'], descriptor['component']
    if part == 'tower':
        return 0.
    omega = math.radians(model['max_slew_deg_per_s'])
    if part == 'boom':
        return model['boom_length_m']*omega
    return model['trolley_radius_m']*omega+model['max_hoist_mps']


def component_envelope(model, component, clearance=0.):
    """Conservative broad phase for any declared pose, including parking."""
    x, y, z = model['base_m']; top = z+model['tower_height_m']
    if component == 'tower':
        return {'type':'vertical_cylinder', 'center_xy_m':[x,y], 'radius_m':model['tower_radius_m']+clearance,
                'z_min_m':z-clearance,'z_max_m':top+clearance}
    if component == 'boom':
        radius = model['boom_radius_m']+clearance
        extent = model['boom_length_m']+radius
        return {'type':'aabb', 'min_m':[x-extent,y-extent,top-radius], 'max_m':[x+extent,y+extent,top+radius]}
    radius = model['rope_radius_m' if component == 'rope' else 'load_radius_m']+clearance
    extent = model['trolley_radius_m']+radius
    low = model['load_z_range_m'][0]-radius
    high = (top if component == 'rope' else model['load_z_range_m'][1])+radius
    return {'type':'aabb', 'min_m':[x-extent,y-extent,low], 'max_m':[x+extent,y+extent,high]}


def geometry_at(descriptor, at_s):
    model, component = descriptor['model'], descriptor['component']
    clearance = descriptor.get('clearance_m', 0.)
    if descriptor.get('motion_status') != 'supported':
        raise UnsupportedGeometry('crane motion is '+descriptor.get('motion_status', 'unknown'))
    if component not in COMPONENTS:
        raise UnsupportedGeometry('unsupported crane component: '+str(component))
    if component == 'tower':
        return component_envelope(model, component, clearance)
    pose = pose_at(descriptor, at_s)
    if pose is None:
        return component_envelope(model, component, clearance)
    x, y, z = model['base_m']; pivot = [x,y,z+model['tower_height_m']]
    theta = math.radians(pose['angle_deg']); c, s = math.cos(theta), math.sin(theta)
    tip = [x+model['boom_length_m']*c,y+model['boom_length_m']*s,pivot[2]]
    trolley = [x+model['trolley_radius_m']*c,y+model['trolley_radius_m']*s,pivot[2]]
    load = [trolley[0],trolley[1],pose['load_z_m']]
    a, b, radius = {'boom':(pivot,tip,model['boom_radius_m']), 'rope':(trolley,load,model['rope_radius_m']),
                    'load':(load,load,model['load_radius_m'])}[component]
    return {'type':'capsule','start_m':a,'end_m':b,'radius_m':radius+clearance}


def motion_breakpoints(descriptor, low, high):
    """Include every pose discontinuity in information, and paused phase shift."""
    model = descriptor['model']; points = {low,high}; shifts = {0.}; shift = 0.; paused_at = None
    if descriptor.get('parking_transition'):
        points.add(descriptor['parking_transition']['at_s'])
    for command in descriptor.get('commands', []):
        at = command['at_s']; points.add(at)
        if command['kind'] == 'pause' and paused_at is None:
            paused_at = at
        elif command['kind'] == 'resume' and paused_at is not None:
            shift += at-paused_at; shifts.add(shift); paused_at = None
    # Include original and shifted boundaries conservatively; extra cuts are harmless.
    for row in model['program']:
        points.update(at+delta for at in row['interval_s'] for delta in shifts)
    return sorted(at for at in points if low <= at <= high)


def occupancy_intervals(p0, p1, t0, t1, constraint, radius=0.):
    """Absolute conservative conflict intervals for a same-time moving obstacle."""
    descriptor = constraint['motion']
    if descriptor.get('motion_status') != 'supported':
        raise UnsupportedGeometry('crane motion '+descriptor.get('motion_status','unknown'))
    low, high = max(t0,constraint['interval_s'][0]), min(t1,constraint['interval_s'][1])
    if low >= high:
        return [], {'cells_checked':0, 'max_inflation_m':0.}
    model = descriptor['model']; speed = component_speed(descriptor)
    dt = min(model.get('max_time_step_s',1.), 2*model.get('max_cell_inflation_m',.5)/speed) if speed else high-low
    points = motion_breakpoints(descriptor, low, high)
    if sum(math.ceil((b-a)/dt) for a,b in zip(points,points[1:])) > 20000:
        raise UnsupportedGeometry('dynamic_check_budget_exhausted: >20000 conservative time cells')
    intervals, checked, max_error, unknown_cells = [], 0, 0., 0
    for left, right in zip(points,points[1:]):
        n = max(1,math.ceil((right-left)/dt))
        for i in range(n):
            a, b = left+(right-left)*i/n, left+(right-left)*(i+1)/n
            mid = (a+b)/2
            unknown = pose_at(descriptor,mid) is None
            # Unknown future pose uses the full declared envelope, not a sample.
            error = 0. if unknown else speed*(b-a)/2
            g = geometry_at(descriptor,mid)
            pa, pb = interpolate(p0,p1,(a-t0)/(t1-t0)), interpolate(p0,p1,(b-t0)/(t1-t0))
            checked += 1; max_error = max(max_error,error); unknown_cells += unknown
            for u,v in segment_occupancy(pa,pb,g,radius+error):
                begin, end = a+u*(b-a), a+v*(b-a)
                if begin >= high or begin >= t1:
                    continue
                if intervals and begin <= intervals[-1][1]+1e-9:
                    intervals[-1][1] = max(intervals[-1][1],end)
                else:
                    intervals.append([begin,end])
    return intervals, {'cells_checked':checked,'max_inflation_m':max_error,'obstacle_speed_bound_mps':speed,
        'max_time_step_s':dt,'unknown_pose_envelope_cells':unknown_cells,
        'method':'continuous_flight_vs_midpoint_solid_with_Hausdorff_speed_bound',
        'interval_semantics':'conservative_possible_contact; not exact entry or exit time'}


def motion_snapshot(world, at_s=None):
    """Rendering uses the same visible model/version and pose as collision checks."""
    at = world['as_of_s'] if at_s is None else number(at_s,'motion_snapshot.time_s')
    if not world['horizon_s'][0] <= at <= world['horizon_s'][1]:
        raise ContractError('motion snapshot time outside declared horizon')
    result=[]
    for facility in world['facilities']:
        if 'motion_model' not in facility:
            continue
        if facility.get('removal_confirmed') and at >= facility['removal_confirmed_at_s']:
            continue
        if at < facility.get('installed_at_s',world['horizon_s'][0]):
            continue
        historical=facility
        for row in world.get('occupancy_history',{}).get(facility['id'],[]):
            if row['effective_at_s'] <= at:
                historical=row['object']
        if 'motion_model' not in historical:
            continue
        descriptor=motion_descriptor(historical['motion_model'],world,facility['id'],historical.get('motion_parking_transition'))
        row={'facility_id':facility['id'],'at_s':at,'environment_version':world['environment_version'],
             'rule_version':world['rule_version'],'motion_status':descriptor['motion_status'],
             'source_ids':historical.get('source_ids',[historical['source_id']]),'components':[],
             'data_kind':'parameterized_rule_prediction' if at > world['as_of_s'] else 'parameterized_record_rule_state'}
        if descriptor['motion_status']=='supported':
            row['pose']=pose_at(descriptor,at)
            row['pose_status']='conservative_range' if row['pose'] is None else 'declared_kinematic_rule'
            row['components']=[{'component':part,'geometry':geometry_at(dict(descriptor,component=part),at)} for part in COMPONENTS]
            row['activity_clearance_m']=historical['motion_model'].get('activity_clearance_m')
        result.append(row)
    return {'time_s':at,'environment_version':world['environment_version'],'facilities':result,
            'simulation_only':True,'telemetry_connected':False}
