import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import { Component, Suspense, useEffect, useMemo } from "react";
import type { JSX, ReactNode } from "react";
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import type { SimulationSnapshot } from "../../../../packages/contracts/typescript/src/index";

const RIB_ASSET_URL = "/assets/maritime/horizon-rib.glb";

export interface DemoSceneProps {
  snapshot: SimulationSnapshot;
  trail: Array<{ north: number; east: number }>;
  timeS: number;
  branch: "protected" | "counterfactual";
  cameraMode: "oblique" | "tactical";
  collision: boolean;
  intervention: boolean;
  playing: boolean;
}

function ned(north: number, east: number, height = 0): [number, number, number] {
  return [east, height, -north];
}

function yaw(headingRad: number): number {
  return -headingRad;
}

const oceanVertex = /* glsl */ `
  uniform float uTime;
  uniform bool uMarine;
  uniform int uWaveCount;
  uniform vec4 uWaves[16];
  uniform float uWavePhases[16];
  varying float vHeight;
  varying vec3 vSeaNormal;
  varying vec3 vSeaPosition;
  void main() {
    vec3 p = position;
    float phaseA = p.x * 0.044 + p.y * 0.019 + uTime * 0.34;
    float phaseB = p.x * -0.025 + p.y * 0.058 - uTime * 0.27;
    float phaseC = p.x * 0.076 + p.y * -0.049 + uTime * 0.43;
    float phaseD = p.x * -0.091 + p.y * -0.022 - uTime * 0.51;
    float a = sin(phaseA) * 0.18;
    float b = sin(phaseB) * 0.11;
    float c = sin(phaseC) * 0.055;
    float d = sin(phaseD) * 0.035;
    float height = a + b + c + d;
    float dx = cos(phaseA) * 0.18 * 0.044
      + cos(phaseB) * 0.11 * -0.025
      + cos(phaseC) * 0.055 * 0.076
      + cos(phaseD) * 0.035 * -0.091;
    float dy = cos(phaseA) * 0.18 * 0.019
      + cos(phaseB) * 0.11 * 0.058
      + cos(phaseC) * 0.055 * -0.049
      + cos(phaseD) * 0.035 * -0.022;
    if (uMarine) {
      height = 0.0;
      dx = 0.0;
      dy = 0.0;
      for (int i = 0; i < 16; i++) {
        if (i >= uWaveCount) break;
        vec4 wave = uWaves[i];
        // Plane x=east, y=north; sea height is positive upward.
        float phase = wave.y * p.y + wave.z * p.x - wave.w * uTime + uWavePhases[i];
        height += wave.x * cos(phase);
        dx -= wave.x * wave.z * sin(phase);
        dy -= wave.x * wave.y * sin(phase);
      }
    }
    p.z += height;
    vHeight = height;
    vSeaNormal = normalize(normalMatrix * vec3(-dx, -dy, 1.0));
    vSeaPosition = (modelMatrix * vec4(p, 1.0)).xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const oceanFragment = /* glsl */ `
  uniform float uTime;
  uniform vec3 uDeep;
  uniform vec3 uShallow;
  uniform vec3 uAccent;
  varying float vHeight;
  varying vec3 vSeaNormal;
  varying vec3 vSeaPosition;
  void main() {
    float rippleA = sin(vSeaPosition.x * 0.31 + vSeaPosition.z * 0.18 + uTime * 0.72);
    float rippleB = sin(vSeaPosition.x * -0.21 + vSeaPosition.z * 0.37 - uTime * 0.61);
    vec3 normal = normalize(vSeaNormal + vec3(rippleA * 0.018, 0.0, rippleB * 0.018));
    vec3 viewDirection = normalize(cameraPosition - vSeaPosition);
    vec3 sunDirection = normalize(vec3(-0.42, 0.78, -0.46));
    vec3 halfDirection = normalize(viewDirection + sunDirection);
    float facing = clamp(dot(normal, viewDirection), 0.0, 1.0);
    float fresnel = pow(1.0 - facing, 3.0);
    float specular = pow(max(dot(normal, halfDirection), 0.0), 84.0) * 0.28;
    float crest = smoothstep(0.19, 0.34, vHeight);
    vec3 color = mix(uDeep, uShallow, clamp(0.48 + vHeight * 0.9, 0.0, 1.0));
    color = mix(color, uAccent, crest * 0.12 + fresnel * 0.09);
    color += vec3(0.92, 0.97, 0.98) * specular;
    gl_FragColor = vec4(color, 0.985);
  }
