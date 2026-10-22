"""Layer 2: conservative associations and continuous, versioned conflict evidence."""

from math import isfinite

from .resources import aircraft_continuity, waiting_reservations, capacity_conflicts

from .geometry import (EPS, UnsupportedGeometry, distance, geometry_bounds,
                       geometry_method, interpolate, segment_occupancy, vector)


def _finite(value):
    result = float(value)
    if not isfinite(result):
        raise ValueError("time/radius must be finite")
    return result


def trajectory_segments(task):
    """Validated linear segments, including separately declared preflight wait."""
    points = task["trajectory"]
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("trajectory requires at least two points")
    parsed = [(_finite(p["time_s"]), vector(p["position_m"])) for p in points]
    if any(a[0] >= b[0] for a,b in zip(parsed,parsed[1:])):
        raise ValueError("trajectory time must increase strictly")
    segments = [(index,a[0],b[0],a[1],b[1]) for index,(a,b) in enumerate(zip(parsed,parsed[1:]))]
    wait = task.get("ground_wait_interval_s")
    if wait is not None:
        start,end = map(_finite,wait)
        if start > end or end > parsed[-1][0]:
            raise ValueError("invalid ground wait interval")
        if start < parsed[0][0]:
            if abs(end-parsed[0][0]) > EPS:
                raise ValueError("separate ground wait must end at trajectory start")
            segments.insert(0,(-1,start,end,parsed[0][1],parsed[0][1]))
    return segments


def _metadata(world):
    return {key: world.get(key) for key in
            ("environment_version","scenario_id","rule_version","model_version","as_of_s","horizon_s")}


def _finish(report):
    report["checks_completed"] = not report["unchecked"]
    report["status"] = ("conflict" if report["conflicts"] else
                        "not_checked" if report["unchecked"] else "clear")
    return report


def _intersection_time(spatial_start, spatial_end, segment_start, segment_end, active_start, active_end):
    """Spatial contact is closed, both temporal occupancies are half open."""
    low = max(spatial_start,segment_start,active_start)
    high = min(spatial_end,segment_end,active_end)
    if low <= high and low < segment_end and low < active_end:
        return (low,high)
    return None


