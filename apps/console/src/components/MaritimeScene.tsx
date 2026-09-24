import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { type ReactNode, useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { headingToSceneYaw, nedToScene } from "../lib/coordinates";
import { collisionContact, vesselsCollide } from "../lib/collision";
import { projectedConflict } from "../lib/pathConflict";
import type { CameraMode, ConsolePacket, PathPoint } from "../types";
import { LicensedCargoShip, LicensedCargoStack, LicensedOceanBuoy, LicensedRib } from "./MaritimeAssets";

interface Props {
  packet: ConsolePacket;
  cameraMode: CameraMode;
  selectedContactId: string;
  onSelectContact: (id: string) => void;
  showBranch: boolean;
  motionRate?: number;
  frozen?: boolean;
}

const CARGO_STACK_PLACEMENTS: ReadonlyArray<{
  position: [number, number, number];
  rotation: number;
}> = [
  { position: [-96, 0.3, 44], rotation: 0 },
  { position: [-86, 0.3, 68], rotation: Math.PI / 2 },
  { position: [105, 0.28, -51], rotation: Math.PI / 2 },
];

const BUOY_PLACEMENTS: ReadonlyArray<[number, number, number]> = [
  [-54, -0.12, 91],
  [62, -0.12, 88],
  [-58, -0.12, -96],
  [68, -0.12, -91],
];

function Ocean({ frozen = false }: { frozen?: boolean }) {
  const geometry = useMemo(() => new THREE.PlaneGeometry(1800, 1400, 90, 70), []);
  const original = useMemo(() => Float32Array.from(geometry.attributes.position.array), [geometry]);
  useFrame(({ clock }) => {
    const positions = geometry.attributes.position;
    const t = frozen ? 0 : clock.elapsedTime;
    for (let i = 0; i < positions.count; i += 1) {
      const x = original[i * 3];
      const y = original[i * 3 + 1];
      positions.setZ(i, Math.sin(x * 0.09 + t * 0.7) * 0.28 + Math.cos(y * 0.065 - t * 0.5) * 0.2);
    }
    positions.needsUpdate = true;
    geometry.computeVertexNormals();
  });
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={-0.45} geometry={geometry} receiveShadow>
      <meshPhysicalMaterial color="#0d5267" roughness={0.3} metalness={0.08} clearcoat={0.86} clearcoatRoughness={0.22} transparent opacity={0.98} />
    </mesh>
  );
}

function CameraAim({ mode, target }: { mode: CameraMode; target: [number, number, number] }) {
  const camera = useThree((state) => state.camera);
  useEffect(() => {
    if (mode === "tactical") {
      camera.position.set(target[0], 180, target[2] - 45);
      camera.up.set(0, 0, -1);
    } else {
      camera.position.set(target[0] + 67, 50, target[2] + 87);
      camera.up.set(0, 1, 0);
    }
    camera.lookAt(target[0], 0, target[2]);
    camera.updateProjectionMatrix();
  }, [camera, mode]);
  useFrame((_, delta) => {
    const desired = mode === "tactical"
      ? new THREE.Vector3(target[0], 180, target[2] - 45)
      : new THREE.Vector3(target[0] + 67, 50, target[2] + 87);
    camera.position.lerp(desired, 1 - Math.exp(-2.8 * Math.min(delta, 0.1)));
    camera.lookAt(target[0], 0, target[2]);
  });
  return null;
}

