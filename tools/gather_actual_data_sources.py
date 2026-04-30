"""
Gather Actual Data from Logs and Shared Memory Captures

This script analyzes real data files to understand what data is actually
available from both sources, validating against documentation.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class DataSourceSample:
    """Sample of data from a specific source"""
    source_name: str
    file_path: str
    data_type: str  # "log" or "shm_capture"
    sample_data: Dict[str, Any] = field(default_factory=dict)
    all_fields: Set[str] = field(default_factory=set)
    sample_count: int = 0


@dataclass
class FieldPresence:
    """Track presence of a field across multiple sources"""
    field_name: str
    in_logs: bool = False
    in_shm_physics: bool = False
    in_shm_graphics: bool = False
    in_shm_static: bool = False
    log_samples: List[str] = field(default_factory=list)
    shm_samples: List[str] = field(default_factory=list)


def find_log_files(base_dir: Path) -> List[Path]:
    """Find all log files in the telemetry directory"""
    log_files = []
    # Try multiple possible locations
    telemetry_dirs = [
        base_dir / "telemetry" / "gamelogs",
        base_dir / "gamelogs",
        Path("telemetry/gamelogs"),
        Path("gamelogs")
    ]
    
    for telemetry_dir in telemetry_dirs:
        if telemetry_dir.exists():
            for file in telemetry_dir.glob("*.txt"):
                log_files.append(file)
            for file in telemetry_dir.glob("*.log"):
                log_files.append(file)
    
    return sorted(set(log_files))


def find_shm_captures(base_dir: Path) -> List[Path]:
    """Find all shared memory capture files"""
    captures = []
    telemetry_dir = base_dir / "telemetry"
    
    if telemetry_dir.exists():
        for file in telemetry_dir.glob("*.jsonl"):
            captures.append(file)
    
    return sorted(captures)


def analyze_log_file(log_path: Path) -> Dict[str, Any]:
    """Analyze a log file to extract all available data"""
    print(f"Analyzing log file: {log_path.name}")

    import re

    fields_found = set()
    sample_data = {}
    event_types = defaultdict(int)

    # Regex patterns from log_parser
    patterns = {
        'version': re.compile(r"Build release ([^,]+),"),
        'track_name_direct': re.compile(r"TRACK NAME (.+)"),
        'driver_line': re.compile(r"\tDriver (.+) on car ([\w_]+)"),
        'connect': re.compile(r"(\d+) connected(?: \(\d+\))? on car ([\w_]+), with new carId ([a-f0-9\-]+)"),
        'game_started': re.compile(r"\[gameplay\] \[info\] Game Started!\s*GameModeType_([A-Z_]+)\| (.+?) \| ([\w_]+) \| GameModeSelectionWeatherType_(\w+)"),
        'fuel_consumed': re.compile(r"\[gameplay\] \[info\] Energy source car ([a-f0-9\-]+) for driver [a-f0-9\-]+ hundredmeters done: (\d+) fuel consumed: ([\-\d.]+) L"),
        'track_limits': re.compile(r"\[physics\] \[info\] Limits: car ([a-f0-9\-]+) tyres out changed: \d+ -> (\d+) with ([\-\d.]+)m inside"),
        'race_split': re.compile(r"\[gameplay\] \[info\] Split completed for car ([a-f0-9\-]+): \((\d+) ms, splitindex (\d+)\)"),
        'practice_split': re.compile(r"\[gameplay\] \[info\] On Split start \d+ end \d+ id (\d+) splittime (\d+)"),
        'physics_lap': re.compile(r"\[physics\] \[info\] Lap test evOnLapCompleted (\d+) completed"),
        'lap_finish': re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\] \[gameplay\] \[info\] New lap carId ([a-f0-9\-]+): ([\d:.]+)"),
        'lap_validity': re.compile(r"\[network\] \[info\] Relevant onSplit for Combo \d+@\d+: laptime (\d+), valid (true|false), flags \d+, lap (\d+)"),
        'penalty': re.compile(r"\{PENALTY_ADDED_KEY\}"),
    }

    try:
        # Try multiple encodings
        encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
        for encoding in encodings:
            try:
                with open(log_path, 'r', encoding=encoding, errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        # Extract category
                        if line.startswith('['):
                            parts = line.split(']')
                            if len(parts) >= 2:
                                category = parts[1].strip('[]')
                                event_types[category] += 1

                        # Check each pattern
                        for pattern_name, pattern in patterns.items():
                            if pattern.search(line):
                                fields_found.add(pattern_name)

                # If we successfully read some lines, break
                if len(fields_found) > 0 or len(event_types) > 0:
                    break

            except Exception:
                continue

    except Exception as e:
        print(f"  Error reading log: {e}")

    sample_data = {
        'event_types': dict(event_types),
        'total_fields': len(fields_found),
        'field_sample': list(fields_found)
    }

    return {
        'fields': fields_found,
        'sample_data': sample_data,
        'event_types': dict(event_types)
    }


def analyze_shm_capture(capture_path: Path) -> Dict[str, Any]:
    """Analyze a shared memory capture to extract all available data"""
    print(f"Analyzing SHM capture: {capture_path.name}")

    physics_fields = set()
    graphics_fields = set()
    static_fields = set()

    sample_count = 0
    is_raw_dump = 'raw_dump' in capture_path.name

    try:
        with open(capture_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            total_lines = len(lines)

            # Sample from different parts of the file to catch when regions become available
            sample_indices = list(range(0, min(100, total_lines), 10))  # Every 10th frame in first 100
            sample_indices.extend(range(100, min(500, total_lines), 50))  # Every 50th frame in next 400
            sample_indices.extend(range(500, min(2000, total_lines), 200))  # Every 200th frame in next 1500
            sample_indices.extend(range(max(2000, total_lines - 100), total_lines, 10))  # Last 100 frames

            for idx in sample_indices:
                if idx >= total_lines:
                    continue

                line = lines[idx].strip()
                if not line:
                    continue

                try:
                    data = json.loads(line)

                    # Skip metadata records
                    if data.get('_record_type') == 'meta':
                        continue

                    sample_count += 1

                    if is_raw_dump:
                        # Raw dump format has physics_raw, graphics_raw, static_raw as hex
                        # These are raw hex strings, not decoded, so we can't extract field names
                        # Just note that the region exists
                        if 'physics_raw' in data and data['physics_raw']:
                            physics_fields.add('physics_raw_data')
                        if 'graphics_raw' in data and data['graphics_raw']:
                            graphics_fields.add('graphics_raw_data')
                        if 'static_raw' in data and data['static_raw']:
                            static_fields.add('static_raw_data')
                    else:
                        # Normal capture format with decoded regions
                        # Physics data - nested under regions
                        if 'regions' in data and 'physics' in data['regions']:
                            physics_fields.update(data['regions']['physics'].keys())

                        # Graphics data - nested under regions
                        if 'regions' in data and 'graphics' in data['regions']:
                            graphics_fields.update(data['regions']['graphics'].keys())

                        # Static data - nested under regions
                        if 'regions' in data and 'static' in data['regions']:
                            static_fields.update(data['regions']['static'].keys())

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"  Error reading capture: {e}")

    return {
        'physics_fields': physics_fields,
        'graphics_fields': graphics_fields,
        'static_fields': static_fields,
        'sample_count': sample_count
    }
def compare_with_documentation(found_fields: Dict[str, Set[str]]) -> Dict[str, Any]:
    """Compare found fields with documented fields"""
    
    # Documented fields from analysis - actual log event patterns
    documented_fields = {
        'log': {
            # Log parser patterns (actual game events)
            'lap_finish', 'lap_validity', 'race_split', 'practice_split',
            'physics_lap', 'penalty', 'connect', 'driver_line', 'version',
            'track_name_direct', 'fuel_consumed', 'track_limits', 'game_started'
        },
        'shm_physics': {
            'gas', 'brake', 'fuel', 'gear', 'rpms', 'steer_angle', 'speed_kmh',
            'velocity', 'acc_g', 'wheel_slip', 'wheel_load', 'wheels_pressure',
            'wheel_angular_speed', 'tyre_wear', 'tyre_dirty_level', 'tyre_core_temp',
            'camber_rad', 'suspension_travel', 'drs', 'tc', 'heading', 'pitch', 'roll',
            'cg_height', 'car_damage', 'number_of_tyres_out', 'pit_limiter_on', 'abs',
            'kers_charge', 'kers_input', 'auto_shifter_on', 'ride_height', 'turbo_boost',
            'ballast', 'air_density', 'air_temp', 'road_temp', 'local_angular_velocity',
            'final_ff', 'performance_meter', 'engine_brake', 'ers_recovery_level',
            'ers_power_level', 'ers_heat_charging', 'ers_is_charging', 'kers_current_kj',
            'drs_available', 'drs_enabled', 'brake_temp', 'clutch', 'tyre_temp_i',
            'tyre_temp_m', 'tyre_temp_o', 'is_ai_controlled', 'brake_bias', 'fx', 'fy',
            'slip_ratio', 'slip_angle', 'tcin_action', 'absin_action', 'suspension_damage',
            'tyre_temp', 'water_temp', 'brake_torque', 'pad_life', 'disc_life',
            'suspension_damage', 'tyre_damage', 'engine_damage', 'gearbox_damage',
            'brake_bias_raw', 'fuel_capacity', 'max_rpm', 'rev_limiter', 'steering_ratio',
            'max_turbo_boost', 'turbo_boost_pressure', 'rpm_limiter', 'engine_brake_mapping',
            'ers_mode', 'drs_activation_speed', 'drs_deactivation_speed', 'drs_available_speed',
            'tyre_pressure', 'tyre_temperature', 'brake_disc_temp', 'brake_pad_temp',
            'suspension_deflection', 'aero_balance', 'downforce', 'drag', 'lift',
            'lateral_acceleration', 'longitudinal_acceleration', 'vertical_acceleration',
            'yaw_rate', 'pitch_rate', 'roll_rate', 'steering_angle', 'steering_torque',
            'throttle_position', 'brake_position', 'clutch_position', 'gear_position',
            'rpm', 'speed', 'fuel_level', 'oil_pressure', 'oil_temperature', 'water_temperature',
            'intake_air_temperature', 'exhaust_gas_temperature', 'manifold_pressure',
            'boost_pressure', 'throttle_body_temperature', 'coolant_temperature',
            'ambient_temperature', 'track_temperature', 'rain_intensity', 'track_grip',
            'air_pressure', 'humidity', 'wind_speed', 'wind_direction'
        },
        'shm_graphics': {
            'rpm', 'display_speed_kmh', 'gear', 'gas_percent', 'brake_percent',
            'clutch_percent', 'steering_percent', 'water_temperature_c', 'air_temperature_c',
            'oil_temperature_c', 'g_forces', 'turbo_boost', 'steer_degrees', 'current_km',
            'current_lap_time_ms', 'predicted_lap_time_ms', 'delta_time_ms',
            'fuel_liter_current_quantity', 'fuel_liter_per_lap', 'laps_possible_with_fuel',
            'total_lap_count', 'current_pos', 'total_drivers', 'last_laptime_ms',
            'best_laptime_ms', 'session_phase_name', 'session_time_left_ms', 'session_total_lap',
            'session_current_lap', 'session_lap_length_km', 'timing_current_laptime',
            'timing_delta_current', 'timing_delta_last', 'timing_best_laptime',
            'timing_ideal_laptime', 'timing_total_time', 'timing_is_invalid', 'flag',
            'global_flag', 'max_gears', 'engine_type', 'diff_coast_raw_value',
            'diff_power_raw_value', 'player_fps', 'driver_name', 'driver_surname',
            'car_model', 'is_in_pit_box', 'is_in_pit_lane', 'is_valid_lap',
            'focused_car_id', 'player_car_id', 'active_cars', 'gap_behind',
            'max_fuel', 'max_turbo_boost', 'use_single_compound', 'assists_state'
        },
        'shm_static': {
            'ac_evo_version', 'session', 'session_name', 'event_id', 'session_id',
            'starting_grip', 'starting_ambient_temperature_c', 'starting_ground_temperature_c',
            'is_static_weather', 'is_timed_race', 'is_online', 'number_of_sessions',
            'nation', 'track', 'track_configuration', 'latitude', 'longitude',
            'track_length_m'
        }
    }
    
    # Compare
    log_found = found_fields.get('log', set())
    shm_physics_found = found_fields.get('shm_physics', set())
    shm_graphics_found = found_fields.get('shm_graphics', set())
    shm_static_found = found_fields.get('shm_static', set())

    documented_log = documented_fields['log']
    documented_shm_physics = documented_fields['shm_physics']
    documented_shm_graphics = documented_fields['shm_graphics']
    documented_shm_static = documented_fields['shm_static']

    return {
        'log': {
            'documented': documented_log,
            'found': log_found,
            'missing': documented_log - log_found,
            'unexpected': log_found - documented_log
        },
        'shm_physics': {
            'documented': documented_shm_physics,
            'found': shm_physics_found,
            'missing': documented_shm_physics - shm_physics_found,
            'unexpected': shm_physics_found - documented_shm_physics
        },
        'shm_graphics': {
            'documented': documented_shm_graphics,
            'found': shm_graphics_found,
            'missing': documented_shm_graphics - shm_graphics_found,
            'unexpected': shm_graphics_found - documented_shm_graphics
        },
        'shm_static': {
            'documented': documented_shm_static,
            'found': shm_static_found,
            'missing': documented_shm_static - shm_static_found,
            'unexpected': shm_static_found - documented_shm_static
        }
    }


def print_data_report(logs_data: List[Dict], shm_data: List[Dict], comparison: Dict):
    """Print comprehensive data report"""
    
    print("=" * 80)
    print("ACTUAL DATA SOURCE ANALYSIS")
    print("=" * 80)
    print()
    
    # Log files summary
    print(f"LOG FILES ANALYZED: {len(logs_data)}")
    if logs_data:
        all_log_fields = set()
        for log in logs_data:
            all_log_fields.update(log['fields'])
        
        print(f"Total unique fields found in logs: {len(all_log_fields)}")
        print(f"Sample fields: {sorted(list(all_log_fields))[:30]}")
        print()
    
    # SHM captures summary
    print(f"SHM CAPTURES ANALYZED: {len(shm_data)}")
    if shm_data:
        all_physics = set()
        all_graphics = set()
        all_static = set()
        total_samples = 0
        
        for shm in shm_data:
            all_physics.update(shm['physics_fields'])
            all_graphics.update(shm['graphics_fields'])
            all_static.update(shm['static_fields'])
            total_samples += shm['sample_count']
        
        print(f"Total samples processed: {total_samples}")
        print(f"Physics fields found: {len(all_physics)}")
        print(f"Graphics fields found: {len(all_graphics)}")
        print(f"Static fields found: {len(all_static)}")
        print()
        
        print("Physics sample fields:", sorted(list(all_physics))[:20])
        print("Graphics sample fields:", sorted(list(all_graphics))[:20])
        print("Static sample fields:", sorted(list(all_static))[:20])
        print()
    
    # Documentation comparison
    print("=" * 80)
    print("DOCUMENTATION VS ACTUAL DATA COMPARISON")
    print("=" * 80)
    print()
    
    for source, data in comparison.items():
        print(f"\n{source.upper()}:")
        print(f"  Documented fields: {len(data['documented'])}")
        print(f"  Found in actual data: {len(data['found'])}")
        print(f"  Missing from data: {len(data['missing'])}")
        print(f"  Unexpected in data: {len(data['unexpected'])}")
        
        if data['missing']:
            print(f"  Missing fields: {sorted(list(data['missing']))[:10]}")
        
        if data['unexpected']:
            print(f"  Unexpected fields: {sorted(list(data['unexpected']))[:10]}")
    
    print()
    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()
    
    # Check for critical missing fields
    critical_missing = []
    for source, data in comparison.items():
        if 'lap_time' in str(data['missing']) or 'sector' in str(data['missing']):
            critical_missing.append(f"{source}: missing timing data")
        if 'fuel' in str(data['missing']):
            critical_missing.append(f"{source}: missing fuel data")
    
    if critical_missing:
        print("⚠️  CRITICAL MISSING FIELDS:")
        for item in critical_missing:
            print(f"  {item}")
        print()
    
    # Check for unexpected fields that might be useful
    useful_unexpected = []
    for source, data in comparison.items():
        if data['unexpected']:
            useful_unexpected.extend([(source, field) for field in data['unexpected']])
    
    if useful_unexpected:
        print("💡 UNEXPECTED FIELDS (may be useful):")
        for source, field in useful_unexpected[:10]:
            print(f"  {source}: {field}")
        print()
    
    print("✅ Data gathering complete. Ready for integration planning.")


def main():
    """Main function to gather and analyze data sources"""

    # Set base directory - handle being run from telemetry subdirectory
    base_dir = Path.cwd()
    if base_dir.name == "telemetry":
        base_dir = base_dir.parent

    print(f"Base directory: {base_dir}")
    print()
    
    # Find data files
    log_files = find_log_files(base_dir)
    shm_captures = find_shm_captures(base_dir)
    
    print(f"Found {len(log_files)} log files")
    print(f"Found {len(shm_captures)} SHM captures")
    print()
    
    # Analyze logs
    logs_data = []
    for log_file in log_files[:5]:  # Limit to first 5 for performance
        log_analysis = analyze_log_file(log_file)
        logs_data.append(log_analysis)
    
    # Analyze SHM captures
    shm_data = []
    for capture in shm_captures[:3]:  # Limit to first 3 for performance
        shm_analysis = analyze_shm_capture(capture)
        shm_data.append(shm_analysis)
    
    # Aggregate all found fields
    found_fields = {
        'log': set(),
        'shm_physics': set(),
        'shm_graphics': set(),
        'shm_static': set()
    }
    
    for log in logs_data:
        found_fields['log'].update(log['fields'])
    
    for shm in shm_data:
        found_fields['shm_physics'].update(shm['physics_fields'])
        found_fields['shm_graphics'].update(shm['graphics_fields'])
        found_fields['shm_static'].update(shm['static_fields'])
    
    # Compare with documentation
    comparison = compare_with_documentation(found_fields)
    
    # Print report
    print_data_report(logs_data, shm_data, comparison)
    
    # Save results to file
    results = {
        'log_files_analyzed': len(logs_data),
        'shm_captures_analyzed': len(shm_data),
        'found_fields': {k: list(v) for k, v in found_fields.items()},
        'documentation_comparison': {
            k: {
                'documented': list(v['documented']),
                'found': list(v['found']),
                'missing': list(v['missing']),
                'unexpected': list(v['unexpected'])
            }
            for k, v in comparison.items()
        }
    }

    output_file = base_dir / "data_source_analysis_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
