import { useLoader } from "@react-three/fiber";
import { Component, Suspense, useMemo } from "react";
import type { ReactNode } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import assetRegistry from "../../../../assets/maritime/registry.json";

type Position = [number, number, number];

interface RegisteredAsset {
  asset_id: string;
  served_file: string;
  required_nodes: string[];
}

function registeredAsset(assetId: string): RegisteredAsset {
  const asset = assetRegistry.assets.find((candidate) => candidate.asset_id === assetId);
  if (!asset) throw new Error(`Missing registered maritime asset ${assetId}`);
  return asset;
}

function publicUrl(asset: RegisteredAsset): string {
  const marker = "/public/";
  const index = asset.served_file.indexOf(marker);
  if (index < 0) throw new Error(`Maritime asset is not in the console public tree: ${asset.served_file}`);
  return `/${asset.served_file.slice(index + marker.length)}`;
}

export const MARITIME_ASSETS = {
  rib: registeredAsset("horizon-rib-tnnv-12x3-v1"),
  cargoShip: registeredAsset("sketlux-container-ship-120m-v1"),
  cargoStack: registeredAsset("sketlux-harbor-cargo-stack-v1"),
  oceanBuoy: registeredAsset("polyhaven-ocean-buoy-1k-v1"),
} as const;

export const MARITIME_ASSET_URLS = {
  rib: publicUrl(MARITIME_ASSETS.rib),
  cargoShip: publicUrl(MARITIME_ASSETS.cargoShip),
  cargoStack: publicUrl(MARITIME_ASSETS.cargoStack),
  oceanBuoy: publicUrl(MARITIME_ASSETS.oceanBuoy),
} as const;

class MaritimeAssetBoundary extends Component<{
  children: ReactNode;
  fallback: ReactNode;
  label: string;
}, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error(`${this.props.label} asset failed to load`, error);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

function AssetModel({ url, expectedNodes, shadows = true }: {
  url: string;
  expectedNodes: readonly string[];
  shadows?: boolean;
}) {
  const gltf = useLoader(GLTFLoader, url);
  const model = useMemo(() => {
    for (const node of expectedNodes) {
      if (!gltf.scene.getObjectByName(node)) throw new Error(`${url} is missing registered node ${node}`);
    }
    const clone = gltf.scene.clone(true);
    clone.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.castShadow = shadows;
        object.receiveShadow = shadows;
      }
    });
    return clone;
  }, [expectedNodes, gltf.scene, shadows, url]);
  return <primitive object={model} />;
}

function FallbackRib({ failed = false }: { failed?: boolean }) {
  const color = failed ? "#a93d3c" : "#d79b45";
  return (
    <group>
      <mesh castShadow position-y={0.45}>
        <boxGeometry args={[3, 0.9, 10.5]} />
        <meshStandardMaterial color={color} roughness={0.55} metalness={0.12} />
      </mesh>
      <mesh castShadow position={[0, 0.78, -5.45]} rotation-x={-Math.PI / 2}>
        <coneGeometry args={[1.5, 2.8, 4]} />
        <meshStandardMaterial color={color} roughness={0.55} />
      </mesh>
    </group>
  );
}

function FallbackCargo({ failed = false }: { failed?: boolean }) {
  return (
    <group>
      <mesh castShadow receiveShadow position-y={1.7}>
        <boxGeometry args={[24, 3.4, 112]} />
        <meshStandardMaterial color={failed ? "#86413c" : "#505b5d"} roughness={0.68} metalness={0.25} />
      </mesh>
      <mesh castShadow position={[0, 6.2, 34]}>
        <boxGeometry args={[16, 9, 18]} />
        <meshStandardMaterial color="#d3d0c7" roughness={0.62} />
      </mesh>
    </group>
  );
}

function FallbackStack({ failed = false }: { failed?: boolean }) {
  return (
    <mesh castShadow receiveShadow position-y={2.6}>
      <boxGeometry args={[5.08, 5.2, 24.58]} />
      <meshStandardMaterial color={failed ? "#86413c" : "#8a5b28"} roughness={0.72} />
    </mesh>
  );
}