function Harbor() {
  const warehousePositions = [[-92, -54], [-73, -54], [-54, -54], [75, 60], [96, 60]];
  return (
    <group>
      <mesh position={[-102, -0.1, -10]} receiveShadow>
        <boxGeometry args={[58, 0.8, 250]} />
        <meshStandardMaterial color="#263b3f" roughness={0.92} />
      </mesh>
      <mesh position={[108, -0.12, -5]} receiveShadow>
        <boxGeometry args={[45, 0.75, 250]} />
        <meshStandardMaterial color="#263b3f" roughness={0.92} />
      </mesh>
      {[-75, -30, 18, 62].map((north) => (
        <mesh key={`west-pier-${north}`} position={[-68, 0.12, -north]} castShadow receiveShadow>
          <boxGeometry args={[42, 0.6, 5]} />
          <meshStandardMaterial color="#8d806d" roughness={0.78} />
        </mesh>
      ))}
      {warehousePositions.map(([east, north], index) => (
        <group key={`warehouse-${index}`} position={[east, 2.3, -north]}>
          <mesh castShadow>
            <boxGeometry args={[12, 4.6, 17]} />
            <meshStandardMaterial color={index % 2 ? "#a8977d" : "#778b89"} roughness={0.8} />
          </mesh>
          <mesh position-y={2.7} rotation-y={Math.PI / 4} castShadow>
            <boxGeometry args={[9.7, 1.3, 9.7]} />
            <meshStandardMaterial color="#554e49" roughness={0.9} />
          </mesh>
        </group>
      ))}
      {[-54, 15, 70].map((north, index) => (
        <group key={`crane-${north}`} position={[-73, 0, -north]}>
          <mesh position-y={8} castShadow>
            <boxGeometry args={[0.8, 16, 0.8]} />
            <meshStandardMaterial color={index === 1 ? "#d6a84a" : "#708087"} metalness={0.5} roughness={0.55} />
          </mesh>
          <mesh position={[5, 14.5, 0]} castShadow>
            <boxGeometry args={[11, 0.65, 0.65]} />
            <meshStandardMaterial color={index === 1 ? "#d6a84a" : "#708087"} metalness={0.5} roughness={0.55} />
          </mesh>
        </group>
      ))}
      {CARGO_STACK_PLACEMENTS.map(({ position, rotation }) => (
        <LicensedCargoStack key={`cargo-${position.join("-")}`} position={position} rotationY={rotation} />
      ))}
      {BUOY_PLACEMENTS.map((position, index) => (
        <LicensedOceanBuoy key={`buoy-${position.join("-")}`} position={position} rotationY={index * 0.7} />
      ))}
    </group>
  );
}

function GhostVessel({ position, heading }: {
  position: [number, number, number]; heading: number;
}) {
  return (
    <group position={position} rotation-y={headingToSceneYaw(heading)}>
      <mesh castShadow scale={[1, 1, 1]}>
        <boxGeometry args={[3, 1.25, 9.6]} />
        <meshStandardMaterial color="#55e3df" metalness={0.18} roughness={0.5} transparent opacity={0.28} />
      </mesh>
      <mesh position={[0, 0, -5.4]} rotation-x={-Math.PI / 2} castShadow>
        <coneGeometry args={[2.12, 3.1, 4]} />
        <meshStandardMaterial color="#55e3df" transparent opacity={0.24} />
      </mesh>
      <mesh position={[0, 1.15, 0.5]} castShadow>
        <boxGeometry args={[2.15, 1.25, 3.1]} />
        <meshStandardMaterial color="#55e3df" transparent opacity={0.2} roughness={0.35} />
      </mesh>
    </group>
  );
}

function PathLine({ points, color, dashed = false, opacity = 1 }: { points: PathPoint[]; color: string; dashed?: boolean; opacity?: number }) {
  const line = useMemo(() => {
    const geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(...nedToScene(point, 0.35))));
    const material = dashed
      ? new THREE.LineDashedMaterial({ color, dashSize: 3.2, gapSize: 2.2, linewidth: 2, transparent: opacity < 1, opacity })
      : new THREE.LineBasicMaterial({ color, transparent: opacity < 1, opacity });
    const result = new THREE.Line(geometry, material);
    result.computeLineDistances();
    return result;
  }, [points, color, dashed, opacity]);
  return <primitive object={line} />;
}

