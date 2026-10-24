/**
 * The About card of the desktop game view: a cover, the title, the summary, and
 * the metadata rows `utils/metadataRows.ts` answers.
 */

import { useState, type CSSProperties, type FC, type ReactElement } from "react";
import type { RomMetadata } from "../../types";
import { metadataRows, type MetadataRow } from "../../utils/metadataRows";

interface AboutDetailsProps {
  title: string;
  platformName: string;
  metadata: RomMetadata | null;
  /** The cover's URLs, tried in order; the platform's name stands in once none loads. */
  covers: string[];
}

const CONTAINER_STYLE: CSSProperties = {
  display: "flex",
  gap: "24px",
};

const COVER_CONTAINER_STYLE: CSSProperties = {
  width: "160px",
  minWidth: "160px",
  height: "240px",
  borderRadius: "2px",
  overflow: "hidden",
  backgroundColor: "rgba(0, 0, 0, 0.4)",
  boxShadow: "0 2px 10px rgba(0, 0, 0, 0.5)",
  border: "1px solid rgba(255, 255, 255, 0.06)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const COVER_IMAGE_STYLE: CSSProperties = {
  width: "100%",
  height: "100%",
  objectFit: "cover",
};

const COVER_PLACEHOLDER_STYLE: CSSProperties = {
  padding: "12px",
  textAlign: "center",
  fontSize: "12px",
  color: "#6b7a8a",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
};

const INFO_CONTAINER_STYLE: CSSProperties = {
  flex: 1,
  minWidth: 0,
};

const TITLE_STYLE: CSSProperties = {
  margin: "0 0 12px 0",
  fontSize: "24px",
  fontWeight: 700,
  color: "#ffffff",
  letterSpacing: "-0.01em",
};

const SUMMARY_STYLE: CSSProperties = {
  margin: "0 0 20px 0",
  fontSize: "14px",
  lineHeight: 1.6,
  color: "#a0b0c0",
  maxWidth: "800px",
};

const GRID_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
  gap: "10px 24px",
  fontSize: "13px",
  borderTop: "1px solid rgba(255, 255, 255, 0.08)",
  paddingTop: "16px",
};

const ROW_STYLE: CSSProperties = {
  display: "flex",
  gap: "12px",
  alignItems: "center",
};

const LABEL_STYLE: CSSProperties = {
  color: "#6e7f91",
  minWidth: "90px",
};

const VALUE_STYLE: CSSProperties = {
  color: "#dfe3e6",
};

const TAGS_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "6px",
};

const TAG_STYLE: CSSProperties = {
  padding: "2px 8px",
  borderRadius: "3px",
  backgroundColor: "rgba(255, 255, 255, 0.08)",
  color: "#dfe3e6",
  fontSize: "12px",
};

/** The first of *sources* that loads, else the platform's name. */
const Cover: FC<{ sources: string[]; title: string; platformName: string }> = ({ sources, title, platformName }) => {
  const [failed, setFailed] = useState(0);
  const src = sources[failed];
  return (
    <div style={COVER_CONTAINER_STYLE}>
      {src ? (
        <img src={src} alt={title} onError={() => setFailed((n) => n + 1)} style={COVER_IMAGE_STYLE} />
      ) : (
        <div style={COVER_PLACEHOLDER_STYLE}>{platformName}</div>
      )}
    </div>
  );
};

/** A metadata row as this card draws it: a list as tags, anything else as a labelled value. */
function metadataRow(row: MetadataRow): ReactElement {
  return (
    <div key={row.key} style={ROW_STYLE}>
      <span style={LABEL_STYLE}>{row.label}</span>
      {typeof row.value === "string" ? (
        <span style={VALUE_STYLE}>{row.value}</span>
      ) : (
        <div style={TAGS_STYLE}>
          {row.value.map((tag) => (
            <span key={tag} style={TAG_STYLE}>
              {tag}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export const AboutDetails: FC<AboutDetailsProps> = ({ title, platformName, metadata, covers }) => {
  const rows = metadataRows(metadata, platformName);
  return (
    <div style={CONTAINER_STYLE}>
      <Cover key={covers.join("|")} sources={covers} title={title} platformName={platformName} />
      <div style={INFO_CONTAINER_STYLE}>
        {title && <h2 style={TITLE_STYLE}>{title}</h2>}
        {metadata?.summary && <p style={SUMMARY_STYLE}>{metadata.summary}</p>}
        {rows.length > 0 && <div style={GRID_STYLE}>{rows.map(metadataRow)}</div>}
      </div>
    </div>
  );
};
