"""JSON contracts and validation; canonical quantities use metres and simulation seconds."""
from __future__ import annotations

import math
import re
from typing import Any, TypedDict

JSON = dict[str, Any]
SUPPORTED_GEOMETRY = frozenset({'aabb', 'vertical_cylinder', 'capsule'})


class ContractError(ValueError):
    """Malformed or internally inconsistent input (status = invalid_input)."""
    status = 'invalid_input'


class UnsupportedData(ContractError):
    """Well-formed input outside the implemented contract."""
    status = 'unsupported'


class VersionContext(TypedDict):
    environment_version: int
    scenario_id: str
    rule_version: str
    model_version: str


class Constraint(VersionContext):
    id: str
    object_id: str
    kind: str
    geometry: JSON | None
    interval_s: list[float]
    source_ids: list[str]


class EvidenceRequest(VersionContext):
    id: str
    source_layer: str
    object_id: str
    field: str
    related_task_ids: list[str]
    deadline_s: float
    max_queries: int


class ConstructionEvent(TypedDict, total=False):
    id: str
    kind: str
    activity_id: str
    facility_id: str
    observed_at_s: float
    effective_at_s: float
    source_kind: str
    new_planned_end_s: float
    facility_removed: bool


class FlightTask(TypedDict):
    id: str
    aircraft_id: str
    plan_version: int
    priority: int
    departure_time_s: float
    deadline_s: float
    launch_pad_id: str
    landing_pad_id: str
    mandatory_services: list[JSON]
    trajectory: list[JSON]
    nominal_horizontal_speed_mps: float
    nominal_vertical_speed_mps: float
    protection_radius_m: float
    energy_assumptions: JSON


class WorldState(VersionContext):
    schema_version: str
    coordinate_system: JSON
    time_system: JSON
    as_of_s: float
    horizon_s: list[float]
    buildings: list[JSON]
    facilities: list[JSON]
    activities: list[JSON]
    pads: list[JSON]
    tasks: list[FlightTask]
    known_sources: list[JSON]
    scene_relations: list[JSON]
    event_log: list[JSON]


class ConflictEvidence(TypedDict):
    task_id: str
    segment_index: int
    constraint_id: str
    object_id: str
    kind: str
    interval_s: list[float]
    source_ids: list[str]
    entry_position_m: list[float]
    exit_position_m: list[float]
    environment_version: int
    scenario_id: str
    rule_version: str
    model_version: str
    plan_version: int
    trigger: str
    unprotected_overlap_intervals_s: list[list[float]]


class MaintenanceCandidate(TypedDict):
    id: str
    kind: str
    tasks: list[FlightTask]
    base_environment_version: int
    base_plan_versions: dict[str, int]
    rule_version: str
    model_version: str
    scenario_id: str
    valid_interval_s: list[float]
    changes: list[JSON]
    searches: list[JSON]
    status: str
    conditions: list[str]
    constraints_used: list[str]


class PlanAdoption(TypedDict, total=False):
    status: str
    adopted: bool
    record: JSON
    review: JSON


def number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractError(f'{label}: expected finite number, got {value!r}')
    if minimum is not None and value < minimum:
        raise ContractError(f'{label}: must be >= {minimum}')
    return float(value)


def stable_id(value: Any, label: str = 'id') -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value):
        raise ContractError(f'{label}: expected stable nonempty ASCII identifier')
    return value


def integer(value: Any, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f'{label}: expected integer >= {minimum}')
    return value


def mapping(value: Any, label: str) -> JSON:
    if not isinstance(value, dict):
        raise ContractError(f'{label}: expected object')
    return value


def sequence(value: Any, label: str) -> list:
    if not isinstance(value, list):
        raise ContractError(f'{label}: expected array')
    return value


def parse_quantity(value: Any, expected_unit: str) -> float:
    """Accept canonical numeric quantity or explicit {value,unit}; never guess units."""
    if isinstance(value, dict):
        if value.get('unit') != expected_unit:
            raise UnsupportedData(f'quantity unit {value.get("unit")!r}; expected {expected_unit!r}')
        value = value.get('value')
    return number(value, f'quantity [{expected_unit}]')