function SmoothPose({ position, heading, speed = 0, motionRate = 1, frozen = false, children }: {
  position: [number, number, number];
  heading: number;
  speed?: number;
  motionRate?: number;
  frozen?: boolean;
  children: ReactNode;
}) {
  const group = useRef<THREE.Group>(null);
  const initialPosition = useRef(position);
  const initialYaw = useRef(headingToSceneYaw(heading));
  const sample = useRef({ position: new THREE.Vector3(...position), heading, receivedAtMs: performance.now() });
  useEffect(() => {
    sample.current = { position: new THREE.Vector3(...position), heading, receivedAtMs: performance.now() };
  }, [heading, position[0], position[1], position[2]]);
  useFrame((_, delta) => {
    if (!group.current) return;
    const currentSample = sample.current;
    const targetYaw = headingToSceneYaw(currentSample.heading);
    const extrapolationS = Math.min(0.3, Math.max(0, (performance.now() - currentSample.receivedAtMs) / 1000));
    const predictedX = currentSample.position.x + Math.sin(currentSample.heading) * speed * extrapolationS * motionRate;
    const predictedZ = currentSample.position.z - Math.cos(currentSample.heading) * speed * extrapolationS * motionRate;
    if (frozen) {
      group.current.position.copy(currentSample.position);
      group.current.rotation.y = targetYaw;
      return;
    }
    const blend = 1 - Math.exp(-14 * Math.min(delta, 0.1));
    group.current.position.x = THREE.MathUtils.lerp(group.current.position.x, predictedX, blend);
    group.current.position.y = THREE.MathUtils.lerp(group.current.position.y, currentSample.position.y, blend);
    group.current.position.z = THREE.MathUtils.lerp(group.current.position.z, predictedZ, blend);
    const yawDelta = Math.atan2(Math.sin(targetYaw - group.current.rotation.y), Math.cos(targetYaw - group.current.rotation.y));
    group.current.rotation.y += yawDelta * blend;
  });
  return <group ref={group} position={initialPosition.current} rotation-y={initialYaw.current}>{children}</group>;
}

function ProjectedConflictMarker({ point }: { point: PathPoint }) {
  return (
    <group position={nedToScene(point, 0.5)}>
      <mesh rotation-x={-Math.PI / 2}>
        <ringGeometry args={[5, 7, 48]} />
        <meshBasicMaterial color="#ff4658" transparent opacity={0.9} side={THREE.DoubleSide} />
      </mesh>
      <mesh rotation-y={Math.PI / 4} position-y={0.3}><boxGeometry args={[12, 0.45, 0.7]} /><meshBasicMaterial color="#ff4658" /></mesh>
      <mesh rotation-y={-Math.PI / 4} position-y={0.3}><boxGeometry args={[12, 0.45, 0.7]} /><meshBasicMaterial color="#ff4658" /></mesh>
      <pointLight color="#ff4658" intensity={30} distance={38} position-y={5} />
    </group>
  );
}

function CollisionBurst({ point, frozen = false }: { point: PathPoint; frozen?: boolean }) {
  const burst = useRef<THREE.Group>(null);
  useFrame(({ clock }) => {
    if (!burst.current) return;
    const pulse = frozen ? 1 : 0.82 + Math.sin(clock.elapsedTime * 9) * 0.18;
    burst.current.scale.setScalar(pulse);
    burst.current.rotation.y = clock.elapsedTime * 0.8;
  });
  return (
    <group ref={burst} position={nedToScene(point, 2.5)}>
      <mesh><sphereGeometry args={[4.8, 20, 14]} /><meshStandardMaterial color="#ffb12f" emissive="#ff3b18" emissiveIntensity={4} transparent opacity={0.82} /></mesh>
      <mesh><sphereGeometry args={[8.5, 16, 12]} /><meshBasicMaterial color="#ff5b35" wireframe transparent opacity={0.5} /></mesh>
      {Array.from({ length: 12 }, (_, index) => {
        const angle = (index / 12) * Math.PI * 2;
        return <mesh key={index} position={[Math.cos(angle) * 8, 1 + (index % 3) * 2.2, Math.sin(angle) * 8]} rotation={[angle, angle * 0.6, 0]}><coneGeometry args={[0.7, 5, 5]} /><meshBasicMaterial color={index % 2 ? "#ffcd57" : "#ff4b35"} /></mesh>;
      })}
      <pointLight color="#ff542e" intensity={85} distance={75} />
    </group>
  );
}