def check_task(task, constraints, world, from_time_s=None):
    report = {"task_id":task.get("id"),"plan_version":task.get("plan_version"),
              "conflicts":[],"unchecked":[],"associations":[],
              "screened_out_pairs":0,"continuous_pairs_checked":0,
              "scope":"all supplied constraints against every continuous trajectory segment",
              "spatial_boundary":"closed_contact_is_conflict", "time_interval":"[start,end)",
              **_metadata(world)}
    try:
        segments = trajectory_segments(task)
        radius = _finite(task["protection_radius_m"])
        if radius < 0:
            raise ValueError("negative protection radius")
        if "horizon_s" in world:
            h0,h1 = map(_finite,world["horizon_s"])
            if segments[0][1] < h0 or segments[-1][2] > h1:
                report["unchecked"].append({"reason":"trajectory_outside_checked_horizon",
                                             "trajectory_interval_s":[segments[0][1],segments[-1][2]],
                                             "checked_horizon_s":[h0,h1]})
    except (KeyError,TypeError,ValueError) as exc:
        report["unchecked"].append({"reason":"invalid_task_geometry_or_time","detail":str(exc)})
        return _finish(report)
    if from_time_s is not None:
        segments=[(i,max(t0,from_time_s),t1,interpolate(p0,p1,(max(t0,from_time_s)-t0)/(t1-t0)),p1)
                  for i,t0,t1,p0,p1 in segments if t1>from_time_s]
        report['future_check_from_s']=from_time_s
    for constraint in constraints:
        cid = constraint.get("id")
        try:
            cs,ce = map(_finite,constraint["interval_s"])
            if cs > ce:
                raise ValueError("constraint interval reversed")
            for key in ("environment_version","rule_version","model_version","scenario_id"):
                if constraint.get(key) != world.get(key):
                    raise ValueError(f"constraint {key} does not match world")
        except (KeyError,TypeError,ValueError) as exc:
            report["unchecked"].append({"constraint_id":cid,"reason":"invalid_constraint","detail":str(exc)})
            continue
        related = False
        for index,t0,t1,p0,p1 in segments:
            if t1 <= cs or ce <= t0:
                report["screened_out_pairs"] += 1
                continue
            try:
                geometry = constraint["geometry"]
                low,high = geometry_bounds(geometry,radius)
                if any(max(a,b) < c-EPS or min(a,b) > d+EPS for a,b,c,d in zip(p0,p1,low,high)):
                    report["screened_out_pairs"] += 1
                    continue
                related = True
                occupancies = segment_occupancy(p0,p1,geometry,radius)
                report["continuous_pairs_checked"] += 1
            except (UnsupportedGeometry,KeyError,TypeError,ValueError) as exc:
                report["unchecked"].append({"constraint_id":cid,"segment_index":index,
                                             "reason":"unsupported_or_invalid_geometry","detail":str(exc)})
                continue
            for lo,hi in occupancies:
                overlap = _intersection_time(t0+lo*(t1-t0),t0+hi*(t1-t0),t0,t1,cs,ce)
                if overlap is None:
                    continue
                a,b = overlap
                raw_overlaps = []
                for raw_lo,raw_hi in segment_occupancy(p0,p1,geometry,0.0):
                    raw_time = _intersection_time(t0+raw_lo*(t1-t0),t0+raw_hi*(t1-t0),t0,t1,cs,ce)
                    if raw_time is not None:
                        raw_overlaps.append(list(raw_time))
                report["conflicts"].append({"task_id":task.get("id"),"segment_index":index,
                    "constraint_id":cid,"object_id":constraint.get("object_id"),
                    "kind":constraint.get("kind"),"interval_s":[a,b],
                    "contact_at_single_instant":a == b,
                    "entry_position_m":interpolate(p0,p1,(a-t0)/(t1-t0)),
                    "exit_position_m":interpolate(p0,p1,(b-t0)/(t1-t0)),
                    "protection_radius_m":radius,"method":geometry_method(geometry),
                    "reason":constraint.get("reason",constraint.get("kind")),
                    "source_ids":constraint.get("source_ids",[]),
                    "trigger":"protection_margin_only" if not raw_overlaps else
                        ("activity_occupancy" if constraint.get("kind") == "activity" else "physical_intersection"),
                    "unprotected_overlap_intervals_s":raw_overlaps,
                    "plan_version":task.get("plan_version"),"model_version":world.get("model_version"),
                    "observed_at_s":constraint.get("observed_at_s"),
                    "environment_version":world.get("environment_version"),
                    "rule_version":world.get("rule_version"),"scenario_id":world.get("scenario_id")})
        if related:
            report["associations"].append({"task_id":task.get("id"),"constraint_id":cid,
                                            "method":"conservative_time_and_bounding_box"})
    return _finish(report)


def pad_reservations(tasks, world):
    """Conservative pad occupancy: ground wait, entire first ascent/last descent.

    No preparation interval before the declared task/wait is invented. This
    convention is intentionally explicit and is not a real-world clearance rule.
    """
    pads = {pad["id"]:pad for pad in world.get("pads",[])}
    reservations,unchecked = [],[]
    for task in tasks:
        try:
            segments = trajectory_segments(task)
            launch,land = pads[task["launch_pad_id"]],pads[task["landing_pad_id"]]
            intervals = []
            for _,t0,t1,p0,p1 in segments:
                at_launch0 = distance(p0,launch["position_m"]) <= EPS
                at_launch1 = distance(p1,launch["position_m"]) <= EPS
                at_land0 = distance(p0,land["position_m"]) <= EPS
                at_land1 = distance(p1,land["position_m"]) <= EPS
                if at_launch0:
                    intervals.append((launch["id"],t0,t1,"ground_wait" if at_launch1 else "launch"))
                if at_land1:
                    intervals.append((land["id"],t0,t1,"ground_wait" if at_land0 else "landing"))
            for pad_id in sorted({row[0] for row in intervals}):
                merged = []
                for _,start,end,kind in sorted(row for row in intervals if row[0] == pad_id):
                    if merged and start <= merged[-1][1]+EPS:
                        merged[-1][1] = max(end,merged[-1][1]); merged[-1][2].add(kind)
                    else:
                        merged.append([start,end,{kind}])
                for start,end,kinds in merged:
                    reservations.append({"resource_id":pad_id,"task_id":task["id"],
                                         "interval_s":[start,end],"kinds":sorted(kinds)})
        except (KeyError,TypeError,ValueError) as exc:
            unchecked.append({"task_id":task.get("id"),"reason":"pad_reservation_unchecked","detail":str(exc)})
    return reservations,unchecked


