"""Independent reference checks: convex distance minimization, no geometry imports.

This deliberately slower check uses distance-to-convex-solid minimization along
the time-clipped segment instead of planner occupancy roots. It supports the
same conservative cylinder convention; it is numerical (100 ternary iterations,
1e-7 metre distance tolerance), not a formal exact-arithmetic certificate.
"""
import math

TOL=1e-7


def _mix(a,b,u):
    return [x+(y-x)*u for x,y in zip(a,b)]


def _distance(point,g,radius):
    if g['type']=='aabb':
        return math.sqrt(sum(max(lo-x,0.,x-hi)**2 for x,lo,hi in zip(point,g['min_m'],g['max_m'])))-radius
    if g['type']=='capsule':
        start=g['start_m'];v=[b-a for a,b in zip(start,g['end_m'])]
        vv=sum(x*x for x in v)
        u=max(0.,min(1.,sum((x-a)*b for x,a,b in zip(point,start,v))/vv)) if vv else 0.
        return math.dist(point,[a+u*b for a,b in zip(start,v)])-g['radius_m']-radius
    if g['type']=='vertical_cylinder':
        radial=math.hypot(point[0]-g['center_xy_m'][0],point[1]-g['center_xy_m'][1])-g['radius_m']-radius
        axial=max(g['z_min_m']-radius-point[2],point[2]-g['z_max_m']-radius)
        # Max of convex functions remains convex and gives the same expanded cylinder.
        return max(radial,axial)
    raise ValueError('reference geometry unsupported: '+str(g.get('type')))


def segment_intersects(a,b,g,radius=0.,end_exclusive=True):
    """Convex minimization includes tangency but excludes sole final endpoint contact."""
    left,right=0.,1.
    for _ in range(100):
        u=(2*left+right)/3;v=(left+2*right)/3
        if _distance(_mix(a,b,u),g,radius)<=_distance(_mix(a,b,v),g,radius):right=v
        else:left=u
    u=(left+right)/2
    options=[(0.,_distance(a,g,radius)),(u,_distance(_mix(a,b,u),g,radius)),(1.,_distance(b,g,radius))]
    best,value=min(options,key=lambda x:x[1])
    if value > TOL:return False
    if end_exclusive and best>1-1e-9 and _distance(b,g,radius)>=-TOL:
        return False
    return True


def check_reference_task(task,constraints,from_time_s=None):
    hits=[];unchecked=[];pairs=0
    for index,(a,b) in enumerate(zip(task['trajectory'],task['trajectory'][1:])):
        t0,t1=a['time_s'],b['time_s']
        for c in constraints:
            low=max(t0,c['interval_s'][0],from_time_s if from_time_s is not None else t0)
            high=min(t1,c['interval_s'][1])
            if low>=high:continue
            try:
                p=_mix(a['position_m'],b['position_m'],(low-t0)/(t1-t0))
                q=_mix(a['position_m'],b['position_m'],(high-t0)/(t1-t0));pairs+=1
                if 'motion' in c:
                    intersects = dynamic_intersects(p,q,low,high,c['motion'],task['protection_radius_m'])
                else:
                    intersects = segment_intersects(p,q,c['geometry'],task['protection_radius_m'])
                if intersects:
                    hits.append({'task_id':task['id'],'constraint_id':c['id'],'segment_index':index})
            except (KeyError,ValueError,TypeError,ZeroDivisionError) as exc:
                unchecked.append({'task_id':task['id'],'constraint_id':c.get('id'),'reason':'independent_reference_incomplete','detail':str(exc)})
    return {'method':'independent_convex_distance_minimization_100_iterations_and_dynamic_Lipschitz_branch_bounds',
        'distance_tolerance_m':TOL,'conflicts':hits,'unchecked':unchecked,'pairs_checked':pairs}


def dynamic_intersects(a,b,t0,t1,descriptor,radius):
    """Independent coupled-time bound, not planner/static root reuse.

    Distance to the moving solid is (aircraft speed + obstacle speed)-Lipschitz
    on each continuous rule segment. Positive midpoint distance above that
    bound proves separation; unresolved intervals <= 0.01 s are conservatively
    occupied. The rule kinematics and declared speed limits are shared inputs,
    but the collision procedure imports no primary geometry primitives.
    """
    from .motion import geometry_at, pose_at, motion_breakpoints, component_speed
    if descriptor.get('motion_status') != 'supported':
        raise ValueError('dynamic reference unsupported/unknown motion')
    flight_speed = math.dist(a,b)/(t1-t0)
    obstacle_speed = component_speed(descriptor)
    cuts = motion_breakpoints(descriptor,t0,t1)
    stack = list(zip(cuts,cuts[1:])); iterations=0
    while stack:
        lo,hi=stack.pop(); mid=(lo+hi)/2;iterations+=1
        if iterations>50000:
            raise ValueError('independent dynamic reference budget exhausted')
        p=_mix(a,b,(mid-t0)/(t1-t0)); g=geometry_at(descriptor,mid)
        d=_distance(p,g,radius)
        speed=flight_speed+(0. if pose_at(descriptor,mid) is None else obstacle_speed)
        if d > speed*(hi-lo)/2+TOL:
            continue
        if d <= TOL or hi-lo<=0.01:
            return True
        stack.extend(((lo,mid),(mid,hi)))
    return False