function SceneContents({ packet, selectedContactId, onSelectContact, showBranch, motionRate = 1, frozen }: Omit<Props, "cameraMode">) {
  const contact = packet.snapshot.traffic[0];
  const own = packet.snapshot.ownship;
  const branchGhost = showBranch && packet.branchPath.length > 0
    ? packet.branchPath[Math.min(3, packet.branchPath.length - 1)]
    : null;
  const selected = contact ? selectedContactId === contact.vessel_id : false;
  const uncertaintyRef = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (uncertaintyRef.current) {
      const pulse = frozen ? 1 : 1 + Math.sin(clock.elapsedTime * 2.2) * 0.035;
      uncertaintyRef.current.scale.setScalar(pulse);
    }
  });
  const contactPosition = contact ? nedToScene({ north: contact.position_ne_m[0], east: contact.position_ne_m[1] }, 0.28) : [0, 0, 0] as [number, number, number];
  const uncertaintyRadius = packet.contact.uncertaintyRadiusM;
  const liveConflict = !packet.fixture && contact
    ? projectedConflict(packet.proposedPath, packet.branchPath, (own.hull.length_m + contact.hull.length_m) * 0.5)
    : null;
  const isCollidingWithOwnship = (vessel: typeof own) => vesselsCollide(own, vessel);
  const collidingVessel = collisionContact(packet.snapshot);
  const collisionPoint = collidingVessel ? {
    north: (own.position_ne_m[0] + collidingVessel.position_ne_m[0]) / 2,
    east: (own.position_ne_m[1] + collidingVessel.position_ne_m[1]) / 2,
  } : null;
  return (
    <>
      <color attach="background" args={["#0a2633"]} />
      <fog attach="fog" args={["#0c2c38", 135, 285]} />
      <ambientLight intensity={1.05} color="#a8cfda" />
      <directionalLight position={[60, 90, 25]} intensity={3.2} color="#ffe5ba" castShadow shadow-mapSize={[2048, 2048]} />
      <hemisphereLight args={["#a0d7e5", "#10242b", 1.35]} />
      <Ocean frozen={frozen} />
      <Harbor />
      {packet.proposedPath.length > 1 && <PathLine points={packet.proposedPath} color="#fb6674" dashed />}
      {packet.acceptedPath.length > 1 && <PathLine points={packet.acceptedPath} color="#4ce1de" />}
      {showBranch && packet.branchPath.length > 1 && <PathLine points={packet.branchPath} color="#f2bb64" dashed opacity={0.72} />}
      {liveConflict && <ProjectedConflictMarker point={liveConflict.point} />}
      <SmoothPose position={nedToScene({ north: own.position_ne_m[0], east: own.position_ne_m[1] }, 0)} heading={own.heading_rad} speed={own.speed_mps} motionRate={motionRate} frozen={frozen}>
        <LicensedRib
          position={[0, 0, 0]}
          heading={0}
          timeS={packet.snapshot.simulation_time_s}
          physicalPose={packet.snapshot.marine_environment ? {
            heaveDown: own.heave_down_m ?? 0,
            roll: own.attitude_rp_rad?.[0] ?? 0,
            pitch: own.attitude_rp_rad?.[1] ?? 0,
          } : undefined}
        />
      </SmoothPose>
      {contact && <SmoothPose position={contactPosition} heading={contact.heading_rad} speed={contact.speed_mps} motionRate={motionRate} frozen={frozen}>
        <LicensedCargoShip
          position={[0, 0, 0]}
          heading={0}
          lengthM={contact.hull.length_m}
          beamM={contact.hull.beam_m}
          collision={isCollidingWithOwnship(contact)}
          selected={selected}
          onClick={() => onSelectContact(contact.vessel_id)}
        />
        {uncertaintyRadius !== null && uncertaintyRadius > 0 && <mesh ref={uncertaintyRef} position={[0, 0.08, 0]} rotation-x={-Math.PI / 2}>
          <ringGeometry args={[Math.max(0, uncertaintyRadius - 0.28), uncertaintyRadius, 72]} />
          <meshBasicMaterial color="#f2bb64" transparent opacity={0.56} side={THREE.DoubleSide} />
        </mesh>}
      </SmoothPose>}
      {packet.snapshot.traffic.slice(1).map((vessel, index) => (
        <SmoothPose key={vessel.vessel_id} position={nedToScene({ north: vessel.position_ne_m[0], east: vessel.position_ne_m[1] }, 0)} heading={vessel.heading_rad} speed={vessel.speed_mps} motionRate={motionRate} frozen={frozen}>
          <LicensedCargoShip
            position={[0, 0, 0]}
            heading={0}
            lengthM={vessel.hull.length_m}
            beamM={vessel.hull.beam_m}
            collision={isCollidingWithOwnship(vessel)}
            detailed={index < 2}
            selected={selectedContactId === vessel.vessel_id}
            onClick={() => onSelectContact(vessel.vessel_id)}
          />
        </SmoothPose>
      ))}
      {collisionPoint && <CollisionBurst point={collisionPoint} frozen={frozen} />}
      {branchGhost && <GhostVessel position={nedToScene(branchGhost, 0.42)} heading={0.08} />}
    </>
  );
}

