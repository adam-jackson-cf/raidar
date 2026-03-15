import fs from "node:fs";
import path from "node:path";

export type VisualClip = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type VisualRegion = {
  name: string;
  weight: number;
  clip: VisualClip;
};

export type VisualViewport = {
  width: number;
  height: number;
};

export type VisualContract = {
  viewport: VisualViewport;
  regions: VisualRegion[];
};

export const DEFAULT_VISUAL_CONTRACT: VisualContract = {
  viewport: { width: 1440, height: 1024 },
  regions: [
    {
      name: "header",
      weight: 0.35,
      clip: { x: 0, y: 0, width: 1440, height: 96 },
    },
    {
      name: "hero",
      weight: 0.45,
      clip: { x: 0, y: 96, width: 1440, height: 344 },
    },
    {
      name: "features",
      weight: 0.35,
      clip: { x: 0, y: 440, width: 1440, height: 424 },
    },
    {
      name: "footer",
      weight: 0.2,
      clip: { x: 0, y: 864, width: 1440, height: 160 },
    },
  ],
};

function isValidClip(value: unknown): value is VisualClip {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as VisualClip).x === "number" &&
    typeof (value as VisualClip).y === "number" &&
    typeof (value as VisualClip).width === "number" &&
    typeof (value as VisualClip).height === "number"
  );
}

function isValidRegion(value: unknown): value is VisualRegion {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as VisualRegion).name === "string" &&
    typeof (value as VisualRegion).weight === "number" &&
    isValidClip((value as VisualRegion).clip)
  );
}

function isValidContract(value: unknown): value is VisualContract {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as VisualContract).viewport?.width === "number" &&
    typeof (value as VisualContract).viewport?.height === "number" &&
    Array.isArray((value as VisualContract).regions) &&
    (value as VisualContract).regions.every(isValidRegion)
  );
}

export function loadVisualContractFrom(
  filePath: string,
): VisualContract | null {
  try {
    const payload = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!isValidContract(payload)) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function loadRuntimeVisualContract(appDir: string): VisualContract {
  const runtimePath = path.join(appDir, ".raidar-visual-config.json");
  return loadVisualContractFrom(runtimePath) ?? DEFAULT_VISUAL_CONTRACT;
}