def parse_interval(value: Any, label: str = 'interval_s') -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ContractError(f'{label}: expected [start,end)')
    start, end = (number(v, label, minimum=0) for v in value)
    if start >= end:
        raise ContractError(f'{label}: requires start < end')
    return [start, end]


def vector(value: Any, label: str, size: int = 3) -> None:
    if not isinstance(value, list) or len(value) != size:
        raise ContractError(f'{label}: expected {size} coordinates')
    for component in value:
        number(component, label)


def validate_geometry(geometry: Any) -> str:
    """Unknown/missing geometry is retained explicitly for downstream not_checked."""
    if geometry is None:
        return 'unknown'
    g = mapping(geometry, 'geometry')
    kind = g.get('type')
    if not isinstance(kind, str) or not kind:
        raise ContractError('geometry.type: expected nonempty string')
    if kind == 'aabb':
        vector(g.get('min_m'), 'geometry.min_m')
        vector(g.get('max_m'), 'geometry.max_m')
        if any(a >= b for a, b in zip(g['min_m'], g['max_m'])):
            raise ContractError('aabb: every minimum must be below maximum')
    elif kind == 'vertical_cylinder':
        vector(g.get('center_xy_m'), 'geometry.center_xy_m', 2)
        number(g.get('radius_m'), 'radius_m', minimum=0)
        if number(g.get('z_min_m'), 'z_min_m') >= number(g.get('z_max_m'), 'z_max_m'):
            raise ContractError('cylinder: requires z_min_m < z_max_m')
    elif kind == 'capsule':
        vector(g.get('start_m'), 'geometry.start_m')
        vector(g.get('end_m'), 'geometry.end_m')
        number(g.get('radius_m'), 'radius_m', minimum=0)
    else:
        # Do not fabricate a usable approximation for unsupported geometry.
        return 'unsupported'
    return 'supported'


def _ids(items: Any, label: str) -> set[str]:
    ids: set[str] = set()
    for item in sequence(items, label):
        identifier = stable_id(mapping(item, label).get('id'), f'{label}.id')
        if identifier in ids:
            raise ContractError(f'{label}: duplicate id {identifier}')
        ids.add(identifier)
    return ids


def validate_task(task: JSON) -> None:
    mapping(task, 'task')
    stable_id(task.get('id'), 'task.id')
    stable_id(task.get('aircraft_id'), 'task.aircraft_id')
    integer(task.get('plan_version'), 'task.plan_version', 1)
    integer(task.get('priority'), 'task.priority', 0)
    for key in ('launch_pad_id', 'landing_pad_id'):
        stable_id(task.get(key), key)
    for key in ('departure_time_s', 'deadline_s', 'protection_radius_m'):
        number(task.get(key), key, minimum=0)
    for key in ('nominal_horizontal_speed_mps', 'nominal_vertical_speed_mps'):
        if number(task.get(key), key) <= 0:
            raise ContractError(f'{key}: must be positive')
    points = sequence(task.get('trajectory'), 'trajectory')
    if len(points) < 2:
        raise ContractError('trajectory: at least two samples required')
    previous = -1.0
    for point in points:
        mapping(point, 'trajectory point')
        current = number(point.get('time_s'), 'trajectory.time_s', minimum=0)
        if current <= previous:
            raise ContractError('trajectory times must be strictly increasing')
        vector(point.get('position_m'), 'trajectory.position_m')
        previous = current
    if points[0]['time_s'] != task['departure_time_s']:
        raise ContractError('departure_time_s must match first trajectory point')
    if task['departure_time_s'] >= task['deadline_s']:
        raise ContractError('deadline_s must be after departure_time_s')
    _ids(task.get('mandatory_services'), 'mandatory_services')
    for service in task['mandatory_services']:
        vector(service.get('position_m'), 'service.position_m')
        number(service.get('duration_s'), 'service.duration_s', minimum=0)
        number(service.get('position_tolerance_m'), 'position_tolerance_m', minimum=0)
        parse_interval(service.get('window_s'), 'service.window_s')
    energy = mapping(task.get('energy_assumptions'), 'energy_assumptions')
    if energy.get('unit') != 'synthetic_energy_unit':
        raise UnsupportedData('only explicitly synthetic energy units supported')
    for key in ('capacity', 'airborne_cost_per_s', 'ground_wait_cost_per_s', 'required_landing_reserve'):
        number(energy.get(key), f'energy.{key}', minimum=0)


