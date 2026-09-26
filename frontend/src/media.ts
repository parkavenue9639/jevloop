import type { ImagePart } from "./types";

export const MAX_IMAGES = 8;
export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif";

/** Only explicit image lists become attachments; never inspect arbitrary JSON. */
export function imageParts(value: unknown): ImagePart[] {
  if (!Array.isArray(value)) return [];
  return value.filter((part): part is ImagePart => part != null && part.type === "image"
    && typeof part.asset_id === "string" && /^[a-f0-9]{64}$/.test(part.asset_id)
    && typeof part.name === "string" && typeof part.mime_type === "string"
    && Number.isInteger(part.width) && part.width > 0 && Number.isInteger(part.height) && part.height > 0
    && (part.detail === "auto" || part.detail === "high"));
}

export const assetUrl = (image: ImagePart): string => `/api/assets/${encodeURIComponent(image.asset_id)}`;

/** The server validates actual decoding, animation and aggregate budgets. */
export async function uploadImage(file: File, signal: AbortSignal): Promise<ImagePart> {
  if (!IMAGE_ACCEPT.split(",").includes(file.type)) throw new Error("PNG, JPEG, WebP or non-animated GIF required.");
  if (!file.size || file.size > MAX_IMAGE_BYTES) throw new Error("Image must be non-empty and at most 10 MiB.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  signal.throwIfAborted();
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  const response = await fetch("/api/assets", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({ name: file.name, data: btoa(binary) }),
  });
  const payload = await response.json();
  signal.throwIfAborted();
  if (!response.ok) throw new Error(payload.error ?? response.statusText);
  const image = imageParts([payload.image])[0];
  if (!image) throw new Error("Invalid image upload response.");
  return image;
}
