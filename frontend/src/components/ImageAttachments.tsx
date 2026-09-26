import { useState } from "react";
import type { ImagePart } from "../types";
import { assetUrl } from "../media";
import { useLang } from "../i18n";

function Attachment({ image }: { image: ImagePart }) {
  const [failed, setFailed] = useState(false);
  const { lang } = useLang();
  return <a href={assetUrl(image)} target="_blank" rel="noreferrer" className="block w-32 overflow-hidden rounded-xl border border-line bg-surface text-ink">
    {failed ? <p role="status" className="p-2 text-xs text-critical">{lang === "zh" ? "图片不可用" : "Image unavailable"}</p>
      : <img src={assetUrl(image)} alt={image.name} loading="lazy" onError={() => setFailed(true)} className="h-24 w-full object-contain" />}
    <span className="block truncate px-2 py-1 text-xs" title={image.name}>{image.name}</span>
  </a>;
}

/** Same immutable asset URL in live conversation, history and replay. */
export function ImageAttachments({ images }: { images: ImagePart[] }) {
  if (!images.length) return null;
  return <div data-testid="image-attachments" className="my-2 flex flex-wrap gap-2">
    {images.map((image, index) => <Attachment key={`${image.asset_id}-${index}`} image={image} />)}
  </div>;
}
