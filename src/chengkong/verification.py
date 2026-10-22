"""Independent candidate entry point: recheck all tasks, objects and resources.

The verifier never changes the world or current plans and never trusts candidate
copies of mission requirements. Feasibility is conditional on the declared model.
"""

from math import hypot, isfinite

from .analysis import check_joint_tasks, check_task, trajectory_segments
from .geometry import EPS, distance

IMMUTABLE_TASK_FIELDS = (
    "aircraft_id", "priority", "plan_version", "deadline_s", "launch_pad_id",
    "landing_pad_id", "mandatory_services", "nominal_horizontal_speed_mps",
    "nominal_vertical_speed_mps", "protection_radius_m", "energy_assumptions",
)


def _task_requirements(task, original, world):
    violations,unchecked = [],[]
    tid = task.get("id")
    def reject(reason, **detail):
        violations.append({"task_id":tid,"reason":reason,**detail})
    for field in IMMUTABLE_TASK_FIELDS:
        if task.get(field) != original.get(field):
            reject("mission_requirement_modified",field=field)
    if original["departure_time_s"] < world["as_of_s"] and any(
            task.get(field) != original.get(field)
            for field in ("trajectory","departure_time_s","ground_wait_interval_s")):
        reject("cannot_rewrite_already_started_task")
    metrics = {"task_id":tid}
    try:
        segments = trajectory_segments(task)
        points = task["trajectory"]
        start,end = segments[0][1],segments[-1][2]
        if abs(float(task["departure_time_s"])-float(points[0]["time_s"])) > EPS:
            reject("departure_does_not_match_trajectory_start")
        if start < float(original["departure_time_s"])-EPS:
            reject("departure_before_original_release")
        if end > float(original["deadline_s"])+EPS:
            reject("deadline_exceeded",arrival_s=end,deadline_s=original["deadline_s"])
        horizon = world["horizon_s"]
        if start < horizon[0]-EPS or end > horizon[1]+EPS:
            reject("trajectory_outside_checked_horizon",interval_s=[start,end])
        pads = {pad["id"]:pad for pad in world["pads"]}
        launch,land = pads[original["launch_pad_id"]],pads[original["landing_pad_id"]]
        if distance(points[0]["position_m"],launch["position_m"]) > EPS:
            reject("launch_position_mismatch")
        if distance(points[-1]["position_m"],land["position_m"]) > EPS:
            reject("landing_position_mismatch")
        lower,upper = world["bounds_m"]["min"],world["bounds_m"]["max"]
        radius = float(original["protection_radius_m"])
        horizontal_limit = float(original["nominal_horizontal_speed_mps"])
        vertical_limit = float(original["nominal_vertical_speed_mps"])
        if min(horizontal_limit,vertical_limit) <= 0 or radius < 0:
            raise ValueError("invalid motion/protection parameters")
        airborne,ground_wait,path_length = 0.0,0.0,0.0
        initial_ground_end = start
        still_initial_ground = True
        for index,t0,t1,p0,p1 in segments:
            dt = t1-t0
            for point in (p0,p1):
                # Flat ground is an allowed contact plane for takeoff/landing;
                # horizontal and upper airspace boundaries include protection.
                if (any(point[k]-radius < lower[k]-EPS or point[k]+radius > upper[k]+EPS for k in (0,1))
                        or point[2] < lower[2]-EPS or point[2]+radius > upper[2]+EPS):
                    reject("airspace_bounds",segment_index=index)
                    break
            horizontal_speed = hypot(p1[0]-p0[0],p1[1]-p0[1])/dt
            vertical_speed = abs(p1[2]-p0[2])/dt
            if horizontal_speed > horizontal_limit+EPS or vertical_speed > vertical_limit+EPS:
                reject("speed_limit",segment_index=index,horizontal_mps=horizontal_speed,vertical_mps=vertical_speed)
            path_length += distance(p0,p1)
            on_ground = abs(p0[2]-lower[2]) <= EPS and abs(p1[2]-lower[2]) <= EPS
            if on_ground:
                ground_wait += dt
                if distance(p0,p1) > EPS:
                    reject("ground_motion_not_supported",segment_index=index)
                pad = next((pad for pad in pads.values() if distance(p0,pad["position_m"]) <= EPS),None)
                if pad is None or not pad.get("ground_wait_allowed",False):
                    reject("ground_wait_location_unavailable",segment_index=index)
                if still_initial_ground:
                    initial_ground_end = t1
            else:
                airborne += dt
                still_initial_ground = False
        actual_departure = initial_ground_end
        if "actual_departure_time_s" in task and abs(float(task["actual_departure_time_s"])-actual_departure) > EPS:
            reject("actual_departure_does_not_match_trajectory")
        declared_wait = task.get("ground_wait_interval_s")
        if actual_departure > float(original["departure_time_s"])+EPS:
            expected_wait = [float(original["departure_time_s"]),actual_departure]
            if declared_wait is None or len(declared_wait) != 2 or any(abs(float(a)-b) > EPS for a,b in zip(declared_wait,expected_wait)):
                reject("delayed_departure_requires_explicit_ground_wait",expected_interval_s=expected_wait)
            if start > float(original["departure_time_s"])+EPS:
                reject("ground_wait_not_in_checked_trajectory")
        elif declared_wait is not None and declared_wait[1]-declared_wait[0] > EPS:
            reject("declared_ground_wait_does_not_match_trajectory")
        energy = original["energy_assumptions"]
        numeric = [float(energy[k]) for k in ("capacity","airborne_cost_per_s","ground_wait_cost_per_s","required_landing_reserve")]
        if not all(isfinite(v) and v >= 0 for v in numeric):
            raise ValueError("invalid energy assumptions")
        capacity,flight_cost,ground_cost,reserve = numeric
        used = airborne*flight_cost+ground_wait*ground_cost
        if used+reserve > capacity+EPS:
            reject("energy_capacity",energy_used=used,reserve=reserve,capacity=capacity)
        metrics.update({"airborne_duration_s":airborne,"ground_wait_duration_s":ground_wait,
                        "path_length_m":path_length,"energy_used":used,"landing_energy_remaining":capacity-used,
                        "energy_unit":energy.get("unit"),"actual_departure_time_s":actual_departure,"arrival_time_s":end})
        # Services require contiguous hovering/stationary occupancy. Use original
        # definitions and consume intervals in declared service order.
        cursor = start
        service_checks = []
        for service in original["mandatory_services"]:
            tolerance = float(service["position_tolerance_m"])
            duration = float(service["duration_s"])
            w0,w1 = map(float,service["window_s"])
            if tolerance < 0 or duration < 0 or w1 < w0:
                raise ValueError("invalid service requirement")
            intervals = []
            for _,t0,t1,p0,p1 in segments:
                if distance(p0,p1) <= EPS and distance(p0,service["position_m"]) <= tolerance+EPS:
                    if intervals and abs(intervals[-1][1]-t0) <= EPS:
                        intervals[-1][1] = t1
                    else:
                        intervals.append([t0,t1])
            found = None
            for t0,t1 in intervals:
                service_start = max(t0,w0,cursor)
                if service_start+duration <= min(t1,w1)+EPS:
                    found = [service_start,service_start+duration]
                    cursor = found[1]
                    break
            if found is None:
                reject("mandatory_service_unsatisfied",service_id=service.get("id"))
            service_checks.append({"service_id":service.get("id"),"satisfied":found is not None,"interval_s":found})
        metrics["service_checks"] = service_checks
    except (KeyError,TypeError,ValueError,IndexError,ZeroDivisionError) as exc:
        unchecked.append({"task_id":tid,"reason":"mission_checks_incomplete","detail":str(exc)})
    return violations,unchecked,metrics


