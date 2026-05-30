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
const MAX_FILE_SIZE_MB = parsePositiveInt(process.env.REPLAY_MAX_FILE_MB, 200);
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;
const MAX_JSON_BODY_SIZE =
  process.env.REPLAY_MAX_JSON_BODY_SIZE || `${Math.ceil(MAX_FILE_SIZE_MB * 1.5)}mb`;
const MAX_RAW_BODY_SIZE = process.env.REPLAY_MAX_RAW_BODY_SIZE || `${MAX_FILE_SIZE_MB}mb`;
const allowedOrigins = new Set(
  FRONTEND_URL.split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
);

app.use(
  cors({
    origin(origin, callback) {
      if (!origin || allowedOrigins.has(origin) || isLocalDevOrigin(origin)) {
        callback(null, true);
        return;
      }

      callback(new Error(`Origin ${origin} is not allowed by CORS.`));
    },
    credentials: true,
  }),
);

app.use(express.json({ limit: MAX_JSON_BODY_SIZE }));

const replayUploadBodyParser = express.raw({
  type: ["application/octet-stream", "application/x-aoe2record"],
  limit: MAX_RAW_BODY_SIZE,
});

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

app.post("/api/replays/upload", replayUploadBodyParser, async (req, res) => {
  try {
    const uploadPayload = getUploadPayload(req);

    if ("error" in uploadPayload) {
      res.status(uploadPayload.status).json({ error: uploadPayload.error });
      return;
    }

    const { fileName, buffer } = uploadPayload;

    if (!fileName.toLowerCase().endsWith(".aoe2record")) {
      res.status(400).json({ error: "Only .aoe2record files are supported." });
      return;
    }

    if (!buffer.length) {
      res.status(400).json({ error: "The uploaded replay file was empty." });
      return;
    }

    if (buffer.length > MAX_FILE_SIZE_BYTES) {
      res.status(400).json({
        error: `Replay files must be ${MAX_FILE_SIZE_MB} MB or smaller for this MVP.`,
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

app.use(
  (
    error: unknown,
    _req: express.Request,
    res: express.Response,
    next: express.NextFunction,
  ) => {
    const maybeError = error as { status?: number; type?: string };

    if (maybeError.status === 413 || maybeError.type === "entity.too.large") {
      res.status(413).json({
        error: `Replay files must be ${MAX_FILE_SIZE_MB} MB or smaller for this MVP.`,
      });
      return;
    }

    next(error);
  },
);

app.listen(PORT, () => {
  console.log(`Backend running on http://localhost:${PORT}`);
});

type UploadPayload =
  | {
      fileName: string;
      buffer: Buffer;
    }
  | {
      status: number;
      error: string;
    };

function getUploadPayload(req: express.Request): UploadPayload {
  if (Buffer.isBuffer(req.body)) {
    const fileName = decodeHeaderValue(req.header("x-replay-filename") || "");

    if (!fileName.trim()) {
      return { status: 400, error: "A replay filename is required." };
    }

    return {
      fileName,
      buffer: req.body,
    };
  }

  if (!req.body || typeof req.body !== "object") {
    return { status: 400, error: "Replay data is required." };
  }

  const { fileName, base64Data } = req.body as {
    fileName?: unknown;
    base64Data?: unknown;
  };

  if (typeof fileName !== "string" || !fileName.trim()) {
    return { status: 400, error: "A replay filename is required." };
  }

  if (typeof base64Data !== "string" || !base64Data.trim()) {
    return { status: 400, error: "Replay data is required." };
  }

  const normalizedBase64 = base64Data.includes(",")
    ? base64Data.split(",").pop() ?? ""
    : base64Data;

  return {
    fileName,
    buffer: Buffer.from(normalizedBase64, "base64"),
  };
}

function decodeHeaderValue(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function parsePositiveInt(value: string | undefined, fallback: number) {
  if (!value) {
    return fallback;
  }

  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function toErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }

  return "An unexpected replay parsing error occurred.";
}

function isLocalDevOrigin(origin: string) {
  try {
    const parsedOrigin = new URL(origin);
    const isLocalHost =
      parsedOrigin.hostname === "localhost" ||
      parsedOrigin.hostname === "127.0.0.1" ||
      parsedOrigin.hostname.startsWith("192.168.") ||
      parsedOrigin.hostname.startsWith("10.") ||
      parsedOrigin.hostname.match(/^172\.(1[6-9]|2\d|3[0-1])\./);

    return isLocalHost && parsedOrigin.port.startsWith("30");
  } catch {
    return false;
  }
}
