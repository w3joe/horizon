import type { ConsolePacket, PathPoint } from "../types";
import { projectedConflict } from "../lib/pathConflict";
import { collisionContact } from "../lib/collision";

const WIDTH = 1000;
const HEIGHT = 620;
const PADDING = 64;

interface Bounds {
  minNorth: number;
  maxNorth: number;
  minEast: number;
  maxEast: number;
}

function boundsFor(packet: ConsolePacket): Bounds {
  const points: PathPoint[] = [
    { north: -170, east: -190 },
    { north: 170, east: 190 },
    ...packet.acceptedPath,
    ...packet.proposedPath,
    ...packet.branchPath,
    { north: packet.snapshot.ownship.position_ne_m[0], east: packet.snapshot.ownship.position_ne_m[1] },
    ...packet.snapshot.traffic.map((contact) => ({ north: contact.position_ne_m[0], east: contact.position_ne_m[1] })),
  ];
  const north = points.map((point) => point.north);
  const east = points.map((point) => point.east);
  const minNorth = Math.min(...north);
  const maxNorth = Math.max(...north);
  const minEast = Math.min(...east);
  const maxEast = Math.max(...east);
  const northMargin = Math.max(20, (maxNorth - minNorth) * .16);
  const eastMargin = Math.max(20, (maxEast - minEast) * .16);
  return { minNorth: minNorth - northMargin, maxNorth: maxNorth + northMargin, minEast: minEast - eastMargin, maxEast: maxEast + eastMargin };
}

function projector(bounds: Bounds) {
  return (north: number, east: number): [number, number] => [
    PADDING + ((east - bounds.minEast) / (bounds.maxEast - bounds.minEast)) * (WIDTH - PADDING * 2),
    HEIGHT - PADDING - ((north - bounds.minNorth) / (bounds.maxNorth - bounds.minNorth)) * (HEIGHT - PADDING * 2),
  ];
}

function pathData(points: PathPoint[], project: ReturnType<typeof projector>) {
  return points.map((point, index) => {
    const [x, y] = project(point.north, point.east);
    return `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function VesselMarker({ north, east, heading, label, selected, project, onSelect }: {
  north: number;
  east: number;
  heading: number;
  label: string;
  selected?: boolean;
  project: ReturnType<typeof projector>;
  onSelect?: () => void;
}) {
  const [x, y] = project(north, east);
  return (
    <g className={`chart-vessel ${selected ? "selected" : ""}`} transform={`translate(${x} ${y}) rotate(${(heading * 180) / Math.PI})`} role={onSelect ? "button" : undefined} tabIndex={onSelect ? 0 : undefined} onClick={onSelect} onKeyDown={(event) => { if (onSelect && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); onSelect(); } }}>
      <circle r={selected ? 21 : 15} />
      <path d="M0 -16 L8 11 L0 7 L-8 11 Z" />
      <text x="17" y="4" transform={`rotate(${(-heading * 180) / Math.PI})`}>{label}</text>
    </g>
  );
}

export function TacticalChart({ packet, selectedContactId, onSelectContact, showBranch, frozen = false }: {
  packet: ConsolePacket;
  selectedContactId: string;
  onSelectContact: (id: string) => void;
  showBranch: boolean;
  frozen?: boolean;
}) {
  const bounds = boundsFor(packet);
  const project = projector(bounds);
  const ownship = packet.snapshot.ownship;
  const primaryContact = packet.snapshot.traffic[0];
  const liveConflict = !packet.fixture && primaryContact
    ? projectedConflict(packet.proposedPath, packet.branchPath, (ownship.hull.length_m + primaryContact.hull.length_m) * 0.5)
    : null;
  const collidedContact = collisionContact(packet.snapshot);
  const collisionPosition = collidedContact ? project(
    (collidedContact.position_ne_m[0] + ownship.position_ne_m[0]) / 2,
    (collidedContact.position_ne_m[1] + ownship.position_ne_m[1]) / 2,
  ) : null;
  return (
    <div className={`tactical-chart ${frozen ? "collision-frozen" : ""}`}>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="Two-dimensional tactical navigation chart">
        <defs><pattern id="tactical-grid" width="62" height="62" patternUnits="userSpaceOnUse"><path d="M62 0H0V62" /></pattern></defs>
        <rect width={WIDTH} height={HEIGHT} className="chart-water" />
        <rect width={WIDTH} height={HEIGHT} fill="url(#tactical-grid)" className="chart-grid" />
        {packet.acceptedPath.length > 1 && <path className="chart-path accepted" d={pathData(packet.acceptedPath, project)} />}
        {packet.proposedPath.length > 1 && <path className="chart-path proposed" d={pathData(packet.proposedPath, project)} />}
        {showBranch && packet.branchPath.length > 1 && <path className="chart-path branch" d={pathData(packet.branchPath, project)} />}
        {liveConflict && (() => { const [x, y] = project(liveConflict.point.north, liveConflict.point.east); return <g className="chart-conflict" transform={`translate(${x} ${y})`}><circle r="14" /><path d="M-8 -8L8 8M8 -8L-8 8" /><text x="19" y="4">PROJECTED CONFLICT</text></g>; })()}
        {collisionPosition && <g className="chart-impact" transform={`translate(${collisionPosition[0]} ${collisionPosition[1]})`}><circle r="12" /><circle r="25" /><path d="M0-34V34M-34 0H34M-24-24L24 24M24-24L-24 24" /><text x="34" y="5">COLLISION</text></g>}
        <VesselMarker north={ownship.position_ne_m[0]} east={ownship.position_ne_m[1]} heading={ownship.heading_rad} label="OWN" project={project} selected />
        {packet.snapshot.traffic.map((contact) => <VesselMarker key={contact.vessel_id} north={contact.position_ne_m[0]} east={contact.position_ne_m[1]} heading={contact.heading_rad} label={contact.vessel_id} selected={contact.vessel_id === selectedContactId} project={project} onSelect={() => onSelectContact(contact.vessel_id)} />)}
        <g className="chart-compass" transform="translate(936 72)"><text textAnchor="middle" y="-24">N</text><path d="M0 -17 L6 7 L0 3 L-6 7 Z" /></g>
        <text className="chart-axis-label" x="22" y="32">LOCAL NED · METRES</text>
      </svg>
      <div className="chart-readout"><span>Sensor data</span><span>Range <strong>{packet.contact.rangeM.toFixed(1)} m</strong></span><span>Bearing <strong>{packet.contact.bearingDeg.toFixed(0)}°</strong></span><span>Age <strong>{packet.contact.ageS?.toFixed(2) ?? "—"} s</strong></span><span>Uncertainty <strong>±{packet.contact.uncertaintyRadiusM?.toFixed(1) ?? "—"} m</strong></span></div>
    </div>
  );
}