def validate_snapshot(snapshot: JSON) -> None:
    mapping(snapshot, 'observed_snapshot')
    as_of = number(snapshot.get('as_of_s'), 'as_of_s', minimum=0)
    integer(snapshot.get('environment_version'), 'environment_version', 1)
    sources = _ids(snapshot.get('known_sources'), 'known_sources')
    for source in snapshot['known_sources']:
        observed = number(source.get('observed_at_s'), 'source.observed_at_s', minimum=0)
        if observed > as_of:
            raise ContractError('snapshot cannot contain a future source')
    buildings = _ids(snapshot.get('buildings'), 'buildings')
    pads = _ids(snapshot.get('pads'), 'pads')
    facilities = _ids(snapshot.get('facilities'), 'facilities')
    activities = _ids(snapshot.get('activities'), 'activities')
    tasks = _ids(snapshot.get('tasks'), 'tasks')
    groups = [sources, buildings, pads, facilities, activities, tasks]
    combined: set[str] = set()
    for group in groups:
        if combined & group:
            raise ContractError('object/source/task identifiers must be globally unique')
        combined |= group
    bounds = mapping(snapshot.get('bounds_m'), 'bounds_m')
    vector(bounds.get('min'), 'bounds.min')
    vector(bounds.get('max'), 'bounds.max')
    if any(a >= b for a, b in zip(bounds['min'], bounds['max'])):
        raise ContractError('bounds minima must be below maxima')
    for building in snapshot['buildings']:
        validate_geometry(building.get('geometry'))
    for pad in snapshot['pads']:
        vector(pad.get('position_m'), 'pad.position_m')
        integer(pad.get('capacity'), 'pad.capacity', 1)
        if not isinstance(pad.get('ground_wait_allowed'), bool):
            raise ContractError('pad.ground_wait_allowed must be boolean')
    for facility in snapshot['facilities']:
        for key in ('installed', 'removal_confirmed'):
            if not isinstance(facility.get(key), bool):
                raise ContractError(f'facility.{key} must be boolean')
        for geometry in sequence(facility.get('physical_geometry'), 'physical_geometry'):
            validate_geometry(geometry)
        if 'motion_model' in facility:
            from .motion import validate_motion
            validate_motion(facility['motion_model'])
            if isinstance(facility['motion_model'], dict):
                for source_id in facility['motion_model'].get('source_ids', []):
                    if source_id not in sources:
                        raise ContractError('motion parameter references missing source: '+source_id)
        removed = facility.get('removal_confirmed_at_s')
        if facility['removal_confirmed']:
            if removed is None or number(removed, 'removal_confirmed_at_s', minimum=0) > as_of:
                raise ContractError('confirmed removal requires an effective time <= as_of_s')
        elif removed is not None:
            raise ContractError('unconfirmed removal must have null removal time')
    for activity in snapshot['activities']:
        if activity.get('facility_id') not in facilities:
            raise ContractError('activity references missing facility')
        interval = parse_interval(activity.get('planned_interval_s'), 'planned_interval_s')
        validate_geometry(activity.get('activity_envelope'))
        status = activity.get('completion_status')
        confirmed = activity.get('completion_confirmed_at_s')
        if status == 'confirmed':
            if confirmed is None or not interval[0] <= number(confirmed, 'completion_confirmed_at_s') <= as_of:
                raise ContractError('confirmed completion requires a valid effective time <= as_of_s')
        elif status == 'not_confirmed':
            if confirmed is not None:
                raise ContractError('unconfirmed completion must have null completion time')
        else:
            raise UnsupportedData(f'unsupported activity completion status: {status!r}')
    for obj in snapshot['buildings'] + snapshot['facilities'] + snapshot['activities']:
        if obj.get('source_id') not in sources:
            raise ContractError(f'{obj["id"]}: missing source reference')
        for source_id in obj.get('source_ids', []):
            if source_id not in sources:
                raise ContractError(f'{obj["id"]}: missing source_ids reference')
    for task in snapshot['tasks']:
        validate_task(task)
        if task['launch_pad_id'] not in pads or task['landing_pad_id'] not in pads:
            raise ContractError('task references missing launch/landing pad')
    for relation in sequence(snapshot.get('scene_relations'), 'scene_relations'):
        mapping(relation, 'scene relation')
        if relation.get('subject') not in combined or relation.get('object') not in combined:
            raise ContractError('scene relation references missing entity')
        stable_id(relation.get('predicate'), 'scene relation predicate')


