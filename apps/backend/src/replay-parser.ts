import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { ReplayReport } from "./report-types.js";

const execFileAsync = promisify(execFile);

const appRoot = process.cwd();

const defaultPythonBin =
  process.platform === "win32"
    ? path.join(appRoot, ".venv", "Scripts", "python.exe")
    : path.join(appRoot, ".venv", "bin", "python");

const pythonBin = process.env.REPLAY_PYTHON_BIN || defaultPythonBin;
const parserScriptPath = path.join(appRoot, "scripts", "parse_replay.py");

const PARSER_STDOUT_MAX_BUFFER = 256 * 1024 * 1024;

export async function parseReplayFile(filePath: string, replayId: string) {
  const args = [
    parserScriptPath,
    "--replay-id",
    replayId,

    // Keep the curated timeline unlimited.
    "--max-events",
    "-1",

    // Keep all useful chat and action rows.
    "--max-chats",
    "-1",
    "--max-raw-actions",
    "-1",
    "--max-sync-stats",
    "-1",

    // Viewlocks can be extremely noisy, but you asked for as much as possible.
    "--max-viewlocks",
    "-1",

    // These can also get large.
    "--max-operation-samples",
    "-1",
    "--max-positions-per-player",
    "-1",
    "--max-tributes-per-player",
    "-1",

    filePath,
  ];

  console.log("[parseReplayFile] starting parser", {
    replayId,
    filePath,
    pythonBin,
    parserScriptPath,
    maxBufferMb: PARSER_STDOUT_MAX_BUFFER / 1024 / 1024,
  });

  const { stdout, stderr } = await execFileAsync(pythonBin, args, {
    cwd: appRoot,
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
    },
    maxBuffer: PARSER_STDOUT_MAX_BUFFER,
    windowsHide: true,
  });

  if (stderr.trim()) {
    console.warn("[parseReplayFile] parser stderr", {
      replayId,
      stderr,
    });
  }

  if (!stdout.trim()) {
    throw new Error(stderr.trim() || "The replay parser returned no output.");
  }

  const report = JSON.parse(stdout) as ReplayReport;

  console.log("[parseReplayFile] parser completed", {
    replayId,
    partial: Boolean(report.partial),
    map: report.match?.map,
    durationSeconds: report.match?.durationSeconds,
    players: report.players?.length ?? 0,
    events: report.events?.length ?? 0,
    rawActions:
      report.rawInspection.extracted?.rawActionsCountReturned ??
      report.rawInspection.extracted?.rawActions?.length ??
      0,
    chats: report.rawInspection.chats?.length ?? 0,
    syncStats:
      report.rawInspection.extracted?.syncStatsCountReturned ??
      report.rawInspection.extracted?.syncStats?.length ??
      0,
    viewlocks:
      report.rawInspection.extracted?.viewlocksCountReturned ??
      report.rawInspection.extracted?.viewlocks?.length ??
      0,
  });

  return report;
}
