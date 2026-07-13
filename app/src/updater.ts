import { check, type DownloadEvent, type Update } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

export interface DesktopUpdate {
  version: string;
  currentVersion: string;
  notes?: string;
  date?: string;
}

export type UpdateProgress =
  | { phase: "started"; contentLength?: number }
  | { phase: "progress"; downloaded: number; contentLength?: number }
  | { phase: "finished" };

let pendingUpdate: Update | null = null;

export async function checkDesktopUpdate(): Promise<DesktopUpdate | null> {
  pendingUpdate?.close().catch(() => {});
  pendingUpdate = await check();
  if (!pendingUpdate) return null;
  return {
    version: pendingUpdate.version,
    currentVersion: pendingUpdate.currentVersion,
    notes: pendingUpdate.body,
    date: pendingUpdate.date,
  };
}

export async function installPendingUpdate(onProgress: (progress: UpdateProgress) => void): Promise<void> {
  const update = pendingUpdate ?? await check();
  if (!update) throw new Error("No update is available to install.");

  let downloaded = 0;
  let contentLength: number | undefined;
  await update.downloadAndInstall((event: DownloadEvent) => {
    if (event.event === "Started") {
      downloaded = 0;
      contentLength = event.data.contentLength;
      onProgress({ phase: "started", contentLength });
    } else if (event.event === "Progress") {
      downloaded += event.data.chunkLength;
      onProgress({ phase: "progress", downloaded, contentLength });
    } else {
      onProgress({ phase: "finished" });
    }
  });
  pendingUpdate = null;
  await relaunch();
}