def validate_event(event: JSON, world: JSON | None = None) -> None:
    mapping(event, 'event')
    stable_id(event.get('id'), 'event.id')
    number(event.get('observed_at_s'), 'event.observed_at_s', minimum=0)
    number(event.get('effective_at_s'), 'event.effective_at_s', minimum=0)
    stable_id(event.get('source_kind'), 'event.source_kind')
    kind = event.get('kind')
    if kind == 'facility_added':
        facility = mapping(event.get('facility'), 'event.facility')
        stable_id(facility.get('id'), 'event.facility.id')
        for geometry in sequence(facility.get('physical_geometry'), 'event.facility.physical_geometry'):
            validate_geometry(geometry)
        if facility.get('removal_confirmed', False) or facility.get('installed', True) is not True:
            raise ContractError('new facility must be installed and not removed')
        if 'motion_model' in facility:
            from .motion import validate_motion
            validate_motion(facility['motion_model'])
    elif kind == 'activity_started' and 'activity' in event:
        activity = mapping(event['activity'], 'event.activity')
        stable_id(activity.get('id'), 'event.activity.id')
        stable_id(activity.get('facility_id'), 'event.activity.facility_id')
        parse_interval(activity.get('planned_interval_s'), 'event.activity.planned_interval_s')
        validate_geometry(activity.get('activity_envelope'))
        if activity.get('completion_status', 'not_confirmed') != 'not_confirmed':
            raise ContractError('activity start cannot assert completion')
    elif kind in ('planned_end_changed', 'activity_completion_confirmed', 'activity_started',
                  'activity_paused', 'activity_resumed', 'activity_envelope_changed'):
        stable_id(event.get('activity_id'), 'event.activity_id')
        if world is not None and event['activity_id'] not in {a['id'] for a in world['activities']}:
            raise ContractError('event references missing activity')
        if kind == 'planned_end_changed':
            number(event.get('new_planned_end_s'), 'event.new_planned_end_s', minimum=0)
            if event.get('completion_status', 'not_confirmed') != 'not_confirmed':
                raise ContractError('planned end update cannot confirm completion')
        elif kind == 'activity_completion_confirmed' and event.get('facility_removed', False) is not False:
            raise ContractError('completion event cannot remove a facility; use facility_removal_confirmed')
        if 'activity_envelope' in event:
            validate_geometry(event['activity_envelope'])
        if kind == 'activity_envelope_changed' and 'activity_envelope' not in event:
            raise ContractError('activity_envelope_changed requires an explicit envelope or null unknown')
    elif kind in ('facility_removal_confirmed', 'facility_geometry_changed', 'facility_pose_changed', 'facility_motion_changed'):
        stable_id(event.get('facility_id'), 'event.facility_id')
        if world is not None and event['facility_id'] not in {f['id'] for f in world['facilities']}:
            raise ContractError('event references missing facility')
        if kind == 'facility_motion_changed':
            if 'motion_model' not in event:
                raise ContractError('facility_motion_changed requires explicit model or null unknown')
            from .motion import validate_motion
            validate_motion(event['motion_model'])
        elif kind != 'facility_removal_confirmed':
            for geometry in sequence(event.get('physical_geometry'), 'event.physical_geometry'):
                validate_geometry(geometry)
    else:
        raise UnsupportedData(f'unsupported event kind: {kind!r}')


