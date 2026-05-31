export type ReplayStatus =
  | "uploaded"
  | "processing"
  | "complete"
  | "partial"
  | "failed";

export type InsightSeverity = "info" | "warning" | "good" | "critical";

export interface ParserDiagnosticItem {
  code: string;
  message: string;
  context?: unknown;
}

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
  schemaVersion?: string;
  winningTeam: number | null;
  winningPlayerSlots: number[];
  resultSource?: string;
  resultConfidence?: "high" | "medium" | "low" | null;
  resultExplanation?: string | null;
}

export interface PlayerCommandSummary {
  totalActions: number;
  buildActions: number;
  researchActions: number;
  makeActions: number;
  moveActions: number;
  otherActions: number;
}

export interface PlayerDetectedTimings {
  technologies: Record<string, number>;
  buildings: Record<string, number>;
  units: Record<string, number>;
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
  commandSummary?: PlayerCommandSummary;
  detectedTimings?: PlayerDetectedTimings;
}

export interface ReplayEvent {
  timeSeconds: number;
  playerSlot: number | null;
  type: string;
  label: string;
  metadata?: Record<string, unknown>;
}

export interface ReplayInsight {
  playerSlot: number | null;
  category: string;
  severity: InsightSeverity;
  text: string;
}

export interface RawInspection {
  parserVersion: string;
  schemaVersion?: string;
  fileSizeBytes: number;
  file?: {
    path: string;
    name: string;
    stem: string;
    suffix: string;
    sizeBytes: number;
  };
  diagnostics?: {
    warnings: ParserDiagnosticItem[];
    parseErrors: ParserDiagnosticItem[];
    timingsMs?: Record<string, number | null>;
    operationIndex?: number;
    skippedOperations?: number;
    lastGoodOffset?: number;
    eofOffset?: number;
    operationCountsTotal?: number;
    actionCountsTotal?: number;
    timelineEventsTotal?: number;
    timelineEventsCaptured?: number;
    timelineEventsTruncated?: boolean;
    resultInference?: {
      source: string;
      confidence?: "high" | "medium" | "low" | null;
      explanation?: string;
      scoreRatio?: number;
      teamMetrics?: unknown;
    };
    gameDataCounts?: Record<string, number>;
  };
  operationCounts: Record<string, number>;
  actionCounts: Record<string, number>;
  actionCountsByPlayer?: Record<string, Record<string, number>>;
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
  ok?: boolean;
  partial?: boolean;
  error?: string;
  details?: unknown;
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
