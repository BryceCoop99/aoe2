export type ReplayStatus = "uploaded" | "processing" | "complete" | "failed";

export type InsightSeverity = "info" | "warning" | "good" | "critical";

export interface MatchOverview {
  id: string;
  map: string;
  mapId: number | null;
  gameType: string;
  durationSeconds: number;
  playedAt: string | null;
  version: string;
  saveVersion: number | null;
  parserVersion: string;
  winningTeam: number | null;
  winningPlayerSlots: number[];
}

export interface PlayerSummary {
  slot: number;
  name: string;
  civilization: string;
  civilizationId: number | null;
  team: number | null;
  participantType: "human" | "ai" | "other";
  result: "win" | "loss" | "unknown";
  feudalTimeSeconds: number | null;
  castleTimeSeconds: number | null;
  imperialTimeSeconds: number | null;
  loomTimeSeconds: number | null;
  firstMilitaryBuildingTimeSeconds: number | null;
  firstMilitaryUnitTimeSeconds: number | null;
  firstMarketTimeSeconds: number | null;
  firstBlacksmithTimeSeconds: number | null;
  firstTownCenterAfterCastleTimeSeconds: number | null;
  firstCastleTimeSeconds: number | null;
  resignedAtSeconds: number | null;
}

export interface ReplayEvent {
  timeSeconds: number;
  playerSlot: number | null;
  type: string;
  label: string;
}

export interface ReplayInsight {
  playerSlot: number | null;
  category: string;
  severity: InsightSeverity;
  text: string;
}

export interface RawInspection {
  parserVersion: string;
  fileSizeBytes: number;
  operationCounts: Record<string, number>;
  actionCounts: Record<string, number>;
  chats: Array<{
    timeSeconds: number;
    playerSlot: number | null;
    message: string;
  }>;
  headerSummary: {
    mapSeed: number | null;
    revealMapId: number | null;
    population: number | null;
    speed: string | null;
    scenarioMapId: number | null;
    scenarioFilename: string | null;
    lobbyName: string | null;
    modName: string | null;
    rmsMapId: number | null;
  };
}

export interface ReplayReport {
  match: MatchOverview;
  players: PlayerSummary[];
  events: ReplayEvent[];
  insights: ReplayInsight[];
  rawInspection: RawInspection;
}

export interface ReplayRecord {
  id: string;
  originalFilename: string;
  storedFilename: string;
  filePath: string;
  fileSizeBytes: number;
  status: ReplayStatus;
  createdAt: string;
  updatedAt: string;
  map: string | null;
  durationSeconds: number | null;
  error: string | null;
}
