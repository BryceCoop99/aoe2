#!/usr/bin/env python3
"""Parse an AoE2 replay into an MVP-friendly analytics report.

Important:
- The final parse result is printed to stdout as JSON.
- Debug logs are written to stderr so the Node server can safely parse stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

from mgz.const import DE_MAP_NAMES, MAP_NAMES, SPEEDS
from mgz.fast import meta, operation
from mgz.fast.enums import Action, Operation
from mgz.fast.header import parse as parse_header

PARSER_VERSION = "mgz-fast 1.0.0"
UPLOAD_REPORT_SCHEMA_VERSION = "replay-report-v1"

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
    "Double-Bit Axe",
    "Gold Mining",
    "Hand Cart",
    "Heavy Plow",
    "Horse Collar",
    "Loom",
    "Stone Mining",
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


def add_warning(
    warnings: list[JsonDict], code: str, message: str, **context: Any
) -> None:
    warnings.append(
        {
            "code": code,
            "message": message,
            "context": context,
        }
    )
    LOGGER.warning("%s: %s %s", code, message, safe_json_dumps(context))


def add_parse_error(
    errors: list[JsonDict],
    code: str,
    message: str,
    **context: Any,
) -> None:
    errors.append(
        {
            "code": code,
            "message": message,
            "context": context,
        }
    )
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
        add_warning(
            warnings,
            "game_data_file_missing",
            "Expected game data file was not found.",
            path=str(path),
        )
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
    explicit_path: str | None,
    warnings: list[JsonDict],
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
        "AoE2 install path was not found. The report will use fallback IDs for civs, techs, buildings, and units.",
        explicitPath=explicit_path,
        envPath=ENV_AOE2_INSTALL_PATH,
        searchedPaths=[str(path) for path in candidate_paths],
    )
    return None


def load_game_data(
    explicit_aoe2_path: str | None,
    warnings: list[JsonDict],
) -> dict[str, dict[int, str]]:
    aoe2_path = resolve_aoe2_path(explicit_aoe2_path, warnings)

    civ_names: dict[int, str] = {}
    tech_names: dict[int, str] = {}
    building_names: dict[int, str] = {}
    unit_names: dict[int, str] = {}

    if aoe2_path is None:
        return {
            "civilizations": civ_names,
            "technologies": tech_names,
            "buildings": building_names,
            "units": unit_names,
        }

    civilizations_path = (
        aoe2_path / "resources" / "_common" / "dat" / "civilizations.json"
    )
    civilizations = read_json_file(civilizations_path, warnings)

    if civilizations:
        for index, civilization in enumerate(
            civilizations.get("civilization_list", [])
        ):
            if not isinstance(civilization, dict):
                continue

            civ_names[index] = civilization.get("internal_name", f"Civ {index}")

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
                    building_names.setdefault(node_id, name)
                elif "Research" in node_type:
                    tech_names.setdefault(node_id, name)
                elif "Unit" in node_type or use_type == "Unit":
                    unit_names.setdefault(node_id, name)
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
                    unit_names.setdefault(unit_id, line_name.replace(" Line", ""))

    LOGGER.info(
        "Loaded game data counts: civs=%s techs=%s buildings=%s units=%s",
        len(civ_names),
        len(tech_names),
        len(building_names),
        len(unit_names),
    )

    return {
        "civilizations": civ_names,
        "technologies": tech_names,
        "buildings": building_names,
        "units": unit_names,
    }


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

    return (
        DE_MAP_NAMES.get(candidate_map_id)
        or MAP_NAMES.get(candidate_map_id)
        or f"Map {candidate_map_id}",
        candidate_map_id,
    )


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
    if not unit_name:
        return False

    if unit_name.startswith("Unit "):
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

        if feudal_time is not None and feudal_time < 11 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "good",
                    "text": f"{player['name']} reached Feudal Age at {format_seconds(feudal_time)}, which is a quick Feudal timing.",
                }
            )

        if castle_time is not None and castle_time < 20 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "good",
                    "text": f"{player['name']} reached Castle Age at {format_seconds(castle_time)}, which is a strong timing for many standard openings.",
                }
            )

        if castle_time is not None and second_tc_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "economy",
                    "severity": "warning",
                    "text": f"{player['name']} did not add a second Town Center within 3 minutes of reaching Castle Age.",
                }
            )

        if first_military_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "military",
                    "severity": "info",
                    "text": f"{player['name']} had no detected military building placement in the parsed command stream.",
                }
            )

        if loom_time is None:
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
            insights,
            "Feudal Age",
            "feudalTimeSeconds",
            30,
            first,
            second,
            "timing",
        )
        compare_timing(
            insights,
            "Castle Age",
            "castleTimeSeconds",
            60,
            first,
            second,
            "timing",
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
            insights,
            "Loom",
            "loomTimeSeconds",
            30,
            first,
            second,
            "economy",
        )

    return insights


def parse_participants(
    header: JsonDict,
    game_data: dict[str, dict[int, str]],
    warnings: list[JsonDict],
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
                rawPlayer=raw_player,
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
                    civilization_id,
                    f"Civ {civilization_id}",
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

    if action_name == "BUILD":
        summary["buildActions"] += 1
    elif action_name == "RESEARCH":
        summary["researchActions"] += 1
    elif action_name == "MAKE":
        summary["makeActions"] += 1
    elif action_name in {"MOVE", "ORDER", "WAYPOINT"}:
        summary["moveActions"] += 1
    else:
        summary["otherActions"] += 1


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


def parse_chat_payload(payload: Any) -> JsonDict:
    decoded_chat = decode_text(payload)

    try:
        parsed_chat = json.loads(decoded_chat)
        if isinstance(parsed_chat, dict):
            return parsed_chat
    except json.JSONDecodeError:
        pass

    return {"message": decoded_chat}


def parse_replay(args: argparse.Namespace) -> JsonDict:
    parse_started_at = perf_counter()

    replay_path = Path(args.replay_path)
    warnings: list[JsonDict] = []
    parse_errors: list[JsonDict] = []

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

    participants = parse_participants(header, game_data, warnings)
    players_by_slot = {player["slot"]: player for player in participants}

    operation_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    action_counts_by_player: dict[int, Counter[str]] = defaultdict(Counter)

    chats: list[JsonDict] = []
    events: list[JsonDict] = []

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
                except Exception as error:
                    add_warning(
                        warnings,
                        "sync_payload_invalid",
                        "SYNC payload did not contain a valid time delta.",
                        operationIndex=operation_index,
                        offset=current_offset,
                        payload=safe_json_dumps(payload),
                        error=str(error),
                    )
                continue

            current_second = int(duration_ms / 1000)

            if op_type == Operation.CHAT:
                parsed_chat = parse_chat_payload(payload)

                chat_player = parsed_chat.get("player")
                chat_message = str(parsed_chat.get("message", "")).strip()

                chats.append(
                    {
                        "timeSeconds": current_second,
                        "playerSlot": (
                            chat_player if isinstance(chat_player, int) else None
                        ),
                        "message": chat_message or decode_text(payload),
                    }
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
                    payload=safe_json_dumps(payload),
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
                    actionData=safe_json_dumps(action_data),
                )
                continue

            player_slot = action_data.get("player_id")
            player = players_by_slot.get(player_slot)

            if player is not None:
                action_counts_by_player[player_slot][action_name] += 1
                update_action_summary(player, action_type)

            if action_type == Action.RESIGN and player is not None:
                player["resignedAtSeconds"] = current_second

                add_timeline_event(
                    events,
                    current_second,
                    player_slot,
                    "resign",
                    f"{player['name']} resigned.",
                    {
                        "source": "action",
                    },
                )
                continue

            if action_type == Action.RESEARCH and player is not None:
                technology_id = action_data.get("technology_id")
                tech_name = game_data["technologies"].get(
                    technology_id,
                    f"Tech {technology_id}",
                )

                player["detectedTimings"]["technologies"].setdefault(
                    tech_name,
                    current_second,
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
                        },
                    )
                continue

            if action_type == Action.BUILD and player is not None:
                building_id = action_data.get("building_id")
                building_name = game_data["buildings"].get(
                    building_id,
                    f"Building {building_id}",
                )

                player["detectedTimings"]["buildings"].setdefault(
                    building_name,
                    current_second,
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

            if action_type == Action.MAKE and player is not None:
                unit_id = action_data.get("unit_id")
                unit_name = game_data["units"].get(unit_id, f"Unit {unit_id}")

                player["detectedTimings"]["units"].setdefault(
                    unit_name,
                    current_second,
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
                        f"{player['name']} completed {unit_name}.",
                        {
                            "source": "action",
                            "unitId": unit_id,
                            "unitName": unit_name,
                        },
                    )

    operations_parse_ms = round(
        (perf_counter() - operations_parse_started_at) * 1000, 2
    )

    finalize_player_timings(
        participants,
        age_ups,
        age_research_starts,
        tc_build_times,
    )

    normalize_player_teams(participants)
    winning_team, winning_slots = apply_results(participants)

    map_label, map_id = map_name(header)
    duration_seconds = int(duration_ms / 1000)

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
    }

    sorted_events = sorted(
        events,
        key=lambda event: (event["timeSeconds"], event["playerSlot"] or 0),
    )

    total_parse_ms = round((perf_counter() - parse_started_at) * 1000, 2)

    report = {
        "ok": True,
        "match": match,
        "players": participants,
        "events": sorted_events[: args.max_events],
        "insights": build_insights(participants),
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
            },
            "diagnostics": {
                "warnings": warnings,
                "parseErrors": parse_errors,
                "timingsMs": {
                    "headerParse": header_parse_ms,
                    "operationsParse": operations_parse_ms,
                    "totalParse": total_parse_ms,
                },
                "operationIndex": operation_index,
                "skippedOperations": skipped_operations,
                "lastGoodOffset": last_good_offset,
                "eofOffset": file_size,
                "operationCountsTotal": sum(operation_counts.values()),
                "actionCountsTotal": sum(action_counts.values()),
                "gameDataCounts": {
                    "civilizations": len(game_data["civilizations"]),
                    "technologies": len(game_data["technologies"]),
                    "buildings": len(game_data["buildings"]),
                    "units": len(game_data["units"]),
                },
            },
            "operationCounts": dict(operation_counts.most_common()),
            "actionCounts": dict(action_counts.most_common()),
            "actionCountsByPlayer": {
                str(player_slot): dict(counter.most_common())
                for player_slot, counter in action_counts_by_player.items()
            },
            "chats": chats[: args.max_chats],
            "headerSummary": {
                "mapSeed": (header.get("lobby") or {}).get("seed"),
                "revealMapId": (header.get("lobby") or {}).get("reveal_map_id"),
                "population": (header.get("lobby") or {}).get("population"),
                "speed": speed_label((header.get("metadata") or {}).get("speed")),
                "scenarioMapId": (header.get("scenario") or {}).get("map_id"),
                "scenarioFilename": decode_text(
                    (header.get("scenario") or {}).get("scenario_filename")
                )
                or None,
                "lobbyName": decode_text((header.get("de") or {}).get("lobby")) or None,
                "modName": decode_text((header.get("de") or {}).get("mod")) or None,
                "rmsMapId": (header.get("de") or {}).get("rms_map_id"),
            },
        },
    }

    LOGGER.info(
        "Replay parse complete: durationSeconds=%s players=%s events=%s warnings=%s errors=%s totalMs=%s",
        duration_seconds,
        len(participants),
        len(sorted_events),
        len(warnings),
        len(parse_errors),
        total_parse_ms,
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
        played_at = (
            f"{groups['year']}-{groups['month']}-{groups['day']}T"
            f"{groups['hour']}:{groups['minute']}:{groups['second']}"
        )

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
        },
        "players": [],
        "events": [
            {
                "timeSeconds": 0,
                "playerSlot": None,
                "type": "parser_limit",
                "label": "Replay uploaded, but this file format could not be fully parsed yet.",
                "metadata": {
                    "error": parser_message,
                    "source": "parser_fallback",
                },
            }
        ],
        "insights": [
            {
                "playerSlot": None,
                "category": "parser",
                "severity": "warning",
                "text": "This replay was stored successfully, but the parser could not read its header. It may be from a newer AoE2: DE build or a replay type the MVP parser does not support yet.",
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
            },
            "diagnostics": {
                "warnings": [
                    {
                        "code": "partial_report",
                        "message": "The replay was saved, but structured parsing failed.",
                        "context": {
                            "fallback": "filename_and_file_inspection",
                        },
                    }
                ],
                "parseErrors": [
                    {
                        "code": "header_parse_failed",
                        "message": parser_message,
                        "context": {
                            "type": type(error).__name__,
                        },
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
                "gameDataCounts": {
                    "civilizations": 0,
                    "technologies": 0,
                    "buildings": 0,
                    "units": 0,
                },
            },
            "operationCounts": {},
            "actionCounts": {},
            "actionCountsByPlayer": {},
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
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse an AoE2 replay into an MVP-friendly analytics report."
    )

    parser.add_argument("replay_path")
    parser.add_argument("--replay-id", default="")
    parser.add_argument("--aoe2-path", default=None)
    parser.add_argument("--max-events", type=int, default=160)
    parser.add_argument("--max-chats", type=int, default=50)
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