`;

function Ocean({ timeS, branch, snapshot }: Pick<DemoSceneProps, "timeS" | "branch" | "snapshot">) {
  const material = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: oceanVertex,
        fragmentShader: oceanFragment,
        uniforms: {
          uTime: { value: timeS },
          uMarine: { value: false },
          uWaveCount: { value: 0 },
          uWaves: { value: Array.from({ length: 16 }, () => new THREE.Vector4()) },
          uWavePhases: { value: new Float32Array(16) },
          uDeep: { value: new THREE.Color(branch === "protected" ? "#062b3a" : "#102b38") },
          uShallow: { value: new THREE.Color(branch === "protected" ? "#176a7d" : "#356878") },
          uAccent: { value: new THREE.Color(branch === "protected" ? "#b7f5ef" : "#f5c697") },
        },
        transparent: true,
        side: THREE.DoubleSide,
      }),
    [branch],
  );
  useFrame(() => {
    const marine = snapshot.marine_environment;
    material.uniforms.uTime.value = marine ? snapshot.simulation_time_s : timeS;
    material.uniforms.uMarine.value = Boolean(marine);
    const components = marine?.wave_components ?? [];
    material.uniforms.uWaveCount.value = Math.min(components.length, 16);
    components.slice(0, 16).forEach((wave, index) => {
      material.uniforms.uWaves.value[index].set(
        wave.amplitude_m,
        wave.wave_number_per_m * Math.cos(wave.direction_rad),
        wave.wave_number_per_m * Math.sin(wave.direction_rad),
        wave.angular_frequency_rad_s,
      );
      material.uniforms.uWavePhases.value[index] = wave.phase_rad;
    });
  });
  useEffect(() => () => material.dispose(), [material]);
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={-0.02} receiveShadow>
      <planeGeometry args={[560, 560, 112, 112]} />
      <primitive object={material} attach="material" />
    </mesh>
  );
}

function Sky() {
  return (
    <>
      <color attach="background" args={["#87b8c9"]} />
      <fog attach="fog" args={["#98bdc8", 210, 470]} />
      <mesh scale={390}>
        <sphereGeometry args={[1, 32, 18]} />
        <shaderMaterial
          side={THREE.BackSide}
          depthWrite={false}
          vertexShader="varying vec3 p; void main(){p=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}"
          fragmentShader="varying vec3 p; void main(){float h=clamp(normalize(p).y*0.5+0.5,0.0,1.0); vec3 low=vec3(0.66,0.77,0.79); vec3 high=vec3(0.20,0.42,0.58); gl_FragColor=vec4(mix(low,high,pow(h,0.72)),1.0);}"
        />
      </mesh>
      <mesh position={[-128, 112, -205]}>
        <sphereGeometry args={[11, 24, 16]} />
        <meshBasicMaterial color="#fff2ca" toneMapped={false} />
      </mesh>
    </>
  );
}

function CameraDirector({ snapshot, trail, mode }: {
  snapshot: SimulationSnapshot;
  trail: DemoSceneProps["trail"];
  mode: DemoSceneProps["cameraMode"];
}) {
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);
  useEffect(() => {
    const own = snapshot.ownship;
    const ownPosition = new THREE.Vector3(...ned(own.position_ne_m[0], own.position_ne_m[1], 0.8));
    if (mode === "oblique") {
      const contact = snapshot.traffic[0];
      const encounter = contact
        ? new THREE.Vector3(...ned(
            contact.position_ne_m[0] - own.position_ne_m[0],
            contact.position_ne_m[1] - own.position_ne_m[1],
          ))
        : new THREE.Vector3(Math.sin(own.heading_rad), 0, -Math.cos(own.heading_rad));
      const distance = Math.max(encounter.length(), 1);
      const forward = encounter.normalize();
      const right = new THREE.Vector3(-forward.z, 0, forward.x);
      camera.position.copy(ownPosition).addScaledVector(forward, -30).addScaledVector(right, 12);
      camera.position.y += 17;
      camera.up.set(0, 1, 0);
      camera.lookAt(
        ownPosition
          .clone()
          .addScaledVector(forward, Math.min(44, distance * 0.32))
          .setY(1.25),
      );
    } else {
      const points = [
        { north: own.position_ne_m[0], east: own.position_ne_m[1] },
        ...snapshot.traffic.map((vessel) => ({
          north: vessel.position_ne_m[0],
          east: vessel.position_ne_m[1],
        })),
        ...trail,
      ];
      const minNorth = Math.min(...points.map((point) => point.north));
      const maxNorth = Math.max(...points.map((point) => point.north));
      const minEast = Math.min(...points.map((point) => point.east));
      const maxEast = Math.max(...points.map((point) => point.east));
      const center = ned((minNorth + maxNorth) / 2, (minEast + maxEast) / 2);
      const aspect = Math.max(size.width / Math.max(size.height, 1), 1);
      const northSpan = maxNorth - minNorth;
      const eastSpan = maxEast - minEast;
      const padding = Math.max(34, Math.min(46, Math.max(northSpan, eastSpan) * 0.14));
      const verticalSpan = Math.max(82, northSpan + padding * 2, (eastSpan + padding * 2) / aspect);
      camera.position.set(center[0], 245, center[2] + 0.01);
      camera.up.set(0, 0, -1);
      camera.lookAt(center[0], 0, center[2]);
      if (camera instanceof THREE.OrthographicCamera) {
        camera.left = (-verticalSpan * aspect) / 2;
        camera.right = (verticalSpan * aspect) / 2;
        camera.top = verticalSpan / 2;
        camera.bottom = -verticalSpan / 2;
      }
    }
    camera.updateProjectionMatrix();
  }, [camera, mode, size.height, size.width, snapshot, trail]);
  return null;
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
      {failed && <pointLight color="#ff625f" intensity={18} distance={20} position-y={3} />}
    </group>
  );
}

class RibAssetBoundary extends Component<{
  children: ReactNode;
  position: [number, number, number];
  heading: number;
}, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Horizon RIB asset failed to load", error);
  }

  render() {
    if (this.state.failed) {
      return (
        <group position={this.props.position} rotation-y={yaw(this.props.heading)}>
          <FallbackRib failed />
        </group>
      );
    }
    return this.props.children;
  }
}

function LicensedRib({ position, heading, timeS, physicalPose }: {
  position: [number, number, number];
  heading: number;
  timeS: number;
  physicalPose?: { heaveDown: number; roll: number; pitch: number };
}) {
  const gltf = useLoader(GLTFLoader, RIB_ASSET_URL);
  const model = useMemo(() => {
    const clone = gltf.scene.clone(true);
    clone.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.castShadow = true;
        object.receiveShadow = true;
      }
    });
    return clone;
  }, [gltf.scene]);
  const heave = physicalPose ? -physicalPose.heaveDown : Math.sin(timeS * 0.72) * 0.055;
  const pitch = physicalPose?.pitch ?? Math.sin(timeS * 0.51 + 0.8) * 0.012;
  const roll = physicalPose ? -physicalPose.roll : Math.sin(timeS * 0.64) * 0.018;
  return (
    <group position={[position[0], position[1] + heave, position[2]]} rotation-y={yaw(heading)}>
      <group rotation={[pitch, 0, roll]}><primitive object={model} /></group>
    </group>
  );
}

function Ownship({ snapshot, timeS }: Pick<DemoSceneProps, "snapshot" | "timeS">) {
  const own = snapshot.ownship;
  const position = ned(own.position_ne_m[0], own.position_ne_m[1], 0);
  return (
    <RibAssetBoundary position={position} heading={own.heading_rad}>
      <Suspense fallback={<group position={position} rotation-y={yaw(own.heading_rad)}><FallbackRib /></group>}>
        <LicensedRib position={position} heading={own.heading_rad} timeS={timeS}
          physicalPose={snapshot.marine_environment ? {
            heaveDown: own.heave_down_m ?? 0,
            roll: own.attitude_rp_rad?.[0] ?? 0,
            pitch: own.attitude_rp_rad?.[1] ?? 0,
          } : undefined} />
      </Suspense>
    </RibAssetBoundary>
  );
}

function Barge({ position, heading, length, beam, collision }: {
  position: [number, number, number];
  heading: number;
  length: number;
  beam: number;
  collision: boolean;
}) {
  return (
    <group position={position} rotation-y={yaw(heading)}>
      <mesh castShadow receiveShadow position-y={0.55}>
        <boxGeometry args={[beam, 1.1, length]} />
        <meshStandardMaterial color={collision ? "#6b332d" : "#4e5556"} roughness={0.73} metalness={0.38} />
      </mesh>
      <mesh castShadow position={[0, 1.28, length * 0.18]}>
        <boxGeometry args={[beam * 0.58, 1.4, Math.min(4.4, length * 0.24)]} />
        <meshStandardMaterial color="#d2cec0" roughness={0.66} metalness={0.12} />
      </mesh>
      <mesh castShadow position={[0, 2.2, length * 0.18]}>
        <boxGeometry args={[beam * 0.44, 0.48, Math.min(3.7, length * 0.2)]} />
        <meshStandardMaterial color="#23343a" roughness={0.35} metalness={0.24} />
      </mesh>
      {[-1, 1].flatMap((side) => [-0.34, 0, 0.34].map((offset) => (
        <mesh key={`${side}-${offset}`} position={[side * (beam / 2 + 0.12), 0.48, offset * length]} rotation-z={Math.PI / 2}>
          <torusGeometry args={[0.32, 0.1, 8, 16]} />
          <meshStandardMaterial color="#11191b" roughness={0.86} />
        </mesh>
      )))}
      <mesh position={[0, 1.35, -length * 0.27]}>
        <boxGeometry args={[beam * 0.72, 0.2, 0.2]} />
        <meshStandardMaterial color="#e3a842" emissive="#573308" emissiveIntensity={0.35} />
      </mesh>
    </group>
  );
}

function Traffic({ snapshot, collision }: Pick<DemoSceneProps, "snapshot" | "collision">) {
  return (
    <>
      {snapshot.traffic.map((vessel, index) => {
        const position = ned(vessel.position_ne_m[0], vessel.position_ne_m[1], 0);
        return (
          <Barge
            key={vessel.vessel_id}
            position={position}
            heading={vessel.heading_rad}
            length={vessel.hull.length_m}
            beam={vessel.hull.beam_m}
            collision={collision && index === 0}
          />
        );
      })}
    </>
  );
}

function TrailWake({ trail, protectedBranch, playing }: {
  trail: DemoSceneProps["trail"];
  protectedBranch: boolean;
  playing: boolean;
}) {
  const geometry = useMemo(() => {
    const points = trail.slice(-140).map((point) => new THREE.Vector3(...ned(point.north, point.east, 0.43)));
    if (points.length < 2) return null;
    const curve = new THREE.CatmullRomCurve3(points, false, "centripetal");
    return new THREE.TubeGeometry(curve, Math.max(8, points.length * 2), 0.09, 5, false);
  }, [trail]);
  useEffect(() => () => geometry?.dispose(), [geometry]);
  if (!geometry) return null;
  return (
    <mesh geometry={geometry}>
      <meshBasicMaterial
        color={protectedBranch ? "#bff9f3" : "#f4d2a8"}
        transparent
        opacity={playing ? 0.3 : 0.23}
        depthWrite={false}
      />
    </mesh>
  );
}

function SternWake({ snapshot, branch }: Pick<DemoSceneProps, "snapshot" | "branch">) {
  const own = snapshot.ownship;
  const position = ned(own.position_ne_m[0], own.position_ne_m[1], 0.1);
  const strength = THREE.MathUtils.clamp(own.speed_mps / 4.5, 0, 1);
  if (strength < 0.06) return null;
  const color = branch === "protected" ? "#d8ffff" : "#ffe5c5";
  return (
    <group position={position} rotation-y={yaw(own.heading_rad)}>
      {[5.4, 7.2, 9.4, 12].map((astern, index) => {
        const width = 1.55 + index * 0.72;
        return (
          <mesh
            key={astern}
            position={[0, 0, astern]}
            rotation-x={-Math.PI / 2}
            scale={[width, 1 + index * 0.24, 1]}
          >
            <ringGeometry args={[0.62, 0.79, 28, 1, Math.PI * 0.12, Math.PI * 0.76]} />
            <meshBasicMaterial
              color={color}
              transparent
              opacity={strength * (0.2 - index * 0.034)}
              depthWrite={false}
              side={THREE.DoubleSide}
            />
          </mesh>
        );
      })}
    </group>
  );
}

function InterventionMarker({ snapshot, timeS }: Pick<DemoSceneProps, "snapshot" | "timeS">) {
  const own = snapshot.ownship;
  const position = ned(own.position_ne_m[0], own.position_ne_m[1], 0.16);
  const pulse = 1 + Math.sin(timeS * 5.5) * 0.12;
  return (
    <group position={position} scale={pulse}>
      <mesh rotation-x={-Math.PI / 2}>
        <ringGeometry args={[6.6, 7.05, 64]} />
        <meshBasicMaterial color="#63f4e7" transparent opacity={0.74} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <pointLight color="#53e7dc" intensity={22} distance={26} position-y={3} />
    </group>
  );
}

function CollisionMarker({ snapshot, timeS }: Pick<DemoSceneProps, "snapshot" | "timeS">) {
  const own = snapshot.ownship;
  const contact = snapshot.traffic[0];
  const north = contact ? (own.position_ne_m[0] + contact.position_ne_m[0]) / 2 : own.position_ne_m[0];
  const east = contact ? (own.position_ne_m[1] + contact.position_ne_m[1]) / 2 : own.position_ne_m[1];
  const pulse = 1 + Math.sin(timeS * 7) * 0.09;
  return (
    <group position={ned(north, east, 0.2)} scale={pulse}>
      <mesh rotation-x={-Math.PI / 2}>
        <ringGeometry args={[7.2, 8.2, 64]} />
        <meshBasicMaterial color="#ff625f" transparent opacity={0.82} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <pointLight color="#ff625f" intensity={32} distance={34} position-y={3.5} />
    </group>
  );
}

function Scene({ snapshot, trail, timeS, branch, cameraMode, collision, intervention, playing }: DemoSceneProps) {
  return (
    <>
      <Sky />
      <ambientLight intensity={0.72} color="#b9dbe0" />
      <hemisphereLight args={["#d8eef1", "#183841", 1.55]} />
      <directionalLight
        position={[-105, 145, -70]}
        intensity={3.3}
        color="#ffe8bd"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-130}
        shadow-camera-right={130}
        shadow-camera-top={130}
        shadow-camera-bottom={-130}
      />
      <Ocean timeS={timeS} branch={branch} snapshot={snapshot} />
      <TrailWake trail={trail} protectedBranch={branch === "protected"} playing={playing} />
      <SternWake snapshot={snapshot} branch={branch} />
      <Ownship snapshot={snapshot} timeS={timeS} />
      <Traffic snapshot={snapshot} collision={collision} />
      {intervention && <InterventionMarker snapshot={snapshot} timeS={timeS} />}
      {collision && <CollisionMarker snapshot={snapshot} timeS={timeS} />}
      <CameraDirector snapshot={snapshot} trail={trail} mode={cameraMode} />
    </>
  );
}

export function DemoScene(props: DemoSceneProps): JSX.Element {
  const tactical = props.cameraMode === "tactical";
  const branchLabel = props.branch === "protected" ? "Protected branch" : "Counterfactual branch";
  return (
    <div
      aria-label={`${branchLabel} 3D replay at ${props.timeS.toFixed(1)} seconds`}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        minHeight: 280,
        overflow: "hidden",
        borderRadius: 14,
        background: "#092a38",
      }}
    >
      <Canvas
        key={props.cameraMode}
        shadows
        dpr={[1, 1.7]}
        orthographic={tactical}
        camera={tactical
          ? { position: [0, 245, 0.01], near: 0.1, far: 700 }
          : { position: [24, 17, 28], fov: 43, near: 0.1, far: 750 }}
        gl={{ antialias: true, toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.08 }}
      >
        <Scene {...props} />
      </Canvas>
      <div style={{ position: "absolute", top: 12, left: 12, display: "flex", gap: 7, pointerEvents: "none" }}>
        <span style={{ padding: "5px 8px", borderRadius: 999, color: "#e9fbfb", background: "rgba(4, 27, 36, .76)", font: "600 10px/1.2 system-ui", letterSpacing: ".08em", textTransform: "uppercase" }}>
          {branchLabel}
        </span>
        {props.intervention && <span style={{ padding: "5px 8px", borderRadius: 999, color: "#071f24", background: "#63f4e7", font: "700 10px/1.2 system-ui", letterSpacing: ".06em", textTransform: "uppercase" }}>Matched intervention</span>}
        {props.collision && <span style={{ padding: "5px 8px", borderRadius: 999, color: "#fff", background: "#c94142", font: "700 10px/1.2 system-ui", letterSpacing: ".06em", textTransform: "uppercase" }}>Evaluated collision</span>}
      </div>
      <div style={{ position: "absolute", right: 11, bottom: 9, maxWidth: "70%", padding: "5px 7px", borderRadius: 6, color: "rgba(235, 248, 248, .82)", background: "rgba(4, 24, 31, .68)", font: "500 9px/1.3 system-ui", textAlign: "right", pointerEvents: "auto" }}>
        {props.snapshot.marine_environment
          ? `Marine motion: ${props.snapshot.marine_environment.qualification} · assurance unqualified`
          : "Visual sea and vessel motion only"} · RIB “Assault Boat” © tnnv ·{" "}
        <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer" style={{ color: "#b7f5ef" }}>CC BY 4.0</a>
      </div>
    </div>
  );
}
