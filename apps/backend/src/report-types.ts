export type ReplayStatus =
  | "uploaded"
  | "processing"
  | "complete"
  | "partial"
  | "failed";

export type InsightSeverity = "info" | "warning" | "good" | "critical";

export type ResultConfidence = "high" | "medium" | "low" | null;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface ParserDiagnosticItem {
  code: string;
  message: string;
  context?: unknown;
}

export interface ResultInference {
  source: string;
  confidence?: ResultConfidence;
  explanation?: string;
  scoreRatio?: number;
  teamMetrics?: unknown;
  winningTeam?: number | null;
  winningPlayerSlots?: number[];
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
  resultConfidence?: ResultConfidence;
  resultExplanation?: string | null;
}

export interface PlayerCommandSummary {
  totalActions: number;
  buildActions: number;
  researchActions: number;
  makeActions: number;
  moveActions: number;
  otherActions: number;

  /**
   * Exhaustive parser adds per-action-type counts here, for example:
   * { BUILD: 12, MOVE: 300, RESEARCH: 4 }
   */
  actionTypes?: Record<string, number>;
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

  /**
   * Exhaustive parser uses metadata for source, operationIndex,
   * unit/building/tech IDs, map coordinates, parser fallback info, etc.
   */
  metadata?: Record<string, unknown>;
}

export interface ReplayInsight {
  playerSlot: number | null;
  category: string;
  severity: InsightSeverity;
  text: string;
}

export interface ReplayFileInspection {
  path: string;
  name: string;
  stem: string;
  suffix: string;
  sizeBytes: number;
  sha1?: string | null;
}

export interface ReplayChatMessage {
  operationIndex?: number;
  offset?: number;
  timeSeconds: number;
  playerSlot: number | null;
  message: string;
  raw?: unknown;
}

export interface RawActionRow {
  operationIndex: number;
  offset: number;
  timeSeconds: number;
  playerSlot: number | null;
  actionType: string;
  payload: unknown;
}

export interface ViewlockRow {
  operationIndex: number;
  offset: number;
  timeSeconds: number;
  payload: unknown;
}

export interface PostgameRow {
  operationIndex: number;
  offset: number;
  timeSeconds: number;
  payload: unknown;
}

export interface OperationSampleRow {
  operationIndex: number;
  offset: number;
  payload: unknown;
}

export interface PlayerTimeseriesRow {
  timeSeconds: number;
  totalResources?: number | null;
  objectCount?: number | null;
  raw?: unknown;
}

export interface SyncStatRow {
  operationIndex: number;
  timeSeconds: number;
  currentTimeMilliseconds?: number | null;
  players?: Record<string, PlayerTimeseriesRow>;
  raw: unknown;
}

export interface FilePreview {
  length: number;
  hexPreview: string;
}

export interface ParserBundle {
  available: boolean;
  skipped?: boolean;
  reason?: string;
  parseMs?: number;
  data?: unknown;
  methodErrors?: Record<
    string,
    {
      error: string;
      type: string;
    }
  >;
  error?: string;
  type?: string;
}

export interface NamedTimingBucket {
  count: number;
  firstTimeSeconds: number;
  lastTimeSeconds: number;
  ids: unknown[];
  firstPayload?: unknown;
}

export interface PlayerCommandData {
  actionCounts: Record<string, number>;
  researches: Record<string, NamedTimingBucket>;
  buildings: Record<string, NamedTimingBucket>;
  units: Record<string, NamedTimingBucket>;
  tributes: Array<{
    timeSeconds: number;
    payload: unknown;
  }>;
  positions: Array<{
    timeSeconds: number;
    actionType: string;
    x: number | null;
    y: number | null;
  }>;
  commandIdCounts: Record<string, number>;
  orderIdCounts: Record<string, number>;
  resourceIdCounts: Record<string, number>;
  formationIdCounts: Record<string, number>;
  stanceIdCounts: Record<string, number>;
}

export interface RawInspectionDiagnostics {
  warnings: ParserDiagnosticItem[];
  parseErrors: ParserDiagnosticItem[];

  /**
   * Counts of rows omitted because of max-* parser limits.
   * Example: { rawActions: 12000, viewlocks: 500 }
   */
  truncation?: Record<string, number>;

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

  resultInference?: ResultInference;

  gameDataCounts?: Record<string, number>;
  gameDataReference?: unknown;
}

export interface ReplayHeaderSummary {
  mapSeed: number | null;
  revealMapId: number | null;
  population: number | null;
  speed: string | null;
  scenarioMapId: number | null;
  scenarioFilename: string | null;
  lobbyName: string | null;
  modName: string | null;
  rmsMapId: number | null;

  /**
   * Extra fields produced by the exhaustive parser.
   */
  gameVersion?: string | null;
  saveVersion?: number | null;
  logVersion?: number | null;
  versionKind?: string | null;
  rawSpeed?: unknown;
  ownerId?: number | null;
  rmsModId?: number | null;
  difficultyId?: number | null;
  startingAgeId?: number | null;
  startingAge?: string | null;
  allTechnologies?: boolean | null;
  teamTogether?: boolean | null;
  lockSpeed?: boolean | null;
  mapDimension?: number | null;
  restoreTime?: number | null;
  timestamp?: unknown;
}

export interface RawInspectionExtracted {
  /**
   * Partial-report fallback only.
   */
  filePreview?: FilePreview | null;

  /**
   * Count of curated timeline events before max-events limiting.
   */
  eventsAllCount?: number;

  /**
   * Raw ACTION stream. This is the closest thing to “all command events”
   * from the replay body.
   */
  rawActions?: RawActionRow[];
  rawActionsCountReturned?: number;
  rawActionsTruncatedCount?: number;

  /**
   * VIEWLOCK rows can be very numerous.
   */
  viewlocks?: ViewlockRow[];
  viewlocksCountReturned?: number;
  viewlocksTruncatedCount?: number;

  /**
   * SYNC stat rows and per-player timeseries, when available in the replay.
   */
  syncStats?: SyncStatRow[];
  syncStatsCountReturned?: number;
  syncStatsTruncatedCount?: number;
  playerTimeseries?: Record<string, PlayerTimeseriesRow[]>;

  /**
   * POSTGAME payloads, when available.
   */
  postgame?: PostgameRow[];

  /**
   * Small samples of each operation type for debugging parser behavior.
   */
  operationSamplesByType?: Record<string, OperationSampleRow[]>;
}

export interface RawInspection {
  parserVersion: string;
  schemaVersion?: string;
  fileSizeBytes: number;

  file?: ReplayFileInspection;

  diagnostics?: RawInspectionDiagnostics;

  operationCounts: Record<string, number>;
  actionCounts: Record<string, number>;
  actionCountsByPlayer?: Record<string, Record<string, number>>;

  /**
   * Per-player extracted command summaries, including all named researches,
   * buildings, units, tribute rows, position rows, and low-level ID counters.
   */
  playerCommandData?: Record<string, PlayerCommandData>;

  chats: ReplayChatMessage[];

  headerSummary: ReplayHeaderSummary;

  /**
   * Shape-only header outline for browsing/debugging.
   */
  headerStructure?: unknown;

  /**
   * JSON-safe raw parsed header. This can be large.
   */
  header?: unknown;

  /**
   * Additional extracted low-level replay data.
   */
  extracted?: RawInspectionExtracted;

  /**
   * Optional mgz.model.parse_match serialized bundle.
   */
  model?: ParserBundle;

  /**
   * Optional mgz.summary.Summary bundle.
   */
  summary?: ParserBundle;
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
