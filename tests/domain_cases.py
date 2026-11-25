from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load

CLEAN = [{'station_id': 'S1',
  'sample_at': '2026-11-01 08:00',
  'concentration': '1.0',
  'flow_m3s': '1',
  'detection_limit': '0.1',
  'qualifier': '',
  'unit': 'mg/L',
  'sample_id': 'W001'},
 {'station_id': 'S1',
  'sample_at': '2026-11-01 12:00',
  'concentration': '0',
  'flow_m3s': '2',
  'detection_limit': '0.4',
  'qualifier': 'ND',
  'unit': 'mg/L',
  'sample_id': 'W002'},
 {'station_id': 'S1',
  'sample_at': '2026-11-01 16:00',
  'concentration': '1.2',
  'flow_m3s': '1',
  'detection_limit': '0.1',
  'qualifier': '',
  'unit': 'mg/L',
  'sample_id': 'W003'},
 {'station_id': 'S2',
  'sample_at': '2026-11-01 08:00',
  'concentration': '50',
  'flow_m3s': '5',
  'detection_limit': '10',
  'qualifier': '',
  'unit': 'ug/L',
  'sample_id': 'W004'},
 {'station_id': 'S2',
  'sample_at': '2026-11-01 12:00',
  'concentration': '0.1',
  'flow_m3s': '5',
  'detection_limit': '0.05',
  'qualifier': '',
  'unit': 'mg/L',
  'sample_id': 'W005'},
 {'station_id': 'S3',
  'sample_at': '2026-11-01 08:00',
  'concentration': '0.8',
  'flow_m3s': '0',
  'detection_limit': '0.1',
  'qualifier': '',
  'unit': 'mg/L',
  'sample_id': 'W006'}]


def row(**changes):
    item = dict(CLEAN[0])
    item.update(changes)
    return item


def calc(rows):
    items, issues = prepare_rows(rows)
    return calculate_rows(items), issues


def run(rows):
    items, issues = prepare_rows(rows)
    return decide_rows(calculate_rows(items)), issues


def run_profile(profile):
    return run(load(profile)["rows"])