export function MaritimeScene(props: Props) {
  const isTactical = props.cameraMode === "tactical";
  const ownshipScenePosition = nedToScene({ north: props.packet.snapshot.ownship.position_ne_m[0], east: props.packet.snapshot.ownship.position_ne_m[1] }, 0);
  const internalComms = [...props.packet.observations].reverse().find((item) => item.contract_type === "Observation" && item.input_group === "internal_ship_communications");
  const radarState = props.packet.contact.sourceIds.some((source) => source.toLowerCase().includes("radar")) ? "TRACK" : "NO RETURN";
  const cameraState = props.packet.contact.sourceIds.some((source) => source.toLowerCase().includes("camera")) ? "TRACK" : "DEGRADED";
  return (
    <div className="scene-canvas" aria-label="Interactive Singapore Strait-inspired synthetic harbour scene">
      <Canvas
        key={props.cameraMode}
        shadows
        dpr={[1, 1.6]}
        orthographic={isTactical}
        camera={isTactical ? { position: [0, 180, 0.01], zoom: 4, near: 0.1, far: 500 } : { position: [67, 50, 42], fov: 40, near: 0.1, far: 600 }}
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping }}
        frameloop={props.frozen ? "demand" : "always"}
      >
        <CameraAim mode={props.cameraMode} target={ownshipScenePosition} />
        <SceneContents {...props} />
      </Canvas>
      <div className="north-indicator" aria-hidden="true"><span>N</span><i /></div>
      <div className="scene-coordinates">Singapore Strait-inspired harbour · NED scenario frame</div>
      <div className="platform-fit" aria-label="Decorative defensive maritime platform fit">
        <span>Patrol platform · presentation fit</span>
        <strong>Sensor mast + radome · EO/IR sensor · mission bay/RHIB · static defensive silhouettes</strong>
        <small>Internal comms evidence: {internalComms && "capability" in internalComms ? internalComms.capability : "unavailable"} · silhouettes are non-functional and have no control, targeting, or safety-evidence role</small>
      </div>
      <div className="sensor-hud" aria-label="Selected contact sensor data">
        <header><span>Sensor data</span><strong>{props.packet.contact.label}</strong></header>
        <dl>
          <div><dt>Range</dt><dd>{props.packet.contact.rangeM.toFixed(1)} m</dd></div>
          <div><dt>Bearing</dt><dd>{props.packet.contact.bearingDeg.toFixed(1)}°</dd></div>
          <div><dt>Age</dt><dd>{props.packet.contact.ageS?.toFixed(2) ?? "—"} s</dd></div>
          <div><dt>Uncertainty</dt><dd>±{props.packet.contact.uncertaintyRadiusM?.toFixed(1) ?? "—"} m</dd></div>
          <div><dt>Radar</dt><dd className={radarState === "TRACK" ? "good" : "warn"}>{radarState}</dd></div>
          <div><dt>Camera</dt><dd className={cameraState === "TRACK" ? "good" : "warn"}>{cameraState}</dd></div>
        </dl>
      </div>
      {props.packet.snapshot.traffic[0] && <button className="contact-label" type="button" onClick={() => props.onSelectContact(props.packet.contact.contactId)} aria-pressed={props.selectedContactId === props.packet.contact.contactId}>
        <strong>{props.packet.contact.label}</strong><span>{props.packet.contact.rangeM.toFixed(1)} m · {props.packet.contact.status}</span>
      </button>}
    </div>
  );
}