function FallbackBuoy({ failed = false }: { failed?: boolean }) {
  return (
    <group>
      <mesh position-y={0.35} castShadow>
        <cylinderGeometry args={[0.48, 0.38, 1.15, 12]} />
        <meshStandardMaterial color={failed ? "#86413c" : "#d5a53f"} roughness={0.6} />
      </mesh>
      <mesh position-y={1.25} castShadow>
        <cylinderGeometry args={[0.08, 0.08, 1.1, 8]} />
        <meshStandardMaterial color="#d5dde0" roughness={0.52} />
      </mesh>
    </group>
  );
}

function LoadedAsset({ asset, fallback, errorFallback, shadows = true }: {
  asset: RegisteredAsset;
  fallback: ReactNode;
  errorFallback?: ReactNode;
  shadows?: boolean;
}) {
  const url = publicUrl(asset);
  return (
    <MaritimeAssetBoundary label={asset.asset_id} fallback={errorFallback ?? fallback}>
      <Suspense fallback={fallback}>
        <AssetModel url={url} expectedNodes={asset.required_nodes} shadows={shadows} />
      </Suspense>
    </MaritimeAssetBoundary>
  );
}

export function LicensedRib({ position, heading, timeS = 0, physicalPose }: {
  position: Position;
  heading: number;
  timeS?: number;
  physicalPose?: { heaveDown: number; roll: number; pitch: number };
}) {
  const heave = physicalPose ? -physicalPose.heaveDown : Math.sin(timeS * 0.72) * 0.055;
  const pitch = physicalPose?.pitch ?? Math.sin(timeS * 0.51 + 0.8) * 0.012;
  const roll = physicalPose ? -physicalPose.roll : Math.sin(timeS * 0.64) * 0.018;
  return (
    <group position={[position[0], position[1] + heave, position[2]]} rotation-y={-heading}>
      <group rotation={[pitch, 0, roll]}>
        <LoadedAsset asset={MARITIME_ASSETS.rib} fallback={<FallbackRib />} errorFallback={<FallbackRib failed />} />
      </group>
    </group>
  );
}

export function LicensedCargoShip({ position, heading, lengthM, beamM, collision = false, selected = false, detailed = true, onClick }: {
  position: Position;
  heading: number;
  lengthM: number;
  beamM: number;
  collision?: boolean;
  selected?: boolean;
  detailed?: boolean;
  onClick?: () => void;
}) {
  const lengthScale = THREE.MathUtils.clamp(lengthM / 120, 0.08, 1.5);
  const beamScale = THREE.MathUtils.clamp(beamM / 24, 0.08, 1.5);
  return (
    <group
      position={position}
      rotation-y={-heading}
      onClick={(event) => {
        event.stopPropagation();
        onClick?.();
      }}
    >
      <group scale={[beamScale, lengthScale, lengthScale]}>
        {detailed
          ? <LoadedAsset asset={MARITIME_ASSETS.cargoShip} fallback={<FallbackCargo />} errorFallback={<FallbackCargo failed />} />
          : <FallbackCargo />}
      </group>
      {selected && <pointLight position={[0, 5, 0]} color="#f2bb64" intensity={18} distance={26} />}
      {collision && (
        <>
          <mesh rotation-x={-Math.PI / 2} position-y={0.12}>
            <ringGeometry args={[Math.max(4, beamM * 0.65), Math.max(4.6, beamM * 0.78), 48]} />
            <meshBasicMaterial color="#ff625f" transparent opacity={0.78} side={THREE.DoubleSide} />
          </mesh>
          <pointLight color="#ff625f" intensity={24} distance={32} position-y={4} />
        </>
      )}
    </group>
  );
}

export function LicensedCargoStack({ position, rotationY = 0 }: {
  position: Position;
  rotationY?: number;
}) {
  return (
    <group position={position} rotation-y={rotationY}>
      <LoadedAsset asset={MARITIME_ASSETS.cargoStack} fallback={<FallbackStack />} errorFallback={<FallbackStack failed />} />
    </group>
  );
}

export function LicensedOceanBuoy({ position, rotationY = 0 }: {
  position: Position;
  rotationY?: number;
}) {
  return (
    <group position={position} rotation-y={rotationY}>
      <LoadedAsset asset={MARITIME_ASSETS.oceanBuoy} fallback={<FallbackBuoy />} errorFallback={<FallbackBuoy failed />} shadows={false} />
    </group>
  );
}
