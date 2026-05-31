#!/usr/bin/env python3
"""Parse an AoE2 replay into an MVP-friendly analytics report.

This version intentionally extracts much more than the UI currently displays:
- report-friendly match/player/event/insight fields
- raw fast-operation action stream
- all captured chat messages, unless limited by CLI flags
- sync stat rows and per-player timeseries, when the rec contains them
- all decoded viewlock rows by default
- postgame payloads, when available
- raw JSON-safe header data
- optional mgz.model serialized output
- optional mgz.summary output

Important:
- The final parse result is printed to stdout as JSON.
- Debug logs are written to stderr so the Node server can safely parse stdout.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any

from mgz.const import DE_MAP_NAMES, MAP_NAMES, SPEEDS
from mgz.fast import meta, operation
from mgz.fast.enums import Action, Operation
from mgz.fast.header import parse as parse_header

try:  # Official aoc-mgz model API. May not exist in mgz-fast-only installs.
    from mgz.model import parse_match as mgz_parse_match
    from mgz.model import serialize as mgz_serialize_model
except Exception:  # pragma: no cover - optional dependency surface
    mgz_parse_match = None
    mgz_serialize_model = None

try:  # Official aoc-mgz summary API. May not exist in mgz-fast-only installs.
    from mgz.summary import Summary as MgzSummary
except Exception:  # pragma: no cover - optional dependency surface
    MgzSummary = None

try:  # Official aoc-mgz/aocref reference data.
    from mgz.reference import get_consts as mgz_get_consts
    from mgz.reference import get_dataset as mgz_get_dataset
except Exception:  # pragma: no cover - optional dependency surface
    mgz_get_consts = None
    mgz_get_dataset = None

PARSER_VERSION = "mgz-fast 1.0.0 + exhaustive extraction"
UPLOAD_REPORT_SCHEMA_VERSION = "replay-report-v2"

LOGGER = logging.getLogger("parse_replay")

ENV_AOE2_INSTALL_PATH = os.environ.get("AOE2_INSTALL_PATH")
DEFAULT_AOE2_PATHS = tuple(
    path
    for path in (
        Path(ENV_AOE2_INSTALL_PATH) if ENV_AOE2_INSTALL_PATH else None,
        Path(r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE"),
        Path(r"C:\Program Files\Steam\steamapps\common\AoE2DE"),
    )
    if path is not None
)

GAME_TYPE_NAMES = {
    0: "Random Map",
    1: "Regicide",
    2: "Death Match",
    3: "Scenario",
    4: "Campaign",
    5: "King of the Hill",
    6: "Wonder Race",
    7: "Defend the Wonder",
    8: "Turbo Random Map",
}

STARTING_AGE_NAMES = {
    0: "Standard",
    1: "Dark Age",
    2: "Feudal Age",
    3: "Castle Age",
    4: "Imperial Age",
    5: "Post-Imperial Age",
}

KEY_BUILDING_NAMES = {
    "Archery Range",
    "Barracks",
    "Blacksmith",
    "Castle",
    "Dock",
    "Donjon",
    "Fortified Church",
    "Government Building",
    "Krepost",
    "Lumber Camp",
    "Market",
    "Mill",
    "Mining Camp",
    "Monastery",
    "Siege Workshop",
    "Stable",
    "Town Center",
    "University",
}

MILITARY_BUILDING_NAMES = {
    "Archery Range",
    "Barracks",
    "Castle",
    "Dock",
    "Donjon",
    "Fortified Church",
    "Government Building",
    "Krepost",
    "Monastery",
    "Siege Workshop",
    "Stable",
}

ECONOMY_TECH_NAMES = {
    "Bow Saw",
    "Crop Rotation",
    "Double-Bit Axe",
    "Gold Mining",
    "Gold Shaft Mining",
    "Hand Cart",
    "Heavy Plow",
    "Horse Collar",
    "Loom",
    "Stone Mining",
    "Stone Shaft Mining",
    "Two-Man Saw",
    "Wheelbarrow",
}

AGE_TECH_NAMES = {
    "Feudal Age",
    "Castle Age",
    "Imperial Age",
}

INTERESTING_TECH_NAMES = ECONOMY_TECH_NAMES | AGE_TECH_NAMES

AGE_MESSAGES = {
    "Feudal": "Feudal Age",
    "Castle": "Castle Age",
    "Imperial": "Imperial Age",
}

CIVILIAN_UNIT_PATTERNS = (
    "Villager",
    "Trade",
    "Fishing",
    "Fishing Ship",
    "Trade Cart",
    "Trade Cog",
    "Transport",
    "Monk",
    "Missionary",
    "King",
    "Sheep",
    "Goat",
    "Cow",
    "Llama",
    "Turkey",
    "Pig",
    "Boar",
    "Deer",
)

SYSTEM_AGE_UP_RE = re.compile(
    r"<player_id,(\d+),[^>]*>\s+advanced to the (Feudal|Castle|Imperial) Age\."
)
FILENAME_VERSION_RE = re.compile(r"v(?P<version>\d+(?:\.\d+)+)", re.IGNORECASE)
FILENAME_DATE_RE = re.compile(
    r"(?P<year>\d{4})[._-](?P<month>\d{2})[._-](?P<day>\d{2})[_ -]?(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?"
)

# Minimal safety net when external reference data cannot name common techs.
KNOWN_TECH_NAMES_BY_ID = {
    12: "Crop Rotation",
    13: "Heavy Plow",
    14: "Horse Collar",
    22: "Loom",
    55: "Gold Mining",
    101: "Feudal Age",
    102: "Castle Age",
    103: "Imperial Age",
    202: "Double-Bit Axe",
    203: "Bow Saw",
    213: "Wheelbarrow",
    249: "Hand Cart",
    278: "Stone Mining",
    279: "Stone Shaft Mining",
}

# Minimal safety net for common building object IDs.
KNOWN_BUILDING_NAMES_BY_ID = {
    12: "Barracks",
    45: "Dock",
    49: "Siege Workshop",
    68: "Mill",
    70: "House",
    71: "Town Center",
    82: "Castle",
    84: "Market",
    87: "Archery Range",
    101: "Stable",
    103: "Blacksmith",
    104: "Monastery",
    109: "Town Center",
    141: "Town Center",
    142: "Town Center",
    209: "University",
    562: "Lumber Camp",
    584: "Mining Camp",
}

JsonDict = dict[str, Any]


def configure_logging(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )


def decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def safe_enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def to_jsonable(
    value: Any, *, max_depth: int = 25, _depth: int = 0, _seen: set[int] | None = None
) -> Any:
    """Convert arbitrary parser objects into JSON-safe values.

    This avoids circular references and keeps huge byte blobs from exploding the
    JSON report. For binary data, it records length and a hex prefix.
    """
    if _seen is None:
        _seen = set()

    if _depth > max_depth:
        return {"__truncated": "max_depth", "type": type(value).__name__}

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return {
            "__type": "bytes",
            "length": len(value),
            "utf8Preview": value[:256].decode("utf-8", errors="replace"),
            "hexPreview": value[:256].hex(),
        }

    if isinstance(value, bytearray):
        return to_jsonable(
            bytes(value), max_depth=max_depth, _depth=_depth, _seen=_seen
        )

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, Enum):
        return value.name

    object_id = id(value)
    if object_id in _seen:
        return {"__cycle": type(value).__name__}

    if dataclasses.is_dataclass(value):
        _seen.add(object_id)
        return {
            field.name: to_jsonable(
                getattr(value, field.name),
                max_depth=max_depth,
                _depth=_depth + 1,
                _seen=_seen,
            )
            for field in dataclasses.fields(value)
        }

    if isinstance(value, dict):
        _seen.add(object_id)
        output: JsonDict = {}
        for key, item in value.items():
            if isinstance(key, (str, int, float, bool)) or key is None:
                json_key = str(key)
            else:
                json_key = safe_enum_name(key)
            output[json_key] = to_jsonable(
                item, max_depth=max_depth, _depth=_depth + 1, _seen=_seen
            )
        return output

    if isinstance(value, (list, tuple, set, frozenset)):
        _seen.add(object_id)
        return [
            to_jsonable(item, max_depth=max_depth, _depth=_depth + 1, _seen=_seen)
            for item in value
        ]

    if hasattr(value, "_asdict"):
        try:
            return to_jsonable(
                value._asdict(), max_depth=max_depth, _depth=_depth + 1, _seen=_seen
            )
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        _seen.add(object_id)
        try:
            return to_jsonable(
                vars(value), max_depth=max_depth, _depth=_depth + 1, _seen=_seen
            )
        except Exception:
            pass

    return str(value)


def append_limited(items: list[Any], item: Any, max_items: int) -> bool:
    if max_items < 0 or len(items) < max_items:
        items.append(item)
        return True
    return False


def limited_items(items: list[Any], max_items: int) -> list[Any]:
    if max_items < 0:
        return items
    return items[:max_items]


def add_warning(
    warnings: list[JsonDict], code: str, message: str, **context: Any
) -> None:
    warnings.append({"code": code, "message": message, "context": context})
    LOGGER.warning("%s: %s %s", code, message, safe_json_dumps(context))


def add_parse_error(
    errors: list[JsonDict], code: str, message: str, **context: Any
) -> None:
    errors.append({"code": code, "message": message, "context": context})
    LOGGER.error("%s: %s %s", code, message, safe_json_dumps(context))


def speed_label(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        if 1.6 <= float(value) <= 1.8:
            return "standard"
        if float(value) < 1.2:
            return "slow"
        if float(value) >= 2:
            return "fast"
        return f"{float(value):.2f}x"

    return SPEEDS.get(value) or (decode_text(value) if value is not None else None)


def read_json_file(path: Path, warnings: list[JsonDict]) -> JsonDict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        add_warning(
            warnings,
            "game_data_json_invalid",
            "Game data JSON file could not be parsed.",
            path=str(path),
            error=str(error),
        )
    except OSError as error:
        add_warning(
            warnings,
            "game_data_read_failed",
            "Game data file could not be read.",
            path=str(path),
            error=str(error),
        )
    return None


def resolve_aoe2_path(
    explicit_path: str | None, warnings: list[JsonDict]
) -> Path | None:
    candidate_paths: list[Path] = []
    if explicit_path:
        candidate_paths.append(Path(explicit_path))
    candidate_paths.extend(DEFAULT_AOE2_PATHS)

    for path in candidate_paths:
        if path and path.exists():
            LOGGER.info("Using AoE2 install path: %s", path)
            return path

    add_warning(
        warnings,
        "aoe2_install_path_not_found",
        "AoE2 install path was not found. The report will rely on bundled/parser reference data and fallback IDs.",
        explicitPath=explicit_path,
        envPath=ENV_AOE2_INSTALL_PATH,
        searchedPaths=[str(path) for path in candidate_paths],
    )
    return None


def reference_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "Name", "internal_name", "display_name"):
            name = value.get(key)
            if isinstance(name, str) and name.strip():
                return name
    return None


def load_game_data(
    explicit_aoe2_path: str | None, warnings: list[JsonDict]
) -> dict[str, Any]:
    """Load names from the local AoE2 install when available.

    This is supplemented later by aocref/mgz.reference after the header gives us
    version/dataset information.
    """
    aoe2_path = resolve_aoe2_path(explicit_aoe2_path, warnings)

    game_data: dict[str, Any] = {
        "civilizations": {},
        "technologies": dict(KNOWN_TECH_NAMES_BY_ID),
        "buildings": dict(KNOWN_BUILDING_NAMES_BY_ID),
        "units": {},
        "objects": dict(KNOWN_BUILDING_NAMES_BY_ID),
        "reference": {},
    }

    if aoe2_path is None:
        return game_data

    civilizations_path = (
        aoe2_path / "resources" / "_common" / "dat" / "civilizations.json"
    )
    civilizations = read_json_file(civilizations_path, warnings)
    if civilizations:
        for index, civilization in enumerate(
            civilizations.get("civilization_list", [])
        ):
            if isinstance(civilization, dict):
                game_data["civilizations"][index] = civilization.get(
                    "internal_name", f"Civ {index}"
                )

    civ_tech_tree_dir = aoe2_path / "resources" / "_common" / "dat" / "CivTechTrees"
    if civ_tech_tree_dir.exists():
        for tech_tree_path in civ_tech_tree_dir.glob("*.json"):
            tech_tree = read_json_file(tech_tree_path, warnings)
            if not tech_tree:
                continue
            for node in tech_tree.get("civ_techs_buildings", []):
                if not isinstance(node, dict):
                    continue
                try:
                    node_id = int(node.get("Node ID"))
                except (TypeError, ValueError):
                    continue
                name = node.get("Name")
                if not isinstance(name, str) or not name.strip():
                    continue
                node_type = str(node.get("Node Type", ""))
                use_type = str(node.get("Use Type", ""))
                if "Building" in node_type or use_type == "Building":
                    game_data["buildings"].setdefault(node_id, name)
                    game_data["objects"].setdefault(node_id, name)
                elif "Research" in node_type:
                    game_data["technologies"].setdefault(node_id, name)
                elif "Unit" in node_type or use_type == "Unit":
                    game_data["units"].setdefault(node_id, name)
                    game_data["objects"].setdefault(node_id, name)
    else:
        add_warning(
            warnings,
            "civ_tech_tree_dir_missing",
            "CivTechTrees directory was not found. Building, unit, and technology names may be incomplete.",
            path=str(civ_tech_tree_dir),
        )

    unit_lines_path = aoe2_path / "resources" / "_common" / "dat" / "unitlines.json"
    unit_lines = read_json_file(unit_lines_path, warnings)
    if unit_lines:
        for line in unit_lines.get("UnitLines", []):
            if not isinstance(line, dict):
                continue
            line_name = line.get("Name")
            if not isinstance(line_name, str) or not line_name.strip():
                continue
            for unit_id in line.get("IDChain", []):
                if isinstance(unit_id, int):
                    game_data["units"].setdefault(
                        unit_id, line_name.replace(" Line", "")
                    )
                    game_data["objects"].setdefault(
                        unit_id, line_name.replace(" Line", "")
                    )

    LOGGER.info(
        "Loaded local game data counts: civs=%s techs=%s buildings=%s units=%s objects=%s",
        len(game_data["civilizations"]),
        len(game_data["technologies"]),
        len(game_data["buildings"]),
        len(game_data["units"]),
        len(game_data["objects"]),
    )
    return game_data


def augment_game_data_from_mgz_reference(
    header: JsonDict, game_data: dict[str, Any], warnings: list[JsonDict]
) -> None:
    if mgz_get_dataset is None or mgz_get_consts is None:
        add_warning(
            warnings,
            "mgz_reference_unavailable",
            "mgz.reference is not available, so object/tech names are limited to local game data and fallback IDs.",
        )
        return

    try:
        version = header.get("version")
        mod = header.get("mod")
        dataset_id, dataset = mgz_get_dataset(version, mod)
        consts = mgz_get_consts()
    except Exception as error:
        add_warning(
            warnings,
            "mgz_reference_load_failed",
            "mgz.reference data could not be loaded for this replay.",
            error=str(error),
            type=type(error).__name__,
        )
        return

    for key, value in (dataset.get("civilizations") or {}).items():
        name = reference_name(value)
        if name:
            try:
                game_data["civilizations"].setdefault(int(key), name)
            except (TypeError, ValueError):
                pass

    for key, value in (dataset.get("technologies") or {}).items():
        name = reference_name(value)
        if name:
            try:
                game_data["technologies"].setdefault(int(key), name)
            except (TypeError, ValueError):
                pass

    for key, value in (dataset.get("objects") or {}).items():
        name = reference_name(value)
        if name:
            try:
                object_id = int(key)
            except (TypeError, ValueError):
                continue
            game_data["objects"].setdefault(object_id, name)
            game_data["units"].setdefault(object_id, name)
            if object_id in KNOWN_BUILDING_NAMES_BY_ID:
                game_data["buildings"].setdefault(object_id, name)

    game_data["reference"] = {
        "datasetId": dataset_id,
        "datasetName": reference_name(dataset.get("dataset"))
        or (dataset.get("dataset") or {}).get("name"),
        "constsLoaded": bool(consts),
    }

    LOGGER.info(
        "After reference augment: civs=%s techs=%s buildings=%s units=%s objects=%s dataset=%s",
        len(game_data["civilizations"]),
        len(game_data["technologies"]),
        len(game_data["buildings"]),
        len(game_data["units"]),
        len(game_data["objects"]),
        dataset_id,
    )


def id_name(
    game_data: dict[str, Any], category: str, identifier: Any, fallback_prefix: str
) -> str:
    if isinstance(identifier, str) and identifier.isdigit():
        identifier = int(identifier)

    if not isinstance(identifier, int):
        return f"{fallback_prefix} {identifier}"

    if category == "technology":
        return (
            game_data["technologies"].get(identifier)
            or KNOWN_TECH_NAMES_BY_ID.get(identifier)
            or f"Tech {identifier}"
        )

    if category == "building":
        return (
            game_data["buildings"].get(identifier)
            or game_data["objects"].get(identifier)
            or KNOWN_BUILDING_NAMES_BY_ID.get(identifier)
            or f"Building {identifier}"
        )

    if category == "unit":
        return (
            game_data["units"].get(identifier)
            or game_data["objects"].get(identifier)
            or f"Unit {identifier}"
        )

    return str(identifier)


def map_name(header: JsonDict) -> tuple[str, int | None]:
    de = header.get("de") or {}
    scenario = header.get("scenario") or {}

    candidate_map_id = None
    if isinstance(de.get("rms_map_id"), int) and de.get("rms_map_id") in DE_MAP_NAMES:
        candidate_map_id = de.get("rms_map_id")
    elif isinstance(scenario.get("map_id"), int):
        candidate_map_id = scenario.get("map_id")

    if candidate_map_id is None:
        return ("Unknown map", None)

    label = DE_MAP_NAMES.get(candidate_map_id) or MAP_NAMES.get(candidate_map_id)
    if label:
        return (label, candidate_map_id)

    if candidate_map_id == 0:
        return ("Unknown map", candidate_map_id)

    return (f"Map {candidate_map_id}", candidate_map_id)


def game_type_label(header: JsonDict, players: list[JsonDict]) -> str:
    lobby = header.get("lobby") or {}
    game_type_id = lobby.get("game_type_id")
    base_label = GAME_TYPE_NAMES.get(game_type_id, "Recorded Match")

    team_buckets: dict[int, int] = defaultdict(int)
    for player in players:
        team = player.get("team")
        team_buckets[team if team is not None else player["slot"]] += 1

    team_sizes = sorted(team_buckets.values())
    if len(team_sizes) >= 2:
        match_shape = "v".join(str(size) for size in team_sizes)
        return f"{match_shape} {base_label}"

    return base_label


def format_seconds(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    minutes, remaining = divmod(int(seconds), 60)
    return f"{minutes}:{remaining:02d}"


def is_military_unit(unit_name: str) -> bool:
    if not unit_name or unit_name.startswith("Unit "):
        return False
    return not any(pattern in unit_name for pattern in CIVILIAN_UNIT_PATTERNS)


def add_timeline_event(
    events: list[JsonDict],
    time_seconds: int,
    player_slot: int | None,
    event_type: str,
    label: str,
    metadata: JsonDict | None = None,
) -> None:
    event: JsonDict = {
        "timeSeconds": int(time_seconds),
        "playerSlot": player_slot,
        "type": event_type,
        "label": label,
    }
    if metadata:
        event["metadata"] = metadata
    events.append(event)


def normalize_team_ids(players: list[JsonDict]) -> dict[int, int]:
    team_values = sorted(
        {player["team"] for player in players if player["team"] is not None}
    )
    return {team_id: index + 1 for index, team_id in enumerate(team_values)}


def infer_results(players: list[JsonDict]) -> tuple[int | None, set[int]]:
    team_members: dict[int, list[int]] = defaultdict(list)
    resigned_players: set[int] = set()

    for player in players:
        team_key = player["team"] if player["team"] is not None else player["slot"]
        team_members[team_key].append(player["slot"])
        if player.get("resignedAtSeconds") is not None:
            resigned_players.add(player["slot"])

    if not resigned_players:
        return (None, set())

    remaining_teams = {
        team_key
        for team_key, members in team_members.items()
        if any(member not in resigned_players for member in members)
    }

    if len(remaining_teams) == 1:
        winning_team = next(iter(remaining_teams))
        winning_slots = {
            member
            for member in team_members[winning_team]
            if member not in resigned_players
        }
        return (winning_team, winning_slots)

    if len(players) == 2 and len(resigned_players) == 1:
        winning_slots = {
            player["slot"]
            for player in players
            if player["slot"] not in resigned_players
        }
        winning_player = next(iter(winning_slots), None)
        if winning_player is None:
            return (None, set())
        winning_team = next(
            (
                player["team"] if player["team"] is not None else player["slot"]
                for player in players
                if player["slot"] == winning_player
            ),
            None,
        )
        return (winning_team, winning_slots)

    return (None, set())


def compare_timing(
    insights: list[JsonDict],
    label: str,
    field: str,
    threshold: int,
    first: JsonDict,
    second: JsonDict,
    category: str,
    noun: str | None = None,
) -> None:
    first_value = first.get(field)
    second_value = second.get(field)
    noun = noun or label
    if first_value is None or second_value is None:
        return
    delta = abs(first_value - second_value)
    if delta <= threshold:
        return
    faster, slower = (first, second) if first_value < second_value else (second, first)
    insights.append(
        {
            "playerSlot": slower["slot"],
            "category": category,
            "severity": "info",
            "text": f"{slower['name']} hit {label} {delta} seconds later than {faster['name']}.",
        }
    )
    insights.append(
        {
            "playerSlot": faster["slot"],
            "category": category,
            "severity": "good",
            "text": f"{faster['name']} was {delta} seconds earlier to {noun} than {slower['name']}.",
        }
    )


def build_insights(players: list[JsonDict]) -> list[JsonDict]:
    insights: list[JsonDict] = []
    comparable_players = [
        player for player in players if player["participantType"] == "human"
    ]

    for player in players:
        castle_time = player.get("castleTimeSeconds")
        feudal_time = player.get("feudalTimeSeconds")
        first_military_time = player.get("firstMilitaryBuildingTimeSeconds")
        loom_time = player.get("loomTimeSeconds")
        second_tc_time = player.get("firstTownCenterAfterCastleTimeSeconds")

        if feudal_time is not None and 4 * 60 <= feudal_time < 11 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "good",
                    "text": f"{player['name']} reached Feudal Age at {format_seconds(feudal_time)}, which is a quick Feudal timing.",
                }
            )

        if castle_time is not None and 8 * 60 <= castle_time < 20 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "good",
                    "text": f"{player['name']} reached Castle Age at {format_seconds(castle_time)}, which is a strong timing for many standard openings.",
                }
            )
        elif castle_time is not None and castle_time < 8 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "info",
                    "text": f"{player['name']} has a detected Castle Age timestamp at {format_seconds(castle_time)}. This may be a starting-age/restored-game artifact or a parser timing artifact rather than a normal age-up.",
                }
            )

        if castle_time is not None and castle_time >= 8 * 60 and second_tc_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "economy",
                    "severity": "warning",
                    "text": f"{player['name']} did not add a second Town Center within 3 minutes of reaching Castle Age.",
                }
            )

        if player["participantType"] == "human" and first_military_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "military",
                    "severity": "info",
                    "text": f"{player['name']} had no detected military building placement in the parsed command stream.",
                }
            )

        if player["participantType"] == "human" and loom_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "economy",
                    "severity": "info",
                    "text": f"{player['name']} had no detected Loom research timing.",
                }
            )

    if len(comparable_players) == 2:
        first, second = comparable_players
        compare_timing(
            insights, "Feudal Age", "feudalTimeSeconds", 30, first, second, "timing"
        )
        compare_timing(
            insights, "Castle Age", "castleTimeSeconds", 60, first, second, "timing"
        )
        compare_timing(
            insights,
            "first military building",
            "firstMilitaryBuildingTimeSeconds",
            45,
            first,
            second,
            "military",
            noun="their first military building",
        )
        compare_timing(
            insights, "Loom", "loomTimeSeconds", 30, first, second, "economy"
        )

    return insights


def parse_participants(
    header: JsonDict, game_data: dict[str, Any], warnings: list[JsonDict]
) -> list[JsonDict]:
    de_players = (header.get("de") or {}).get("players") or []
    participants: list[JsonDict] = []

    if not de_players:
        add_warning(
            warnings,
            "no_de_players_found",
            "No DE players were found in the replay header.",
        )

    for raw_player in de_players:
        if not isinstance(raw_player, dict):
            continue

        name = decode_text(raw_player.get("name")).strip()
        ai_name = decode_text(raw_player.get("ai_name")).strip()
        participant_name = name or ai_name
        slot = raw_player.get("number")

        if not participant_name or not isinstance(slot, int) or slot <= 0:
            add_warning(
                warnings,
                "skipped_invalid_player",
                "A player entry was skipped because it had no name or valid slot.",
                rawPlayer=to_jsonable(raw_player),
            )
            continue

        participant_type = "other"
        if raw_player.get("type") == 2:
            participant_type = "human"
        elif raw_player.get("type") == 4:
            participant_type = "ai"

        civilization_id = raw_player.get("civilization_id")

        participants.append(
            {
                "slot": slot,
                "name": participant_name,
                "civilization": game_data["civilizations"].get(
                    civilization_id, f"Civ {civilization_id}"
                ),
                "civilizationId": civilization_id,
                "team": raw_player.get("team_id"),
                "participantType": participant_type,
                "result": "unknown",
                "feudalTimeSeconds": None,
                "castleTimeSeconds": None,
                "imperialTimeSeconds": None,
                "loomTimeSeconds": None,
                "firstMilitaryBuildingTimeSeconds": None,
                "firstMilitaryUnitTimeSeconds": None,
                "firstMarketTimeSeconds": None,
                "firstBlacksmithTimeSeconds": None,
                "firstTownCenterAfterCastleTimeSeconds": None,
                "firstCastleTimeSeconds": None,
                "resignedAtSeconds": None,
                "commandSummary": {
                    "totalActions": 0,
                    "buildActions": 0,
                    "researchActions": 0,
                    "makeActions": 0,
                    "moveActions": 0,
                    "otherActions": 0,
                    "actionTypes": {},
                },
                "detectedTimings": {
                    "technologies": {},
                    "buildings": {},
                    "units": {},
                },
            }
        )

    participants.sort(key=lambda player: player["slot"])
    LOGGER.info("Parsed participants: %s", len(participants))
    return participants


def get_player_name(players_by_slot: dict[int, JsonDict], player_slot: int) -> str:
    return players_by_slot.get(player_slot, {"name": f"Player {player_slot}"})["name"]


def update_action_summary(player: JsonDict, action_type: Any) -> None:
    action_name = safe_enum_name(action_type)
    summary = player["commandSummary"]
    summary["totalActions"] += 1
    summary["actionTypes"][action_name] = summary["actionTypes"].get(action_name, 0) + 1

    if action_name == "BUILD":
        summary["buildActions"] += 1
    elif action_name == "RESEARCH":
        summary["researchActions"] += 1
    elif action_name == "MAKE":
        summary["makeActions"] += 1
    elif action_name in {
        "MOVE",
        "ORDER",
        "WAYPOINT",
        "MULTI_GATHERPOINT",
        "GATHER_POINT",
        "PATROL",
    }:
        summary["moveActions"] += 1
    else:
        summary["otherActions"] += 1


def record_named_timing(
    bucket: dict[str, JsonDict],
    name: str,
    time_seconds: int,
    identifier: Any | None = None,
    payload: Any | None = None,
) -> None:
    entry = bucket.setdefault(
        name,
        {
            "count": 0,
            "firstTimeSeconds": time_seconds,
            "lastTimeSeconds": time_seconds,
            "ids": [],
        },
    )
    entry["count"] += 1
    entry["firstTimeSeconds"] = min(entry["firstTimeSeconds"], time_seconds)
    entry["lastTimeSeconds"] = max(entry["lastTimeSeconds"], time_seconds)
    if identifier is not None and identifier not in entry["ids"]:
        entry["ids"].append(identifier)
    if payload is not None and "firstPayload" not in entry:
        entry["firstPayload"] = to_jsonable(payload)


def action_event_summary(
    action_name: str,
    action_data: JsonDict,
    game_data: dict[str, Any],
    player_name: str,
) -> tuple[str, str, JsonDict]:
    """Build a readable event row for every ACTION operation.

    This is intentionally broad. The curated timeline still adds richer key
    events such as `building`, `technology`, `unit`, and `resign`, but when
    --event-detail=all is enabled this helper makes sure the report.events
    array also has a row for every low-level action we can decode.
    """
    metadata: JsonDict = {
        "source": "action",
        "actionType": action_name,
    }

    for field in (
        "command_id",
        "order_id",
        "resource_id",
        "formation_id",
        "stance_id",
        "x",
        "y",
        "target_id",
        "object_id",
        "unit_id",
        "building_id",
        "technology_id",
        "amount",
    ):
        if field in action_data:
            metadata[field] = action_data.get(field)

    if action_name == "RESEARCH":
        technology_id = action_data.get("technology_id")
        technology_name = id_name(game_data, "technology", technology_id, "Tech")
        metadata["technologyId"] = technology_id
        metadata["technologyName"] = technology_name
        return (
            "action_research",
            f"{player_name} started research: {technology_name}.",
            metadata,
        )

    if action_name == "BUILD":
        building_id = action_data.get("building_id")
        building_name = id_name(game_data, "building", building_id, "Building")
        metadata["buildingId"] = building_id
        metadata["buildingName"] = building_name
        return (
            "action_build",
            f"{player_name} issued build command: {building_name}.",
            metadata,
        )

    if action_name == "MAKE":
        unit_id = action_data.get("unit_id")
        unit_name = id_name(game_data, "unit", unit_id, "Unit")
        metadata["unitId"] = unit_id
        metadata["unitName"] = unit_name
        return (
            "action_make",
            f"{player_name} queued/made unit: {unit_name}.",
            metadata,
        )

    if action_name == "RESIGN":
        return ("action_resign", f"{player_name} resigned.", metadata)

    if action_name == "TRIBUTE":
        return ("action_tribute", f"{player_name} sent tribute.", metadata)

    if action_name in {
        "MOVE",
        "ORDER",
        "WAYPOINT",
        "MULTI_GATHERPOINT",
        "GATHER_POINT",
        "PATROL",
    }:
        return (
            f"action_{action_name.lower()}",
            f"{player_name} issued {action_name.replace('_', ' ').lower()} command.",
            metadata,
        )

    return (
        f"action_{action_name.lower()}",
        f"{player_name} issued {action_name.replace('_', ' ').lower()} action.",
        metadata,
    )


def finalize_player_timings(
    participants: list[JsonDict],
    age_ups: dict[int, dict[str, int]],
    age_research_starts: dict[int, dict[str, int]],
    tc_build_times: dict[int, list[int]],
) -> None:
    for player in participants:
        slot = player["slot"]
        player["feudalTimeSeconds"] = age_ups.get(slot, {}).get(
            "Feudal Age"
        ) or age_research_starts.get(slot, {}).get("Feudal Age")
        player["castleTimeSeconds"] = age_ups.get(slot, {}).get(
            "Castle Age"
        ) or age_research_starts.get(slot, {}).get("Castle Age")
        player["imperialTimeSeconds"] = age_ups.get(slot, {}).get(
            "Imperial Age"
        ) or age_research_starts.get(slot, {}).get("Imperial Age")

        castle_time = player["castleTimeSeconds"]
        if castle_time is not None:
            tc_after_castle = [
                build_time
                for build_time in tc_build_times.get(slot, [])
                if castle_time < build_time <= castle_time + 180
            ]
            if tc_after_castle:
                player["firstTownCenterAfterCastleTimeSeconds"] = min(tc_after_castle)


def normalize_player_teams(participants: list[JsonDict]) -> None:
    team_map = normalize_team_ids(participants)
    for player in participants:
        if player["team"] is not None:
            player["team"] = team_map.get(player["team"], player["team"])


def apply_results(participants: list[JsonDict]) -> tuple[int | None, set[int]]:
    winning_team, winning_slots = infer_results(participants)
    for player in participants:
        if player["slot"] in winning_slots:
            player["result"] = "win"
        elif player.get("resignedAtSeconds") is not None:
            player["result"] = "loss"
    return winning_team, winning_slots


def apply_estimated_results(
    participants: list[JsonDict],
    winning_team: int,
    winning_slots: set[int],
) -> None:
    for player in participants:
        if player["slot"] in winning_slots:
            player["result"] = "win"
        elif player.get("team") is not None:
            player["result"] = "loss"


def estimate_results_from_final_sync(
    participants: list[JsonDict],
    player_timeseries: dict[int, list[JsonDict]],
) -> JsonDict | None:
    team_metrics: dict[int, JsonDict] = {}

    for player in participants:
        slot = player["slot"]
        team = player.get("team")
        if team is None:
            continue

        rows = player_timeseries.get(slot) or []
        if not rows:
            continue

        latest = rows[-1]
        total_resources = latest.get("totalResources")
        object_count = latest.get("objectCount")
        if not isinstance(total_resources, (int, float)) and not isinstance(
            object_count, (int, float)
        ):
            continue

        metrics = team_metrics.setdefault(
            team,
            {
                "team": team,
                "playerSlots": [],
                "totalResources": 0,
                "objectCount": 0,
                "score": 0,
            },
        )
        metrics["playerSlots"].append(slot)
        metrics["totalResources"] += int(total_resources or 0)
        metrics["objectCount"] += int(object_count or 0)

    for metrics in team_metrics.values():
        # This is not the official AoE score. It is only a fallback signal when
        # the replay has no explicit resign/victory result in the parsed stream.
        metrics["score"] = metrics["totalResources"] + metrics["objectCount"] * 25

    ranked_teams = sorted(
        team_metrics.values(),
        key=lambda metrics: metrics["score"],
        reverse=True,
    )

    if len(ranked_teams) < 2:
        return None

    leader = ranked_teams[0]
    runner_up = ranked_teams[1]
    if leader["score"] <= 0 or runner_up["score"] <= 0:
        return None

    score_ratio = leader["score"] / runner_up["score"]
    if score_ratio < 1.15:
        return None

    confidence = "medium" if score_ratio >= 1.35 else "low"
    winning_team = int(leader["team"])
    winning_slots = {
        player["slot"] for player in participants if player.get("team") == winning_team
    }

    return {
        "winningTeam": winning_team,
        "winningPlayerSlots": sorted(winning_slots),
        "source": "final_sync_stats_estimate",
        "confidence": confidence,
        "explanation": (
            "No explicit victory/resign result was parsed. Winner was estimated "
            "from the final sync-stat resource and object-count snapshot."
        ),
        "scoreRatio": round(score_ratio, 3),
        "teamMetrics": ranked_teams,
    }


def parse_chat_payload(payload: Any) -> JsonDict:
    decoded_chat = decode_text(payload)
    try:
        parsed_chat = json.loads(decoded_chat)
        if isinstance(parsed_chat, dict):
            return parsed_chat
    except json.JSONDecodeError:
        pass
    return {"message": decoded_chat}


def dig(data: Any, *keys: str, default: Any = None) -> Any:
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
        if value is None:
            return default
    return value


def build_header_summary(header: JsonDict) -> JsonDict:
    metadata = header.get("metadata") or {}
    lobby = header.get("lobby") or {}
    scenario = header.get("scenario") or {}
    de = header.get("de") or {}
    map_data = header.get("map") or {}

    starting_age_id = de.get("starting_age_id")
    return {
        "gameVersion": decode_text(header.get("game_version")) or None,
        "saveVersion": header.get("save_version"),
        "logVersion": header.get("log_version"),
        "versionKind": (
            safe_enum_name(header.get("version"))
            if header.get("version") is not None
            else None
        ),
        "mapSeed": lobby.get("seed"),
        "revealMapId": lobby.get("reveal_map_id"),
        "population": lobby.get("population"),
        "speed": speed_label(metadata.get("speed")),
        "rawSpeed": metadata.get("speed"),
        "ownerId": metadata.get("owner_id"),
        "scenarioMapId": scenario.get("map_id"),
        "scenarioFilename": decode_text(scenario.get("scenario_filename")) or None,
        "lobbyName": decode_text(de.get("lobby"))
        or decode_text(de.get("lobby_name"))
        or None,
        "modName": decode_text(de.get("mod")) or None,
        "rmsMapId": de.get("rms_map_id"),
        "rmsModId": de.get("rms_mod_id"),
        "difficultyId": de.get("difficulty_id") or scenario.get("difficulty_id"),
        "startingAgeId": starting_age_id,
        "startingAge": STARTING_AGE_NAMES.get(starting_age_id),
        "allTechnologies": de.get("all_technologies"),
        "teamTogether": de.get("team_together"),
        "lockSpeed": de.get("lock_speed"),
        "mapDimension": map_data.get("dimension"),
        "restoreTime": map_data.get("restore_time"),
        "timestamp": de.get("timestamp"),
    }


def header_structure(value: Any, *, depth: int = 0, max_depth: int = 4) -> Any:
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): header_structure(item, depth=depth + 1, max_depth=max_depth)
            for key, item in value.items()
        }
    if isinstance(value, list):
        if not value:
            return []
        return [
            header_structure(value[0], depth=depth + 1, max_depth=max_depth),
            f"... {len(value)} item(s)",
        ]
    return type(value).__name__


def update_counter(target: dict[str, int], key: Any) -> None:
    label = safe_enum_name(key)
    target[label] = target.get(label, 0) + 1


def try_build_model_bundle(
    replay_path: Path, include_model: bool, warnings: list[JsonDict]
) -> JsonDict:
    if not include_model:
        return {"available": False, "skipped": True, "reason": "disabled_by_cli"}
    if mgz_parse_match is None or mgz_serialize_model is None:
        return {"available": False, "skipped": True, "reason": "mgz.model_unavailable"}

    started = perf_counter()
    try:
        with replay_path.open("rb") as replay_file:
            match = mgz_parse_match(replay_file)
        serialized = mgz_serialize_model(match)
        return {
            "available": True,
            "parseMs": round((perf_counter() - started) * 1000, 2),
            "data": to_jsonable(serialized, max_depth=30),
        }
    except Exception as error:
        add_warning(
            warnings,
            "mgz_model_parse_failed",
            "mgz.model.parse_match failed. Fast extraction may still provide data.",
            error=str(error),
            type=type(error).__name__,
        )
        return {
            "available": False,
            "error": str(error),
            "type": type(error).__name__,
            "parseMs": round((perf_counter() - started) * 1000, 2),
        }


def call_summary_method(summary: Any, method_name: str) -> Any:
    method = getattr(summary, method_name)
    return method()


def try_build_summary_bundle(
    replay_path: Path, include_summary: bool, warnings: list[JsonDict]
) -> JsonDict:
    if not include_summary:
        return {"available": False, "skipped": True, "reason": "disabled_by_cli"}
    if MgzSummary is None:
        return {
            "available": False,
            "skipped": True,
            "reason": "mgz.summary_unavailable",
        }

    started = perf_counter()
    try:
        with replay_path.open("rb") as replay_file:
            summary = MgzSummary(replay_file)

        methods = [
            "get_version",
            "get_duration",
            "get_restored",
            "get_completed",
            "get_played",
            "get_owner",
            "get_encoding",
            "get_language",
            "get_platform",
            "get_settings",
            "get_dataset",
            "get_diplomacy",
            "get_teams",
            "get_players",
            "get_objects",
            "get_map",
            "get_chat",
            "get_postgame",
            "get_hash",
            "get_file_hash",
            "get_mirror",
        ]

        data: JsonDict = {}
        errors: JsonDict = {}
        for method_name in methods:
            if not hasattr(summary, method_name):
                continue
            try:
                data[
                    method_name[4:] if method_name.startswith("get_") else method_name
                ] = to_jsonable(
                    call_summary_method(summary, method_name),
                    max_depth=25,
                )
            except Exception as error:
                errors[method_name] = {
                    "error": str(error),
                    "type": type(error).__name__,
                }

        return {
            "available": True,
            "parseMs": round((perf_counter() - started) * 1000, 2),
            "data": data,
            "methodErrors": errors,
        }
    except Exception as error:
        add_warning(
            warnings,
            "mgz_summary_parse_failed",
            "mgz.summary.Summary failed. Fast extraction may still provide data.",
            error=str(error),
            type=type(error).__name__,
        )
        return {
            "available": False,
            "error": str(error),
            "type": type(error).__name__,
            "parseMs": round((perf_counter() - started) * 1000, 2),
        }


def process_sync_stats(
    payload: Any,
    current_second: int,
    operation_index: int,
    sync_stat_rows: list[JsonDict],
    player_timeseries: dict[int, list[JsonDict]],
    max_sync_stats: int,
    truncation: JsonDict,
) -> None:
    if not isinstance(payload, (tuple, list)) or len(payload) < 3:
        return

    stat_row = payload[2]
    if not stat_row:
        return

    row: JsonDict = {
        "operationIndex": operation_index,
        "timeSeconds": current_second,
        "raw": to_jsonable(stat_row),
    }

    if isinstance(stat_row, dict):
        if "current_time" in stat_row:
            row["currentTimeMilliseconds"] = stat_row.get("current_time")
        players: JsonDict = {}
        for key, stats in stat_row.items():
            if not isinstance(key, int) or not isinstance(stats, dict):
                continue
            player_row = {
                "timeSeconds": current_second,
                "totalResources": stats.get("total_res"),
                "objectCount": stats.get("obj_count"),
                "raw": to_jsonable(stats),
            }
            players[str(key)] = player_row
            if append_limited(player_timeseries[key], player_row, max_sync_stats):
                pass
        row["players"] = players

    if not append_limited(sync_stat_rows, row, max_sync_stats):
        truncation["syncStats"] = truncation.get("syncStats", 0) + 1


def parse_replay(args: argparse.Namespace) -> JsonDict:
    parse_started_at = perf_counter()

    replay_path = Path(args.replay_path)
    warnings: list[JsonDict] = []
    parse_errors: list[JsonDict] = []
    truncation: JsonDict = {}

    if not replay_path.exists():
        raise FileNotFoundError(f"Replay file not found: {replay_path}")
    if not replay_path.is_file():
        raise ValueError(f"Replay path is not a file: {replay_path}")
    if replay_path.suffix.lower() != ".aoe2record":
        add_warning(
            warnings,
            "unexpected_file_extension",
            "Replay file does not end with .aoe2record.",
            path=str(replay_path),
            suffix=replay_path.suffix,
        )

    file_size = replay_path.stat().st_size
    file_sha1 = hashlib.sha1(replay_path.read_bytes()).hexdigest()

    LOGGER.info(
        "Starting replay parse: path=%s size=%s replayId=%s",
        replay_path,
        file_size,
        args.replay_id or replay_path.stem,
    )

    game_data = load_game_data(args.aoe2_path, warnings)

    header_parse_started_at = perf_counter()
    with replay_path.open("rb") as replay_file:
        header = parse_header(replay_file)
    header_parse_ms = round((perf_counter() - header_parse_started_at) * 1000, 2)
    LOGGER.info("Header parsed in %sms", header_parse_ms)

    augment_game_data_from_mgz_reference(header, game_data, warnings)

    participants = parse_participants(header, game_data, warnings)
    players_by_slot = {player["slot"]: player for player in participants}

    operation_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    action_counts_by_player: dict[int, Counter[str]] = defaultdict(Counter)
    operation_samples_by_type: dict[str, list[JsonDict]] = defaultdict(list)

    chats: list[JsonDict] = []
    events: list[JsonDict] = []
    raw_actions: list[JsonDict] = []
    viewlocks: list[JsonDict] = []
    sync_stat_rows: list[JsonDict] = []
    player_timeseries: dict[int, list[JsonDict]] = defaultdict(list)
    postgame_payloads: list[JsonDict] = []

    player_command_data: dict[int, JsonDict] = {
        player["slot"]: {
            "actionCounts": {},
            "researches": {},
            "buildings": {},
            "units": {},
            "tributes": [],
            "positions": [],
            "commandIdCounts": {},
            "orderIdCounts": {},
            "resourceIdCounts": {},
            "formationIdCounts": {},
            "stanceIdCounts": {},
        }
        for player in participants
    }

    age_ups: dict[int, dict[str, int]] = defaultdict(dict)
    age_research_starts: dict[int, dict[str, int]] = defaultdict(dict)
    tc_build_times: dict[int, list[int]] = defaultdict(list)

    duration_ms = 0
    operation_index = 0
    skipped_operations = 0
    last_good_offset = 0

    operations_parse_started_at = perf_counter()

    with replay_path.open("rb") as replay_file:
        parse_header(replay_file)

        try:
            meta(replay_file)
        except Exception as error:
            add_warning(
                warnings,
                "meta_parse_failed",
                "Replay metadata parsing failed, but operation parsing will continue.",
                error=str(error),
                offset=replay_file.tell(),
            )

        eof = os.fstat(replay_file.fileno()).st_size

        while replay_file.tell() < eof:
            current_offset = replay_file.tell()
            last_good_offset = current_offset

            try:
                op_type, payload = operation(replay_file)
                operation_index += 1
            except EOFError:
                LOGGER.info("Reached EOF while reading operations.")
                break
            except Exception as error:
                skipped_operations += 1
                add_parse_error(
                    parse_errors,
                    "operation_parse_failed",
                    "An operation could not be parsed. Parsing stopped at this point.",
                    operationIndex=operation_index,
                    offset=current_offset,
                    error=str(error),
                    errorType=type(error).__name__,
                )
                break

            operation_name = safe_enum_name(op_type)
            operation_counts[operation_name] += 1
            if (
                args.max_operation_samples < 0
                or len(operation_samples_by_type[operation_name])
                < args.max_operation_samples
            ):
                operation_samples_by_type[operation_name].append(
                    {
                        "operationIndex": operation_index,
                        "offset": current_offset,
                        "payload": to_jsonable(payload, max_depth=8),
                    }
                )

            if args.debug and operation_index % 1000 == 0:
                LOGGER.debug(
                    "Parsed %s operations, offset=%s, durationSeconds=%s",
                    operation_index,
                    replay_file.tell(),
                    int(duration_ms / 1000),
                )

            if op_type == Operation.SYNC:
                try:
                    duration_ms += int(payload[0])
                    current_second = int(duration_ms / 1000)
                    process_sync_stats(
                        payload,
                        current_second,
                        operation_index,
                        sync_stat_rows,
                        player_timeseries,
                        args.max_sync_stats,
                        truncation,
                    )
                except Exception as error:
                    add_warning(
                        warnings,
                        "sync_payload_invalid",
                        "SYNC payload did not contain a valid time delta or stats row.",
                        operationIndex=operation_index,
                        offset=current_offset,
                        payload=to_jsonable(payload, max_depth=8),
                        error=str(error),
                    )
                continue

            current_second = int(duration_ms / 1000)

            if op_type == Operation.VIEWLOCK:
                item = {
                    "operationIndex": operation_index,
                    "offset": current_offset,
                    "timeSeconds": current_second,
                    "payload": to_jsonable(payload, max_depth=8),
                }
                if not append_limited(viewlocks, item, args.max_viewlocks):
                    truncation["viewlocks"] = truncation.get("viewlocks", 0) + 1
                continue

            if op_type == Operation.POSTGAME:
                postgame_payloads.append(
                    {
                        "operationIndex": operation_index,
                        "offset": current_offset,
                        "timeSeconds": current_second,
                        "payload": to_jsonable(payload, max_depth=20),
                    }
                )
                continue

            if op_type == Operation.CHAT:
                parsed_chat = parse_chat_payload(payload)
                chat_player = parsed_chat.get("player")
                chat_message = str(parsed_chat.get("message", "")).strip()
                chat_item = {
                    "operationIndex": operation_index,
                    "offset": current_offset,
                    "timeSeconds": current_second,
                    "playerSlot": chat_player if isinstance(chat_player, int) else None,
                    "message": chat_message or decode_text(payload),
                    "raw": to_jsonable(parsed_chat, max_depth=10),
                }
                if not append_limited(chats, chat_item, args.max_chats):
                    truncation["chats"] = truncation.get("chats", 0) + 1

                if args.event_detail == "all":
                    add_timeline_event(
                        events,
                        current_second,
                        chat_player if isinstance(chat_player, int) else None,
                        "chat",
                        chat_message or decode_text(payload),
                        {
                            "source": "chat",
                            "operationIndex": operation_index,
                            "offset": current_offset,
                            "raw": to_jsonable(parsed_chat, max_depth=10),
                        },
                    )

                age_match = SYSTEM_AGE_UP_RE.search(chat_message)
                if age_match:
                    player_slot = int(age_match.group(1))
                    age_label = AGE_MESSAGES.get(age_match.group(2))
                    if age_label:
                        age_ups[player_slot][age_label] = current_second
                        add_timeline_event(
                            events,
                            current_second,
                            player_slot,
                            "age_up",
                            f"{get_player_name(players_by_slot, player_slot)} reached {age_label}.",
                            {
                                "source": "chat",
                                "age": age_label,
                                "operationIndex": operation_index,
                            },
                        )
                continue

            if op_type != Operation.ACTION:
                continue

            try:
                action_type, action_data = payload
            except Exception:
                add_warning(
                    warnings,
                    "action_payload_invalid",
                    "ACTION payload was not a two-item action tuple.",
                    operationIndex=operation_index,
                    offset=current_offset,
                    payload=to_jsonable(payload, max_depth=8),
                )
                continue

            action_name = safe_enum_name(action_type)
            action_counts[action_name] += 1

            if not isinstance(action_data, dict):
                add_warning(
                    warnings,
                    "action_data_invalid",
                    "ACTION data was not a dictionary.",
                    operationIndex=operation_index,
                    offset=current_offset,
                    actionType=action_name,
                    actionData=to_jsonable(action_data, max_depth=8),
                )
                continue

            player_slot = action_data.get("player_id")
            player = players_by_slot.get(player_slot)
            command_data = (
                player_command_data.get(player_slot)
                if isinstance(player_slot, int)
                else None
            )

            raw_action = {
                "operationIndex": operation_index,
                "offset": current_offset,
                "timeSeconds": current_second,
                "playerSlot": player_slot if isinstance(player_slot, int) else None,
                "actionType": action_name,
                "payload": to_jsonable(action_data, max_depth=12),
            }
            if not append_limited(raw_actions, raw_action, args.max_raw_actions):
                truncation["rawActions"] = truncation.get("rawActions", 0) + 1

            if args.event_detail == "all":
                event_player_name = (
                    player["name"]
                    if player is not None
                    else (
                        f"Player {player_slot}"
                        if isinstance(player_slot, int)
                        else "Unknown player"
                    )
                )
                event_type, event_label, event_metadata = action_event_summary(
                    action_name,
                    action_data,
                    game_data,
                    event_player_name,
                )
                event_metadata["operationIndex"] = operation_index
                event_metadata["offset"] = current_offset
                add_timeline_event(
                    events,
                    current_second,
                    player_slot if isinstance(player_slot, int) else None,
                    event_type,
                    event_label,
                    event_metadata,
                )

            if player is not None:
                action_counts_by_player[player_slot][action_name] += 1
                update_action_summary(player, action_type)

            if command_data is not None:
                update_counter(command_data["actionCounts"], action_name)
                for field, bucket_name in (
                    ("command_id", "commandIdCounts"),
                    ("order_id", "orderIdCounts"),
                    ("resource_id", "resourceIdCounts"),
                    ("formation_id", "formationIdCounts"),
                    ("stance_id", "stanceIdCounts"),
                ):
                    if field in action_data:
                        update_counter(
                            command_data[bucket_name], action_data.get(field)
                        )
                if "x" in action_data and "y" in action_data:
                    append_limited(
                        command_data["positions"],
                        {
                            "timeSeconds": current_second,
                            "actionType": action_name,
                            "x": action_data.get("x"),
                            "y": action_data.get("y"),
                        },
                        args.max_positions_per_player,
                    )

            if action_name == "RESIGN" and player is not None:
                player["resignedAtSeconds"] = current_second
                add_timeline_event(
                    events,
                    current_second,
                    player_slot,
                    "resign",
                    f"{player['name']} resigned.",
                    {"source": "action", "operationIndex": operation_index},
                )
                continue

            if action_name == "RESEARCH" and player is not None:
                technology_id = action_data.get("technology_id")
                tech_name = id_name(game_data, "technology", technology_id, "Tech")

                player["detectedTimings"]["technologies"].setdefault(
                    tech_name, current_second
                )
                if command_data is not None:
                    record_named_timing(
                        command_data["researches"],
                        tech_name,
                        current_second,
                        technology_id,
                        action_data,
                    )

                if tech_name == "Loom" and player["loomTimeSeconds"] is None:
                    player["loomTimeSeconds"] = current_second

                if tech_name in AGE_TECH_NAMES:
                    age_research_starts[player_slot][tech_name] = current_second

                if tech_name in INTERESTING_TECH_NAMES:
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "technology",
                        f"{player['name']} started {tech_name}.",
                        {
                            "source": "action",
                            "technologyId": technology_id,
                            "technologyName": tech_name,
                            "operationIndex": operation_index,
                        },
                    )
                continue

            if action_name == "BUILD" and player is not None:
                building_id = action_data.get("building_id")
                building_name = id_name(game_data, "building", building_id, "Building")

                player["detectedTimings"]["buildings"].setdefault(
                    building_name, current_second
                )
                if command_data is not None:
                    record_named_timing(
                        command_data["buildings"],
                        building_name,
                        current_second,
                        building_id,
                        action_data,
                    )

                if building_name in KEY_BUILDING_NAMES:
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "building",
                        f"{player['name']} placed {building_name}.",
                        {
                            "source": "action",
                            "buildingId": building_id,
                            "buildingName": building_name,
                            "operationIndex": operation_index,
                            "x": action_data.get("x"),
                            "y": action_data.get("y"),
                        },
                    )

                if (
                    building_name in MILITARY_BUILDING_NAMES
                    and player["firstMilitaryBuildingTimeSeconds"] is None
                ):
                    player["firstMilitaryBuildingTimeSeconds"] = current_second
                if (
                    building_name == "Market"
                    and player["firstMarketTimeSeconds"] is None
                ):
                    player["firstMarketTimeSeconds"] = current_second
                if (
                    building_name == "Blacksmith"
                    and player["firstBlacksmithTimeSeconds"] is None
                ):
                    player["firstBlacksmithTimeSeconds"] = current_second
                if (
                    building_name == "Castle"
                    and player["firstCastleTimeSeconds"] is None
                ):
                    player["firstCastleTimeSeconds"] = current_second
                if building_name == "Town Center":
                    tc_build_times[player_slot].append(current_second)
                continue

            if action_name == "MAKE" and player is not None:
                unit_id = action_data.get("unit_id")
                unit_name = id_name(game_data, "unit", unit_id, "Unit")

                player["detectedTimings"]["units"].setdefault(unit_name, current_second)
                if command_data is not None:
                    record_named_timing(
                        command_data["units"],
                        unit_name,
                        current_second,
                        unit_id,
                        action_data,
                    )

                if (
                    is_military_unit(unit_name)
                    and player["firstMilitaryUnitTimeSeconds"] is None
                ):
                    player["firstMilitaryUnitTimeSeconds"] = current_second
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "unit",
                        f"{player['name']} queued/completed {unit_name}.",
                        {
                            "source": "action",
                            "unitId": unit_id,
                            "unitName": unit_name,
                            "operationIndex": operation_index,
                        },
                    )
                continue

            if action_name == "TRIBUTE" and command_data is not None:
                append_limited(
                    command_data["tributes"],
                    {
                        "timeSeconds": current_second,
                        "payload": to_jsonable(action_data, max_depth=10),
                    },
                    args.max_tributes_per_player,
                )

    operations_parse_ms = round(
        (perf_counter() - operations_parse_started_at) * 1000, 2
    )

    finalize_player_timings(participants, age_ups, age_research_starts, tc_build_times)
    normalize_player_teams(participants)
    winning_team, winning_slots = apply_results(participants)
    result_inference: JsonDict = {
        "source": "explicit_resign" if winning_team is not None else "unknown",
        "confidence": "high" if winning_team is not None else None,
        "explanation": (
            "Winner inferred from parsed resign actions."
            if winning_team is not None
            else "No explicit victory or resign result was found in the parsed replay stream."
        ),
    }

    if winning_team is None:
        estimated_result = estimate_results_from_final_sync(
            participants,
            player_timeseries,
        )
        if estimated_result is not None:
            winning_team = estimated_result["winningTeam"]
            winning_slots = set(estimated_result["winningPlayerSlots"])
            apply_estimated_results(participants, winning_team, winning_slots)
            result_inference = estimated_result
            add_warning(
                warnings,
                "result_estimated_from_final_sync_stats",
                "The replay did not expose an explicit winner, so the result was estimated from final sync stats.",
                winningTeam=winning_team,
                confidence=estimated_result.get("confidence"),
                scoreRatio=estimated_result.get("scoreRatio"),
            )

    map_label, map_id = map_name(header)
    duration_seconds = int(duration_ms / 1000)
    header_summary = build_header_summary(header)

    insights = build_insights(participants)
    if result_inference.get("source") == "final_sync_stats_estimate":
        insights.insert(
            0,
            {
                "playerSlot": None,
                "category": "result",
                "severity": "warning",
                "text": (
                    f"Winner is estimated as Team {winning_team} from final sync stats "
                    "because the replay did not expose an explicit victory or resign result."
                ),
            },
        )

    match = {
        "id": args.replay_id or replay_path.stem,
        "map": map_label,
        "mapId": map_id,
        "gameType": game_type_label(header, participants),
        "durationSeconds": duration_seconds,
        "playedAt": None,
        "version": decode_text(header.get("game_version")),
        "saveVersion": header.get("save_version"),
        "parserVersion": PARSER_VERSION,
        "schemaVersion": UPLOAD_REPORT_SCHEMA_VERSION,
        "winningTeam": winning_team,
        "winningPlayerSlots": sorted(winning_slots),
        "resultSource": result_inference.get("source"),
        "resultConfidence": result_inference.get("confidence"),
        "resultExplanation": result_inference.get("explanation"),
    }

    sorted_events = sorted(
        events,
        key=lambda event: (
            event["timeSeconds"],
            event["playerSlot"] or 0,
            event["type"],
        ),
    )

    visible_events = limited_items(sorted_events, int(args.max_events))

    total_parse_ms = round((perf_counter() - parse_started_at) * 1000, 2)

    # Optional heavier parsers after the fast pass. Their errors are warnings;
    # they should not block the report.
    model_bundle = try_build_model_bundle(replay_path, not args.no_model, warnings)
    summary_bundle = try_build_summary_bundle(
        replay_path, not args.no_summary, warnings
    )

    report = {
        "ok": True,
        "partial": False,
        "match": match,
        "players": participants,
        "events": visible_events,
        "insights": insights,
        "rawInspection": {
            "parserVersion": PARSER_VERSION,
            "schemaVersion": UPLOAD_REPORT_SCHEMA_VERSION,
            "fileSizeBytes": file_size,
            "file": {
                "path": str(replay_path),
                "name": replay_path.name,
                "stem": replay_path.stem,
                "suffix": replay_path.suffix,
                "sizeBytes": file_size,
                "sha1": file_sha1,
            },
            "diagnostics": {
                "warnings": warnings,
                "parseErrors": parse_errors,
                "truncation": truncation,
                "timingsMs": {
                    "headerParse": header_parse_ms,
                    "operationsParse": operations_parse_ms,
                    "totalParseBeforeModelSummary": total_parse_ms,
                },
                "operationIndex": operation_index,
                "skippedOperations": skipped_operations,
                "lastGoodOffset": last_good_offset,
                "eofOffset": file_size,
                "operationCountsTotal": sum(operation_counts.values()),
                "actionCountsTotal": sum(action_counts.values()),
                "timelineEventsTotal": len(sorted_events),
                "timelineEventsCaptured": len(visible_events),
                "timelineEventsTruncated": len(visible_events) < len(sorted_events),
                "eventDetail": args.event_detail,
                "resultInference": result_inference,
                "gameDataCounts": {
                    "civilizations": len(game_data["civilizations"]),
                    "technologies": len(game_data["technologies"]),
                    "buildings": len(game_data["buildings"]),
                    "units": len(game_data["units"]),
                    "objects": len(game_data["objects"]),
                },
                "gameDataReference": game_data.get("reference"),
            },
            "operationCounts": dict(operation_counts.most_common()),
            "actionCounts": dict(action_counts.most_common()),
            "actionCountsByPlayer": {
                str(player_slot): dict(counter.most_common())
                for player_slot, counter in action_counts_by_player.items()
            },
            "playerCommandData": {
                str(slot): data for slot, data in player_command_data.items()
            },
            "chats": chats,
            "headerSummary": header_summary,
            "extracted": {
                "eventsAllCount": len(sorted_events),
                "rawActions": raw_actions,
                "rawActionsCountReturned": len(raw_actions),
                "rawActionsTruncatedCount": truncation.get("rawActions", 0),
                "viewlocks": viewlocks,
                "viewlocksCountReturned": len(viewlocks),
                "viewlocksTruncatedCount": truncation.get("viewlocks", 0),
                "syncStats": sync_stat_rows,
                "syncStatsCountReturned": len(sync_stat_rows),
                "syncStatsTruncatedCount": truncation.get("syncStats", 0),
                "playerTimeseries": {
                    str(slot): rows for slot, rows in player_timeseries.items()
                },
                "postgame": postgame_payloads,
                "operationSamplesByType": dict(operation_samples_by_type),
            },
            "headerStructure": header_structure(header),
            "header": (
                None
                if args.no_header
                else to_jsonable(header, max_depth=args.header_depth)
            ),
            "model": model_bundle,
            "summary": summary_bundle,
        },
    }

    LOGGER.info(
        "Replay parse complete: durationSeconds=%s players=%s events=%s rawActions=%s warnings=%s errors=%s totalMs=%s",
        duration_seconds,
        len(participants),
        len(sorted_events),
        len(raw_actions),
        len(warnings),
        len(parse_errors),
        round((perf_counter() - parse_started_at) * 1000, 2),
    )

    return report


def build_error_response(
    error: BaseException, args: argparse.Namespace | None
) -> JsonDict:
    replay_path = getattr(args, "replay_path", None)
    debug = bool(getattr(args, "debug", False))
    response: JsonDict = {
        "ok": False,
        "error": "Replay parse failed.",
        "details": {
            "message": str(error),
            "type": type(error).__name__,
            "replayPath": replay_path,
            "parserVersion": PARSER_VERSION,
            "schemaVersion": UPLOAD_REPORT_SCHEMA_VERSION,
        },
    }
    if debug:
        response["details"]["traceback"] = traceback.format_exc()
    return response


def infer_filename_metadata(replay_path: Path) -> JsonDict:
    name = replay_path.name
    version_match = FILENAME_VERSION_RE.search(name)
    date_match = FILENAME_DATE_RE.search(name)
    played_at = None
    if date_match:
        groups = date_match.groupdict(default="00")
        played_at = f"{groups['year']}-{groups['month']}-{groups['day']}T{groups['hour']}:{groups['minute']}:{groups['second']}"
    return {
        "version": version_match.group("version") if version_match else None,
        "playedAt": played_at,
    }


def build_partial_report(error: BaseException, args: argparse.Namespace) -> JsonDict:
    replay_path = Path(args.replay_path)
    file_size = replay_path.stat().st_size if replay_path.exists() else 0
    filename_metadata = infer_filename_metadata(replay_path)
    parser_message = str(error)
    replay_id = args.replay_id or replay_path.stem
    replay_version = filename_metadata.get("version") or "Unknown"

    file_sha1 = None
    file_preview = None
    if replay_path.exists() and replay_path.is_file():
        raw_bytes = replay_path.read_bytes()
        file_sha1 = hashlib.sha1(raw_bytes).hexdigest()
        file_preview = {
            "length": len(raw_bytes),
            "hexPreview": raw_bytes[:512].hex(),
        }

    fallback_warnings = [
        {
            "code": "partial_report",
            "message": "The replay was saved, but structured parsing failed.",
            "context": {"fallback": "filename_and_file_inspection"},
        }
    ]

    # Even when the fast header parser fails, try the official summary/model paths.
    # Summary may fall back to the full parser in official aoc-mgz installs.
    model_bundle = try_build_model_bundle(
        replay_path, not getattr(args, "no_model", False), fallback_warnings
    )
    summary_bundle = try_build_summary_bundle(
        replay_path, not getattr(args, "no_summary", False), fallback_warnings
    )

    return {
        "ok": True,
        "partial": True,
        "match": {
            "id": replay_id,
            "map": "Unknown map",
            "mapId": None,
            "gameType": "Recorded Match",
            "durationSeconds": 0,
            "playedAt": filename_metadata.get("playedAt"),
            "version": replay_version,
            "saveVersion": None,
            "parserVersion": PARSER_VERSION,
            "schemaVersion": UPLOAD_REPORT_SCHEMA_VERSION,
            "winningTeam": None,
            "winningPlayerSlots": [],
            "resultSource": "unknown",
            "resultConfidence": None,
            "resultExplanation": "The parser could not read enough replay data to infer a result.",
        },
        "players": [],
        "events": [
            {
                "timeSeconds": 0,
                "playerSlot": None,
                "type": "parser_limit",
                "label": "Replay uploaded, but this file format could not be fully parsed yet.",
                "metadata": {"error": parser_message, "source": "parser_fallback"},
            }
        ],
        "insights": [
            {
                "playerSlot": None,
                "category": "parser",
                "severity": "warning",
                "text": "This replay was stored successfully, but the parser could not read its header. It may be from a newer AoE2: DE build or a replay type the parser does not support yet.",
            },
            {
                "playerSlot": None,
                "category": "file",
                "severity": "info",
                "text": f"The uploaded file is {round(file_size / 1024 / 1024, 2)} MB and appears to be version {replay_version}.",
            },
        ],
        "rawInspection": {
            "parserVersion": PARSER_VERSION,
            "schemaVersion": UPLOAD_REPORT_SCHEMA_VERSION,
            "fileSizeBytes": file_size,
            "file": {
                "path": str(replay_path),
                "name": replay_path.name,
                "stem": replay_path.stem,
                "suffix": replay_path.suffix,
                "sizeBytes": file_size,
                "sha1": file_sha1,
            },
            "diagnostics": {
                "warnings": fallback_warnings,
                "parseErrors": [
                    {
                        "code": "header_parse_failed",
                        "message": parser_message,
                        "context": {"type": type(error).__name__},
                    }
                ],
                "timingsMs": {
                    "headerParse": None,
                    "operationsParse": None,
                    "totalParse": 0,
                },
                "operationIndex": 0,
                "skippedOperations": 0,
                "lastGoodOffset": 0,
                "eofOffset": file_size,
                "operationCountsTotal": 0,
                "actionCountsTotal": 0,
                "timelineEventsTotal": 1,
                "timelineEventsCaptured": 1,
                "timelineEventsTruncated": False,
                "resultInference": {
                    "source": "unknown",
                    "confidence": None,
                    "explanation": "The parser could not read enough replay data to infer a result.",
                },
                "gameDataCounts": {
                    "civilizations": 0,
                    "technologies": 0,
                    "buildings": 0,
                    "units": 0,
                    "objects": 0,
                },
            },
            "operationCounts": {},
            "actionCounts": {},
            "actionCountsByPlayer": {},
            "playerCommandData": {},
            "chats": [],
            "headerSummary": {
                "mapSeed": None,
                "revealMapId": None,
                "population": None,
                "speed": None,
                "scenarioMapId": None,
                "scenarioFilename": None,
                "lobbyName": None,
                "modName": None,
                "rmsMapId": None,
            },
            "extracted": {
                "filePreview": file_preview,
                "rawActions": [],
                "syncStats": [],
                "playerTimeseries": {},
                "viewlocks": [],
                "postgame": [],
            },
            "headerStructure": {},
            "header": None,
            "model": model_bundle,
            "summary": summary_bundle,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse an AoE2 replay into an MVP-friendly analytics report."
    )
    parser.add_argument("replay_path")
    parser.add_argument("--replay-id", default="")
    parser.add_argument("--aoe2-path", default=None)
    parser.add_argument(
        "--max-events",
        type=int,
        default=-1,
        help="Maximum timeline events in report.events. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--max-chats",
        type=int,
        default=-1,
        help="Maximum raw chat messages to include. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--max-raw-actions",
        type=int,
        default=-1,
        help="Maximum raw ACTION rows to include. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--max-viewlocks",
        type=int,
        default=-1,
        help="Maximum VIEWLOCK rows to include. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--max-sync-stats",
        type=int,
        default=-1,
        help="Maximum SYNC stat rows and per-player timeseries rows. Use -1 for unlimited.",
    )
    parser.add_argument(
        "--event-detail",
        choices=("key", "all"),
        default="all",
        help=(
            "Use 'key' for a curated report.events timeline, or 'all' to add "
            "a report.events row for every decoded ACTION plus every CHAT."
        ),
    )
    parser.add_argument("--max-operation-samples", type=int, default=-1)
    parser.add_argument("--max-positions-per-player", type=int, default=-1)
    parser.add_argument("--max-tributes-per-player", type=int, default=-1)
    parser.add_argument("--header-depth", type=int, default=25)
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Do not include the JSON-safe raw header in rawInspection.header.",
    )
    parser.add_argument(
        "--no-model",
        action="store_true",
        help="Do not attempt mgz.model.parse_match serialization.",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="Do not attempt mgz.summary.Summary extraction.",
    )
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args: argparse.Namespace | None = None
    try:
        args = parse_args()
        configure_logging(args.debug)
        report = parse_replay(args)
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2 if args.pretty else None,
                default=str,
            )
        )
    except Exception as error:
        if args is None:
            configure_logging(debug=False)
        LOGGER.exception("Replay parse failed.")
        response = (
            build_partial_report(error, args)
            if args is not None
            else build_error_response(error, args)
        )
        print(
            json.dumps(
                response,
                ensure_ascii=False,
                indent=2 if bool(getattr(args, "pretty", False)) else None,
                default=str,
            )
        )
        raise SystemExit(0 if args is not None else 1)


if __name__ == "__main__":
    main()