def check_joint_tasks(tasks, world, from_time_s=None):
    report = {"conflicts":[],"unchecked":[],"pair_segments_checked":0,
              "scope":"continuous aircraft separation, fixed aircraft assignment, pad capacities",
              "pad_occupancy_convention":"ground waits and entire takeoff/landing segments; no implicit preparation",
              **_metadata(world)}
    trajectories = {}
    for task in tasks:
        try:
            trajectories[task["id"]] = trajectory_segments(task)
            if from_time_s is not None:
                trajectories[task['id']]=[(i,max(t0,from_time_s),t1,interpolate(p0,p1,(max(t0,from_time_s)-t0)/(t1-t0)),p1)
                    for i,t0,t1,p0,p1 in trajectories[task['id']] if t1>from_time_s]
            radius = _finite(task["protection_radius_m"])
            if radius < 0:
                raise ValueError("negative protection radius")
        except (KeyError,TypeError,ValueError) as exc:
            trajectories.pop(task.get("id"),None)
            report["unchecked"].append({"task_id":task.get("id"),"reason":"invalid_task","detail":str(exc)})
    reuse = aircraft_continuity(tasks,world)
    report["aircraft_transitions"] = reuse["transitions"]
    report["conflicts"].extend(reuse["conflicts"])
    report["unchecked"].extend(reuse["unchecked"])
    report['additional_stationary_occupancies']=reuse['reservations']
    # Between-task stationary occupancies are physical aircraft presence, not
    # merely pad-capacity tokens. Rebuild the authoritative object constraints
    # here so callers cannot omit an obstacle list for these extra intervals.
    report['stationary_object_reports']=[]
    report['stationary_reference_reports']=[]
    if reuse['reservations']:
        from .world import generate_constraints
        from .reference_geometry import check_reference_task
        try:
            authoritative=generate_constraints(world)
            task_by_id={t['id']:t for t in tasks}
            for row in reuse['reservations']:
                owner=task_by_id[row['task_id']]
                proxy={**owner,'protection_radius_m':row['protection_radius_m'],
                    'departure_time_s':row['interval_s'][0],
                    'trajectory':[{'time_s':at,'position_m':list(row['position_m'])} for at in row['interval_s']]}
                proxy.pop('ground_wait_interval_s',None)
                checked=check_task(proxy,authoritative,world,from_time_s)
                checked['occupancy_kind']='aircraft_turnaround_and_charge'
                checked['resource_id']=row['resource_id']
                report['stationary_object_reports'].append(checked)
                report['unchecked'].extend(checked['unchecked'])
                for conflict in checked['conflicts']:
                    report['conflicts'].append({**conflict,'kind':'aircraft_turnaround_object_occupancy',
                        'object_constraint_kind':conflict['kind'],'resource_id':row['resource_id'],
                        'task_ids':row['related_task_ids']})
                reference=check_reference_task(proxy,authoritative,from_time_s)
                reference['occupancy_kind']='aircraft_turnaround_and_charge'
                reference['resource_id']=row['resource_id']
                report['stationary_reference_reports'].append(reference)
                report['unchecked'].extend(reference['unchecked'])
                covered={c['constraint_id'] for c in checked['conflicts']}
                for conflict in reference['conflicts']:
                    if conflict['constraint_id'] not in covered:
                        report['conflicts'].append({**conflict,'kind':'independent_reference_turnaround_object_occupancy',
                            'resource_id':row['resource_id'],'task_ids':row['related_task_ids'],
                            'interval_s':[max(row['interval_s'][0],from_time_s) if from_time_s is not None else row['interval_s'][0],row['interval_s'][1]]})
        except (KeyError,TypeError,ValueError) as exc:
            report['unchecked'].append({'reason':'turnaround_object_checks_incomplete','detail':str(exc)})
    extra_radii={}
    for index,row in enumerate(reuse['reservations']):
        tid=row['task_id'];lo,hi=row['interval_s']
        if from_time_s is not None:lo=max(lo,from_time_s)
        if lo<hi and tid in trajectories:
            segment_index=-1000-index
            point=row['position_m']
            trajectories[tid].append((segment_index,lo,hi,point,point))
            extra_radii[(tid,segment_index)]=row['protection_radius_m']
    for i,left in enumerate(tasks):
        for right in tasks[i+1:]:
            if left.get("id") not in trajectories or right.get("id") not in trajectories:
                continue
            ls,rs = trajectories[left["id"]],trajectories[right["id"]]
            for li,l0,l1,lp0,lp1 in ls:
                for ri,r0,r1,rp0,rp1 in rs:
                    radius=float(extra_radii.get((left['id'],li),left['protection_radius_m']))+float(extra_radii.get((right['id'],ri),right['protection_radius_m']))
                    start,end = max(l0,r0),min(l1,r1)
                    if start >= end:
                        continue
                    pa = interpolate(lp0,lp1,(start-l0)/(l1-l0))
                    pb = interpolate(lp0,lp1,(end-l0)/(l1-l0))
                    qa = interpolate(rp0,rp1,(start-r0)/(r1-r0))
                    qb = interpolate(rp0,rp1,(end-r0)/(r1-r0))
                    a,b = [x-y for x,y in zip(pa,qa)],[x-y for x,y in zip(pb,qb)]
                    sphere = {"type":"capsule","start_m":[0,0,0],"end_m":[0,0,0],"radius_m":radius}
                    report["pair_segments_checked"] += 1
                    for lo,hi in segment_occupancy(a,b,sphere):
                        if lo >= 1:
                            continue
                        report["conflicts"].append({"kind":"aircraft_separation",
                            "task_ids":[left["id"],right["id"]],"segment_indices":[li,ri],
                            "interval_s":[start+lo*(end-start),start+hi*(end-start)],
                            "required_separation_m":radius,"encounter_type":_encounter_type(lp0,lp1,rp0,rp1),"method":"continuous_exact_relative_motion_spheres"})
    reservations,unchecked = pad_reservations(tasks,world)
    report["unchecked"].extend(unchecked)
    reservations.extend(reuse["reservations"])
    waits,unchecked = waiting_reservations(tasks,world)
    reservations.extend(waits);report["unchecked"].extend(unchecked)
    report["reservations"] = reservations
    resources = world.get("pads",[]) + [dict(a,resource_kind="waiting_area") for a in world.get("waiting_areas",[])]
    conflicts,unchecked = capacity_conflicts(reservations,resources)
    report["conflicts"].extend(conflicts);report["unchecked"].extend(unchecked)
    if from_time_s is not None:
        end_by_id={t['id']:t['trajectory'][-1]['time_s'] for t in tasks}
        report['conflicts']=[c for c in report['conflicts'] if 'interval_s' in c and
            (c['interval_s'][1]>from_time_s or c['interval_s'][0]==c['interval_s'][1]>=from_time_s)] + [c for c in report['conflicts'] if 'interval_s' not in c and any(end_by_id.get(t,0)>from_time_s for t in c.get('task_ids',[]))]
        report['future_check_from_s']=from_time_s
    return _finish(report)


def _encounter_type(p0,p1,q0,q1):
    p=[b-a for a,b in zip(p0,p1)];q=[b-a for a,b in zip(q0,q1)]
    norm=(sum(x*x for x in p)*sum(x*x for x in q))**.5
    if norm<1e-12:return "stationary_or_waiting"
    cosine=sum(x*y for x,y in zip(p,q))/norm
    return "head_on" if cosine<-.9 else "same_direction" if cosine>.9 else "crossing"
