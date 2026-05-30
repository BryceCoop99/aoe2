#!/usr/bin/env python3
"""Parse an AoE2 replay into an MVP-friendly analytics report."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from mgz.const import DE_MAP_NAMES, MAP_NAMES, SPEEDS
from mgz.fast import meta, operation
from mgz.fast.enums import Action, Operation
from mgz.fast.header import parse as parse_header

PARSER_VERSION = "mgz-fast 1.0.0"
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

INTERESTING_TECH_NAMES = {
    "Bow Saw",
    "Castle Age",
    "Double-Bit Axe",
    "Feudal Age",
    "Gold Mining",
    "Hand Cart",
    "Heavy Plow",
    "Horse Collar",
    "Imperial Age",
    "Loom",
    "Stone Mining",
    "Wheelbarrow",
}

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
)

SYSTEM_AGE_UP_RE = re.compile(
    r"<player_id,(\d+),[^>]*>\s+advanced to the (Feudal|Castle|Imperial) Age\."
)


def decode_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


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


def load_game_data() -> dict[str, dict[int, str]]:
    aoe2_path = next((path for path in DEFAULT_AOE2_PATHS if path and path.exists()), None)

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

    civilizations_path = aoe2_path / "resources" / "_common" / "dat" / "civilizations.json"
    if civilizations_path.exists():
        civilizations = json.loads(civilizations_path.read_text(encoding="utf-8"))
        for index, civilization in enumerate(civilizations.get("civilization_list", [])):
            civ_names[index] = civilization.get("internal_name", f"Civ {index}")

    civ_tech_tree_dir = aoe2_path / "resources" / "_common" / "dat" / "CivTechTrees"
    for tech_tree_path in civ_tech_tree_dir.glob("*.json"):
        tech_tree = json.loads(tech_tree_path.read_text(encoding="utf-8"))
        for node in tech_tree.get("civ_techs_buildings", []):
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

    unit_lines_path = aoe2_path / "resources" / "_common" / "dat" / "unitlines.json"
    if unit_lines_path.exists():
        unit_lines = json.loads(unit_lines_path.read_text(encoding="utf-8"))
        for line in unit_lines.get("UnitLines", []):
            line_name = line.get("Name")
            if not isinstance(line_name, str) or not line_name.strip():
                continue
            for unit_id in line.get("IDChain", []):
                if isinstance(unit_id, int):
                    unit_names.setdefault(unit_id, line_name.replace(" Line", ""))

    return {
        "civilizations": civ_names,
        "technologies": tech_names,
        "buildings": building_names,
        "units": unit_names,
    }


def map_name(header: dict[str, Any]) -> tuple[str, int | None]:
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


def game_type_label(header: dict[str, Any], players: list[dict[str, Any]]) -> str:
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
    events: list[dict[str, Any]],
    time_seconds: int,
    player_slot: int | None,
    event_type: str,
    label: str,
) -> None:
    events.append(
        {
            "timeSeconds": int(time_seconds),
            "playerSlot": player_slot,
            "type": event_type,
            "label": label,
        }
    )


def normalize_team_ids(players: list[dict[str, Any]]) -> dict[int, int]:
    team_values = sorted({player["team"] for player in players if player["team"] is not None})
    return {team_id: index + 1 for index, team_id in enumerate(team_values)}


def infer_results(players: list[dict[str, Any]]) -> tuple[int | None, set[int]]:
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
        winning_slots = {player["slot"] for player in players if player["slot"] not in resigned_players}
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


def build_insights(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    insights: list[dict[str, Any]] = []
    comparable_players = [player for player in players if player["participantType"] == "human"]

    for player in players:
        castle_time = player.get("castleTimeSeconds")
        if castle_time is not None and castle_time < 20 * 60:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "timing",
                    "severity": "good",
                    "text": f"{player['name']} reached Castle Age at {format_seconds(castle_time)}, which is a strong timing for many standard openings.",
                }
            )

        second_tc_time = player.get("firstTownCenterAfterCastleTimeSeconds")
        if castle_time is not None and second_tc_time is None:
            insights.append(
                {
                    "playerSlot": player["slot"],
                    "category": "economy",
                    "severity": "warning",
                    "text": f"{player['name']} did not add a second Town Center within 3 minutes of reaching Castle Age.",
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

    return insights


def compare_timing(
    insights: list[dict[str, Any]],
    label: str,
    field: str,
    threshold: int,
    first: dict[str, Any],
    second: dict[str, Any],
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay_path")
    parser.add_argument("--replay-id", default="")
    args = parser.parse_args()

    replay_path = Path(args.replay_path)
    if not replay_path.exists():
        raise FileNotFoundError(f"Replay file not found: {replay_path}")

    game_data = load_game_data()

    with replay_path.open("rb") as replay_file:
        header = parse_header(replay_file)

    de_players = (header.get("de") or {}).get("players") or []
    participants: list[dict[str, Any]] = []

    for raw_player in de_players:
        name = decode_text(raw_player.get("name")).strip()
        ai_name = decode_text(raw_player.get("ai_name")).strip()
        participant_name = name or ai_name
        slot = raw_player.get("number")
        if not participant_name or not isinstance(slot, int) or slot <= 0:
            continue

        participant_type = "other"
        if raw_player.get("type") == 2:
            participant_type = "human"
        elif raw_player.get("type") == 4:
            participant_type = "ai"

        participants.append(
            {
                "slot": slot,
                "name": participant_name,
                "civilization": game_data["civilizations"].get(
                    raw_player.get("civilization_id"),
                    f"Civ {raw_player.get('civilization_id')}",
                ),
                "civilizationId": raw_player.get("civilization_id"),
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
            }
        )

    participants.sort(key=lambda player: player["slot"])
    players_by_slot = {player["slot"]: player for player in participants}

    operation_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    chats: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    age_ups: dict[int, dict[str, int]] = defaultdict(dict)
    age_research_starts: dict[int, dict[str, int]] = defaultdict(dict)
    tc_build_times: dict[int, list[int]] = defaultdict(list)
    duration_ms = 0

    with replay_path.open("rb") as replay_file:
        parse_header(replay_file)
        meta(replay_file)
        eof = os.fstat(replay_file.fileno()).st_size

        while replay_file.tell() < eof:
            try:
                op_type, payload = operation(replay_file)
            except EOFError:
                break

            operation_counts[op_type.name] += 1

            if op_type == Operation.SYNC:
                duration_ms += payload[0]
                continue

            current_second = int(duration_ms / 1000)

            if op_type == Operation.CHAT:
                decoded_chat = decode_text(payload)
                try:
                    parsed_chat = json.loads(decoded_chat)
                except json.JSONDecodeError:
                    parsed_chat = {"message": decoded_chat}

                chat_player = parsed_chat.get("player")
                chat_message = str(parsed_chat.get("message", "")).strip()
                chats.append(
                    {
                        "timeSeconds": current_second,
                        "playerSlot": chat_player if isinstance(chat_player, int) else None,
                        "message": chat_message or decoded_chat,
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
                            f"{players_by_slot.get(player_slot, {'name': f'Player {player_slot}'})['name']} reached {age_label}.",
                        )
                continue

            if op_type != Operation.ACTION:
                continue

            action_type, action_data = payload
            action_counts[action_type.name] += 1
            player_slot = action_data.get("player_id") if isinstance(action_data, dict) else None
            player = players_by_slot.get(player_slot)

            if action_type == Action.RESIGN and player is not None:
                player["resignedAtSeconds"] = current_second
                add_timeline_event(
                    events,
                    current_second,
                    player_slot,
                    "resign",
                    f"{player['name']} resigned.",
                )
                continue

            if action_type == Action.RESEARCH and player is not None:
                technology_id = action_data.get("technology_id")
                tech_name = game_data["technologies"].get(
                    technology_id,
                    f"Tech {technology_id}",
                )

                if tech_name == "Loom" and player["loomTimeSeconds"] is None:
                    player["loomTimeSeconds"] = current_second

                if tech_name in {"Feudal Age", "Castle Age", "Imperial Age"}:
                    age_research_starts[player_slot][tech_name] = current_second

                if tech_name in INTERESTING_TECH_NAMES:
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "technology",
                        f"{player['name']} started {tech_name}.",
                    )
                continue

            if action_type == Action.BUILD and player is not None:
                building_id = action_data.get("building_id")
                building_name = game_data["buildings"].get(
                    building_id,
                    f"Building {building_id}",
                )

                if building_name in KEY_BUILDING_NAMES:
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "building",
                        f"{player['name']} placed {building_name}.",
                    )

                if building_name in MILITARY_BUILDING_NAMES and player["firstMilitaryBuildingTimeSeconds"] is None:
                    player["firstMilitaryBuildingTimeSeconds"] = current_second

                if building_name == "Market" and player["firstMarketTimeSeconds"] is None:
                    player["firstMarketTimeSeconds"] = current_second

                if building_name == "Blacksmith" and player["firstBlacksmithTimeSeconds"] is None:
                    player["firstBlacksmithTimeSeconds"] = current_second

                if building_name == "Castle" and player["firstCastleTimeSeconds"] is None:
                    player["firstCastleTimeSeconds"] = current_second

                if building_name == "Town Center":
                    tc_build_times[player_slot].append(current_second)
                continue

            if action_type == Action.MAKE and player is not None:
                unit_id = action_data.get("unit_id")
                unit_name = game_data["units"].get(unit_id, f"Unit {unit_id}")
                if is_military_unit(unit_name) and player["firstMilitaryUnitTimeSeconds"] is None:
                    player["firstMilitaryUnitTimeSeconds"] = current_second
                    add_timeline_event(
                        events,
                        current_second,
                        player_slot,
                        "unit",
                        f"{player['name']} completed {unit_name}.",
                    )

    for player in participants:
        slot = player["slot"]
        player["feudalTimeSeconds"] = age_ups.get(slot, {}).get("Feudal Age") or age_research_starts.get(slot, {}).get("Feudal Age")
        player["castleTimeSeconds"] = age_ups.get(slot, {}).get("Castle Age") or age_research_starts.get(slot, {}).get("Castle Age")
        player["imperialTimeSeconds"] = age_ups.get(slot, {}).get("Imperial Age") or age_research_starts.get(slot, {}).get("Imperial Age")

        castle_time = player["castleTimeSeconds"]
        if castle_time is not None:
            tc_after_castle = [
                build_time
                for build_time in tc_build_times.get(slot, [])
                if castle_time < build_time <= castle_time + 180
            ]
            if tc_after_castle:
                player["firstTownCenterAfterCastleTimeSeconds"] = min(tc_after_castle)

    team_map = normalize_team_ids(participants)
    for player in participants:
        if player["team"] is not None:
            player["team"] = team_map.get(player["team"], player["team"])

    winning_team, winning_slots = infer_results(participants)

    for player in participants:
        if player["slot"] in winning_slots:
            player["result"] = "win"
        elif player.get("resignedAtSeconds") is not None:
            player["result"] = "loss"

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
        "winningTeam": winning_team,
        "winningPlayerSlots": sorted(winning_slots),
    }

    report = {
        "match": match,
        "players": participants,
        "events": sorted(events, key=lambda event: (event["timeSeconds"], event["playerSlot"] or 0))[:160],
        "insights": build_insights(participants),
        "rawInspection": {
            "parserVersion": PARSER_VERSION,
            "fileSizeBytes": replay_path.stat().st_size,
            "operationCounts": dict(operation_counts.most_common()),
            "actionCounts": dict(action_counts.most_common()),
            "chats": chats[:50],
            "headerSummary": {
                "mapSeed": (header.get("lobby") or {}).get("seed"),
                "revealMapId": (header.get("lobby") or {}).get("reveal_map_id"),
                "population": (header.get("lobby") or {}).get("population"),
                "speed": speed_label((header.get("metadata") or {}).get("speed")),
                "scenarioMapId": (header.get("scenario") or {}).get("map_id"),
                "scenarioFilename": decode_text((header.get("scenario") or {}).get("scenario_filename")) or None,
                "lobbyName": decode_text((header.get("de") or {}).get("lobby")) or None,
                "modName": decode_text((header.get("de") or {}).get("mod")) or None,
                "rmsMapId": (header.get("de") or {}).get("rms_map_id"),
            },
        },
    }

    print(json.dumps(report))


if __name__ == "__main__":
    main()
