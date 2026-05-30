import "dotenv/config";
import cors from "cors";
import express from "express";
import { randomUUID } from "node:crypto";
import { parseReplayFile } from "./replay-parser.js";
import {
  createReplayRecord,
  getReplayRecord,
  getReplayReport,
  listReplayRecords,
  saveReplayFile,
  saveReplayReport,
  updateReplayRecord,
} from "./replay-store.js";
import { ReplayRecord } from "./report-types.js";

const app = express();

const PORT = Number(process.env.PORT || 4000);
const FRONTEND_URL = process.env.FRONTEND_URL || "http://localhost:3000";
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
const MAX_BODY_SIZE = "75mb";

app.use(
  cors({
    origin: FRONTEND_URL,
    credentials: true,
  }),
);

app.use(express.json({ limit: MAX_BODY_SIZE }));

app.get("/health", (_req, res) => {
  res.json({
    ok: true,
    service: "backend",
  });
});

app.get("/api/replays", async (_req, res) => {
  const replays = await listReplayRecords();
  res.json({ replays });
});

app.get("/api/replays/:replayId", async (req, res) => {
  const replayId = req.params.replayId;
  const replay = await getReplayRecord(replayId);

  if (!replay) {
    res.status(404).json({
      error: "Replay not found.",
    });
    return;
  }

  const report = replay.status === "complete" ? await getReplayReport(replayId) : null;

  res.json({
    replay,
    report,
  });
});

app.post("/api/replays/upload", async (req, res) => {
  try {
    const { fileName, base64Data } = req.body as {
      fileName?: unknown;
      base64Data?: unknown;
    };

    if (typeof fileName !== "string" || !fileName.trim()) {
      res.status(400).json({ error: "A replay filename is required." });
      return;
    }

    if (!fileName.toLowerCase().endsWith(".aoe2record")) {
      res.status(400).json({ error: "Only .aoe2record files are supported." });
      return;
    }

    if (typeof base64Data !== "string" || !base64Data.trim()) {
      res.status(400).json({ error: "Replay data is required." });
      return;
    }

    const normalizedBase64 = base64Data.includes(",")
      ? base64Data.split(",").pop() ?? ""
      : base64Data;
    const buffer = Buffer.from(normalizedBase64, "base64");

    if (!buffer.length) {
      res.status(400).json({ error: "The uploaded replay file was empty." });
      return;
    }

    if (buffer.length > MAX_FILE_SIZE_BYTES) {
      res.status(400).json({
        error: "Replay files must be 50 MB or smaller for this MVP.",
      });
      return;
    }

    const replayId = randomUUID();
    const savedReplay = await saveReplayFile(replayId, fileName, buffer);
    const now = new Date().toISOString();
    const replayRecord: ReplayRecord = {
      id: replayId,
      originalFilename: fileName,
      storedFilename: savedReplay.storedFilename,
      filePath: savedReplay.filePath,
      fileSizeBytes: savedReplay.sizeBytes,
      status: "processing",
      createdAt: now,
      updatedAt: now,
      map: null,
      durationSeconds: null,
      error: null,
    };

    await createReplayRecord(replayRecord);

    try {
      const report = await parseReplayFile(savedReplay.filePath, replayId);
      await saveReplayReport(replayId, report);
      await updateReplayRecord(replayId, (record) => ({
        ...record,
        status: "complete",
        updatedAt: new Date().toISOString(),
        map: report.match.map,
        durationSeconds: report.match.durationSeconds,
        error: null,
      }));
    } catch (error) {
      const message = toErrorMessage(error);
      await updateReplayRecord(replayId, (record) => ({
        ...record,
        status: "failed",
        updatedAt: new Date().toISOString(),
        error: message,
      }));

      res.status(422).json({
        replayId,
        error: message,
      });
      return;
    }

    res.status(201).json({
      replayId,
    });
  } catch (error) {
    res.status(500).json({
      error: toErrorMessage(error),
    });
  }
});

app.listen(PORT, () => {
  console.log(`Backend running on http://localhost:${PORT}`);
});

function toErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }

  return "An unexpected replay parsing error occurred.";
}
