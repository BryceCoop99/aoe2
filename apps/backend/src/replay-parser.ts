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
const maxTimelineEvents = parseInteger(
  process.env.REPLAY_MAX_TIMELINE_EVENTS,
  -1,
).toString();
const parserStdoutBufferMb = parsePositiveInt(
  process.env.REPLAY_PARSER_STDOUT_BUFFER_MB,
  100,
);

export async function parseReplayFile(filePath: string, replayId: string) {
  const { stdout, stderr } = await execFileAsync(
    pythonBin,
    [
      parserScriptPath,
      "--replay-id",
      replayId,
      "--max-events",
      maxTimelineEvents,
      filePath,
    ],
    {
      cwd: appRoot,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      maxBuffer: parserStdoutBufferMb * 1024 * 1024,
      windowsHide: true,
    },
  );

  if (!stdout.trim()) {
    throw new Error(stderr.trim() || "The replay parser returned no output.");
  }

  return JSON.parse(stdout) as ReplayReport;
}

function parsePositiveInt(value: string | undefined, fallback: number) {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseInteger(value: string | undefined, fallback: number) {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}