def verify_candidate(candidate, world, constraints):
    """Recheck a complete joint candidate without modifying current plans."""
    report = {"candidate_id":candidate.get("id"),"status":"not_checked","feasible":False,
              "checks_completed":False,"violations":[],"unchecked":[],"task_reports":[],
              "task_metrics":[],"joint_report":None,"environment_version":world.get("environment_version"),
              "scenario_id":world.get("scenario_id"),"rule_version":world.get("rule_version"),
              "model_version":world.get("model_version"),
              "conditions":["Synthetic local flat-ground scenario and declared horizon only.",
                  "Fixed linear segment motion, spherical protection and synthetic linear energy model.",
                  "Cylinder cap rims use a conservative radial/axial expanded envelope.",
                  "No pad preparation phase before declared task/ground-wait time is modeled.",
                  "Unconfirmed activity remains possible throughout the declared conservative horizon.",
                  "Candidate is an advisory result; current plans are unchanged."]}
    expected = {"base_environment_version":world.get("environment_version"),
                "base_plan_versions":{task["id"]:task["plan_version"] for task in world["tasks"]},
                "rule_version":world.get("rule_version"),"model_version":world.get("model_version"),
                "scenario_id":world.get("scenario_id")}
    mismatches = [key for key,value in expected.items() if key not in candidate or candidate[key] != value]
    if mismatches:
        report.update(status="needs_recheck",version_mismatches=mismatches)
        report["unchecked"].append({"reason":"candidate_input_versions_changed_or_missing","fields":mismatches})
        return report
    # Regenerate the complete environment constraint set. A caller cannot skip
    # objects (or replace geometry) by supplying a smaller hand-picked list.
    from .world import generate_constraints
    try:
        authoritative = generate_constraints(world)
        expected_constraints = {row["id"]:row for row in authoritative}
        supplied_constraints = {row["id"]:row for row in constraints}
        if len(supplied_constraints) != len(constraints) or supplied_constraints != expected_constraints:
            report["unchecked"].append({"reason":"constraint_set_incomplete_or_modified",
                                         "action":"rechecked_regenerated_full_world_constraints"})
        constraints = authoritative
        report["constraint_source"] = "regenerated_from_current_world_all_objects"
    except (KeyError,TypeError,ValueError) as exc:
        report["unchecked"].append({"reason":"world_constraint_regeneration_failed","detail":str(exc)})
        return report
    try:
        v0,v1 = map(float,candidate["valid_interval_s"])
        h0,h1 = world["horizon_s"]
        if not all(isfinite(v) for v in (v0,v1)) or v0 >= v1 or v0 < h0 or v1 > h1:
            raise ValueError("valid interval must be nonempty and within checked horizon")
        tasks = candidate["tasks"]
        if not isinstance(tasks,list) or any(not isinstance(task,dict) for task in tasks):
            raise ValueError("candidate tasks must be a list of objects")
    except (KeyError,TypeError,ValueError) as exc:
        report["unchecked"].append({"reason":"invalid_candidate","detail":str(exc)})
        return report
    originals = {task["id"]:task for task in world["tasks"]}
    task_ids = [task.get("id") for task in tasks]
    if len(set(task_ids)) != len(task_ids) or set(task_ids) != set(originals):
        report["violations"].append({"reason":"joint_task_set_modified","expected":sorted(originals),
                                     "actual":task_ids})
    for task in tasks:
        if task.get("id") not in originals:
            continue
        violations,unchecked,metrics = _task_requirements(task,originals[task["id"]],world)
        report["violations"].extend(violations)
        report["unchecked"].extend(unchecked)
        report["task_metrics"].append(metrics)
        try:
            segments = trajectory_segments(task)
            if segments[0][1] < v0-EPS or segments[-1][2] > v1+EPS:
                report["violations"].append({"task_id":task["id"],"reason":"task_outside_candidate_valid_interval"})
        except (KeyError,TypeError,ValueError):
            pass  # Already recorded by requirement checks and task analysis.
        checked = check_task(task,constraints,world)
        report["task_reports"].append(checked)
        report["unchecked"].extend(checked["unchecked"])
        if checked["conflicts"]:
            report["violations"].append({"task_id":task["id"],"reason":"object_conflicts","count":len(checked["conflicts"])})
    joint = check_joint_tasks(tasks,world)
    report["joint_report"] = joint
    report["unchecked"].extend(joint["unchecked"])
    if joint["conflicts"]:
        report["violations"].append({"reason":"joint_resource_or_separation_conflicts","count":len(joint["conflicts"])})
    report["checks_completed"] = not report["unchecked"]
    report["feasible"] = not report["violations"] and report["checks_completed"]
    report["status"] = ("rejected" if report["violations"] else
                        "conditionally_feasible" if report["feasible"] else "not_checked")
    return report