def validate_example(raw: JSON) -> None:
    mapping(raw, 'example')
    if raw.get('schema_version') != 'startup-example-v1':
        raise UnsupportedData('unsupported schema_version')
    coordinates = mapping(raw.get('coordinate_system'), 'coordinate_system')
    if (coordinates.get('type'), coordinates.get('unit'), coordinates.get('height_datum')) != (
            'local_cartesian', 'm', 'local_flat_ground_z0'):
        raise UnsupportedData('v1 supports local Cartesian metres, local_flat_ground_z0 only')
    if coordinates.get('axes') != ['x_east', 'y_north', 'z_up']:
        raise UnsupportedData('unsupported coordinate axes')
    time = mapping(raw.get('time_system'), 'time_system')
    if time.get('type') != 'relative_simulation_seconds' or time.get('interval_convention') != '[start,end)':
        raise UnsupportedData('v1 requires relative simulation seconds and [start,end) intervals')
    if not time.get('origin_label') or not coordinates.get('origin'):
        raise ContractError('coordinate and time origins must be explicit')
    horizon = parse_interval(raw.get('evaluation_horizon_s'), 'evaluation_horizon_s')
    validate_snapshot(raw.get('observed_snapshot'))
    if not horizon[0] <= raw['observed_snapshot']['as_of_s'] < horizon[1]:
        raise ContractError('initial snapshot must be within horizon')
    _ids(raw.get('scenario_definitions_for_simulator_only'), 'scenarios')
    for scenario in raw['scenario_definitions_for_simulator_only']:
        seen: dict[str, JSON] = {}
        for event in sequence(scenario.get('events'), 'scenario.events'):
            # References may target facilities/activities introduced by earlier events.
            # The chronological replay checks referential/state consistency atomically.
            validate_event(event)
            if event['id'] in seen and seen[event['id']] != event:
                raise ContractError('same event id has conflicting payloads')
            seen[event['id']] = event
    if raw.get('data_kind') != 'synthetic' or raw.get('is_real_case') is not False:
        raise UnsupportedData('startup-example-v1 import currently accepts explicitly synthetic records only')


def validate_world(world: JSON) -> None:
    validate_snapshot(world)
    stable_id(world.get('scenario_id'), 'scenario_id')
    parse_interval(world.get('horizon_s'), 'horizon_s')
    if 'scenario_definitions_for_simulator_only' in world:
        raise ContractError('decision world cannot include simulator scenario definitions')
    for key in ('rule_version', 'model_version'):
        stable_id(world.get(key), key)
    if not world['horizon_s'][0] <= world['as_of_s'] <= world['horizon_s'][1]:
        raise ContractError('world.as_of_s outside declared horizon')
    coords = mapping(world.get('coordinate_system'), 'world.coordinate_system')
    times = mapping(world.get('time_system'), 'world.time_system')
    if (coords.get('type'), coords.get('unit'), coords.get('height_datum')) != (
            'local_cartesian', 'm', 'local_flat_ground_z0'):
        raise UnsupportedData('unsupported world coordinate units or height datum')
    if times.get('type') != 'relative_simulation_seconds' or times.get('interval_convention') != '[start,end)':
        raise UnsupportedData('unsupported world time system')
    seen_events = set()
    for entry in sequence(world.get('event_log'), 'event_log'):
        identifier = stable_id(entry.get('id'), 'event_log.id')
        if identifier in seen_events:
            raise ContractError('duplicate event log id')
        seen_events.add(identifier)
        if number(entry.get('observed_at_s'), 'event_log.observed_at_s') > world['as_of_s']:
            raise ContractError('world event_log contains hidden future observation')
    if 'observed_events' in world:
        observed = sequence(world['observed_events'], 'observed_events')
        by_id = {}
        for event in observed:
            validate_event(event)
            if event['observed_at_s'] > world['as_of_s']:
                raise ContractError('observed_events contains hidden future observation')
            if event['id'] in by_id:
                raise ContractError('duplicate observed event id')
            by_id[event['id']] = event
        if by_id != {entry['id']: entry['event'] for entry in world['event_log']}:
            raise ContractError('observed events must match immutable receipt log')
        pending = sequence(world.get('pending_events', []), 'pending_events')
        if pending != [event for event in observed if event['effective_at_s'] > world['as_of_s']]:
            raise ContractError('pending events must match visible future-effective records')
