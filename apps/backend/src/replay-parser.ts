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

export async function parseReplayFile(filePath: string, replayId: string) {
  const { stdout, stderr } = await execFileAsync(
    pythonBin,
    [parserScriptPath, "--replay-id", replayId, filePath],
    {
      cwd: appRoot,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      maxBuffer: 20 * 1024 * 1024,
      windowsHide: true,
    },
  );

  if (!stdout.trim()) {
    throw new Error(stderr.trim() || "The replay parser returned no output.");
  }

  return JSON.parse(stdout) as ReplayReport;
}
