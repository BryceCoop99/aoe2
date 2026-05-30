import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { ReplayRecord, ReplayReport } from "./report-types.js";

const storageRoot = path.resolve(process.cwd(), "storage");
const replaysRoot = path.join(storageRoot, "replays");
const indexPath = path.join(storageRoot, "index.json");

interface ReplayIndexFile {
  replays: ReplayRecord[];
}

async function ensureStorage() {
  await mkdir(replaysRoot, { recursive: true });
}

async function readIndex(): Promise<ReplayIndexFile> {
  await ensureStorage();

  try {
    const raw = await readFile(indexPath, "utf8");
    const parsed = JSON.parse(raw) as ReplayIndexFile;

    return {
      replays: Array.isArray(parsed.replays) ? parsed.replays : [],
    };
  } catch {
    return { replays: [] };
  }
}

async function writeIndex(index: ReplayIndexFile) {
  await ensureStorage();
  await writeFile(indexPath, JSON.stringify(index, null, 2), "utf8");
}

export async function createReplayRecord(record: ReplayRecord) {
  const index = await readIndex();
  index.replays.push(record);
  await writeIndex(index);
}

export async function updateReplayRecord(
  replayId: string,
  updater: (record: ReplayRecord) => ReplayRecord,
) {
  const index = await readIndex();
  const nextReplays = index.replays.map((record) =>
    record.id === replayId ? updater(record) : record,
  );

  index.replays = nextReplays;
  await writeIndex(index);
}

export async function getReplayRecord(replayId: string) {
  const index = await readIndex();
  return index.replays.find((record) => record.id === replayId) ?? null;
}

export async function listReplayRecords(limit = 20) {
  const index = await readIndex();
  return index.replays
    .slice()
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .slice(0, limit);
}

export async function saveReplayFile(
  replayId: string,
  originalFilename: string,
  buffer: Buffer,
) {
  await ensureStorage();

  const replayDir = path.join(replaysRoot, replayId);
  await mkdir(replayDir, { recursive: true });

  const sanitizedFilename = originalFilename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const storedFilename = sanitizedFilename.endsWith(".aoe2record")
    ? sanitizedFilename
    : `${sanitizedFilename}.aoe2record`;
  const filePath = path.join(replayDir, storedFilename);

  await writeFile(filePath, buffer);

  return {
    replayDir,
    storedFilename,
    filePath,
    sizeBytes: (await stat(filePath)).size,
  };
}

export async function saveReplayReport(replayId: string, report: ReplayReport) {
  const replayDir = path.join(replaysRoot, replayId);
  await mkdir(replayDir, { recursive: true });

  const reportPath = path.join(replayDir, "report.json");
  await writeFile(reportPath, JSON.stringify(report, null, 2), "utf8");

  return reportPath;
}

export async function getReplayReport(replayId: string) {
  const reportPath = path.join(replaysRoot, replayId, "report.json");

  try {
    const raw = await readFile(reportPath, "utf8");
    return JSON.parse(raw) as ReplayReport;
  } catch {
    return null;
  }
}
