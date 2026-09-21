import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { headingToSceneYaw, nedToScene } from "../lib/coordinates";
import type { CameraMode, ConsolePacket, PathPoint } from "../types";

interface Props {
  packet: ConsolePacket;
  cameraMode: CameraMode;
  selectedContactId: string;
  onSelectContact: (id: string) => void;
  showBranch: boolean;
}

function Ocean() {
  const geometry = useMemo(() => new THREE.PlaneGeometry(360, 300, 72, 60), []);
  const original = useMemo(() => Float32Array.from(geometry.attributes.position.array), [geometry]);
  useFrame(({ clock }) => {
    const positions = geometry.attributes.position;
    const t = clock.elapsedTime;
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

function CameraAim({ mode }: { mode: CameraMode }) {
  const camera = useThree((state) => state.camera);
  useEffect(() => {
    if (mode === "tactical") {
      camera.position.set(0, 180, -45);
      camera.up.set(0, 0, -1);
    } else {
      camera.position.set(67, 50, 42);
      camera.up.set(0, 1, 0);
    }
    camera.lookAt(0, 0, -45);
    camera.updateProjectionMatrix();
  }, [camera, mode]);
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
      {[[-67, -101], [77, -102], [102, 83], [-98, 104]].map(([east, north]) => (
        <group key={`beacon-${east}-${north}`} position={[east, 0, -north]}>
          <mesh position-y={2.2}><cylinderGeometry args={[0.22, 0.3, 4.4, 8]} /><meshStandardMaterial color="#c4ced0" /></mesh>
          <mesh position-y={4.55}><sphereGeometry args={[0.45, 10, 8]} /><meshBasicMaterial color="#f5c35b" /></mesh>
          <pointLight position-y={4.5} intensity={9} distance={28} color="#f3b84d" />
        </group>
      ))}
    </group>
  );
}

function Vessel({ position, heading, ownship = false, selected = false, ghost = false, onClick, scale = 1 }: {
  position: [number, number, number]; heading: number; ownship?: boolean; selected?: boolean; ghost?: boolean; onClick?: () => void; scale?: number;
}) {
  const hull = ownship ? "#17242a" : "#d7ddda";
  const accent = ownship ? "#55e3df" : "#829195";
  return (
    <group position={position} rotation-y={headingToSceneYaw(heading)} scale={scale} onClick={(event) => { event.stopPropagation(); onClick?.(); }}>
      {selected && <pointLight position={[0, 4, 0]} color="#f2bb64" intensity={18} distance={22} />}
      <mesh castShadow scale={[1, 1, 1]}>
        <boxGeometry args={[3, 1.25, 9.6]} />
        <meshStandardMaterial color={hull} metalness={0.18} roughness={0.5} transparent={ghost} opacity={ghost ? 0.28 : 1} emissive={selected ? "#514018" : "#000000"} />
      </mesh>
      <mesh position={[0, 0, -5.4]} rotation-x={-Math.PI / 2} castShadow>
        <coneGeometry args={[2.12, 3.1, 4]} />
        <meshStandardMaterial color={hull} transparent={ghost} opacity={ghost ? 0.28 : 1} />
      </mesh>
      <mesh position={[0, 1.15, 0.5]} castShadow>
        <boxGeometry args={[2.15, 1.25, 3.1]} />
        <meshStandardMaterial color={accent} transparent={ghost} opacity={ghost ? 0.2 : 0.92} roughness={0.35} />
      </mesh>
      <mesh position={[0, 2.35, 0.3]} castShadow>
        <cylinderGeometry args={[0.08, 0.08, 2.4, 8]} />
        <meshStandardMaterial color="#c7d5d8" transparent={ghost} opacity={ghost ? 0.2 : 1} />
      </mesh>
      {ownship && <mesh position={[0, 1.95, -1.4]}><boxGeometry args={[2.35, 0.12, 0.35]} /><meshBasicMaterial color="#58e3df" /></mesh>}
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

function SceneContents({ packet, selectedContactId, onSelectContact, showBranch }: Omit<Props, "cameraMode">) {
  const contact = packet.snapshot.traffic[0];
  const own = packet.snapshot.ownship;
  const branchGhost = showBranch && packet.branchPath.length > 0
    ? packet.branchPath[Math.min(3, packet.branchPath.length - 1)]
    : null;
  const selected = contact ? selectedContactId === contact.vessel_id : false;
  const uncertaintyRef = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    if (uncertaintyRef.current) {
      const pulse = 1 + Math.sin(clock.elapsedTime * 2.2) * 0.035;
      uncertaintyRef.current.scale.setScalar(pulse);
    }
  });
  const contactPosition = contact ? nedToScene({ north: contact.position_ne_m[0], east: contact.position_ne_m[1] }, 0.28) : [0, 0, 0] as [number, number, number];
  const uncertaintyRadius = packet.contact.uncertaintyRadiusM;
  return (
    <>
      <color attach="background" args={["#0a2633"]} />
      <fog attach="fog" args={["#0c2c38", 135, 285]} />
      <ambientLight intensity={1.05} color="#a8cfda" />
      <directionalLight position={[60, 90, 25]} intensity={3.2} color="#ffe5ba" castShadow shadow-mapSize={[2048, 2048]} />
      <hemisphereLight args={["#a0d7e5", "#10242b", 1.35]} />
      <Ocean />
      <Harbor />
      {packet.proposedPath.length > 1 && <PathLine points={packet.proposedPath} color="#fb6674" dashed />}
      {packet.acceptedPath.length > 1 && <PathLine points={packet.acceptedPath} color="#4ce1de" />}
      {showBranch && packet.branchPath.length > 1 && <PathLine points={packet.branchPath} color="#f2bb64" dashed opacity={0.72} />}
      <Vessel position={nedToScene({ north: own.position_ne_m[0], east: own.position_ne_m[1] }, 0.42)} heading={own.heading_rad} ownship />
      {contact && <Vessel position={contactPosition} heading={contact.heading_rad} selected={selected} onClick={() => onSelectContact(contact.vessel_id)} scale={1.45} />}
      {contact && uncertaintyRadius !== null && uncertaintyRadius > 0 && <mesh ref={uncertaintyRef} position={[contactPosition[0], 0.08, contactPosition[2]]} rotation-x={-Math.PI / 2}>
        <ringGeometry args={[Math.max(0, uncertaintyRadius - 0.28), uncertaintyRadius, 72]} />
        <meshBasicMaterial color="#f2bb64" transparent opacity={0.56} side={THREE.DoubleSide} />
      </mesh>}
      {packet.snapshot.traffic.slice(1).map((vessel) => (
        <Vessel key={vessel.vessel_id} position={nedToScene({ north: vessel.position_ne_m[0], east: vessel.position_ne_m[1] }, 0.35)} heading={vessel.heading_rad} scale={1.65} />
      ))}
      {branchGhost && <Vessel position={nedToScene(branchGhost, 0.42)} heading={0.08} ownship ghost />}
    </>
  );
}

export function MaritimeScene(props: Props) {
  const isTactical = props.cameraMode === "tactical";
  return (
    <div className="scene-canvas" aria-label="Interactive 3D synthetic harbor scene">
      <Canvas
        key={props.cameraMode}
        shadows
        dpr={[1, 1.6]}
        orthographic={isTactical}
        camera={isTactical ? { position: [0, 180, 0.01], zoom: 4, near: 0.1, far: 500 } : { position: [67, 50, 42], fov: 40, near: 0.1, far: 600 }}
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping }}
      >
        <CameraAim mode={props.cameraMode} />
        <SceneContents {...props} />
      </Canvas>
      <div className="north-indicator" aria-hidden="true"><span>N</span><i /></div>
      <div className="scene-coordinates">NED frame · heading clockwise from north</div>
      {props.packet.snapshot.traffic[0] && <button className="contact-label" type="button" onClick={() => props.onSelectContact(props.packet.contact.contactId)} aria-pressed={props.selectedContactId === props.packet.contact.contactId}>
        <strong>{props.packet.contact.label}</strong><span>{props.packet.contact.rangeM.toFixed(1)} m · {props.packet.contact.status}</span>
      </button>}
    </div>
  );
}
