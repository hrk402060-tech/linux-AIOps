import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { buildPlatformModel, type PlatformDomain } from './loginPlatformModel';

export interface PlatformRenderer {
  select: (domain: PlatformDomain) => void;
  rotate: (direction: -1 | 1) => void;
  zoom: (direction: -1 | 1) => void;
  reset: () => void;
  dispose: () => void;
}

export interface PlatformRendererOptions {
  canvas: HTMLCanvasElement;
  stage: HTMLElement;
  labels: Record<PlatformDomain, HTMLElement>;
  coreLabel: HTMLElement;
  onAvailability: (available: boolean) => void;
}

export function createPlatformRenderer(options: PlatformRendererOptions): PlatformRenderer {
  const { canvas, stage, labels, coreLabel, onAvailability } = options;
  const renderer = new THREE.WebGLRenderer({
    canvas, alpha: true, antialias: true, preserveDrawingBuffer: true,
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NoToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 60);
  const target = new THREE.Vector3(0, -0.12, 0.65);
  const initialScale = 1.06;
  const initialDirection = new THREE.Vector3(0.43, 0.6, 0.67).normalize();
  camera.position.copy(target).addScaledVector(initialDirection, 10);
  const controls = new OrbitControls(camera, canvas);
  controls.target.copy(target);
  controls.enablePan = false;
  controls.enableZoom = false;
  controls.minPolarAngle = 0.72;
  controls.maxPolarAngle = 1.05;
  controls.dampingFactor = 0.12;
  controls.update();
  controls.saveState();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8f9ba9, 1.05));
  const sun = new THREE.DirectionalLight(0xfff9f1, 3.6);
  sun.position.set(-3, 7, 5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 25 });
  sun.shadow.bias = -0.001;
  sun.shadow.normalBias = 0.04;
  sun.shadow.radius = 3;
  scene.add(sun);
  const fill = new THREE.DirectionalLight(0xe0ecff, 1.1);
  fill.position.set(6, 5, -5);
  scene.add(fill);
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(18, 18), new THREE.ShadowMaterial({ opacity: 0.19 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.24;
  ground.receiveShadow = true;
  scene.add(ground);
  const { structure, domains } = buildPlatformModel();
  scene.add(structure);
  const fitPoints: THREE.Vector3[] = [];
  structure.updateMatrixWorld(true);
  structure.traverse((mesh) => {
    if (!(mesh instanceof THREE.Mesh)) return;
    mesh.geometry.computeBoundingBox();
    const box = mesh.geometry.boundingBox!;
    for (const x of [box.min.x, box.max.x]) {
      for (const y of [box.min.y, box.max.y]) {
        for (const z of [box.min.z, box.max.z]) {
          const point = mesh.localToWorld(new THREE.Vector3(x, y, z)).sub(target);
          fitPoints.push(point);
          if (mesh.parent !== structure) fitPoints.push(point.clone().add(new THREE.Vector3(0, 0.28, 0)));
        }
      }
    }
  });
  const projected = new THREE.Vector3();
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const coarsePointer = window.matchMedia('(pointer: coarse)');
  let selected: PlatformDomain | null = null;
  let frame = 0;
  let disposed = false;
  let available = true;
  controls.enableDamping = !reducedMotion.matches;
  controls.enabled = !coarsePointer.matches;
  canvas.style.touchAction = 'pan-y pinch-zoom';

  function project(position: THREE.Vector3, offset = 0): { x: number; y: number } {
    projected.copy(position).project(camera);
    return { x: (projected.x * 0.5 + 0.5) * stage.clientWidth,
      y: (-projected.y * 0.5 + 0.5) * stage.clientHeight + offset };
  }

  function positionLabels(): void {
    const center = project(structure.localToWorld(new THREE.Vector3(0, 0.76, 0)));
    coreLabel.style.left = `${center.x}px`;
    coreLabel.style.top = `${center.y}px`;
    const occupied = [{ left: center.x - coreLabel.offsetWidth / 2 - 5,
      right: center.x + coreLabel.offsetWidth / 2 + 5,
      top: center.y - coreLabel.offsetHeight / 2 - 5,
      bottom: center.y + coreLabel.offsetHeight / 2 + 5 }];
    for (const domain of domains) {
      const element = labels[domain.id];
      const front = domain.id === 'automation' || domain.id === 'governance';
      const anchor = front ? new THREE.Vector3(0, 0.04, 0.64) : new THREE.Vector3(0, 1.25, 0);
      const point = project(domain.group.localToWorld(anchor), front ? 25 : -2);
      const half = element.offsetWidth / 2 + 5;
      const height = element.offsetHeight + 6;
      const x = THREE.MathUtils.clamp(point.x, half, stage.clientWidth - half);
      const desired = THREE.MathUtils.clamp(point.y, height, stage.clientHeight - 4);
      const candidates = [desired];
      for (let y = height; y <= stage.clientHeight - 4; y += 6) candidates.push(y);
      candidates.sort((a, b) => Math.abs(a - desired) - Math.abs(b - desired));
      const fits = (y: number) => occupied.every((rect) => x + half < rect.left
        || x - half > rect.right || y < rect.top || y - height > rect.bottom);
      const y = candidates.find(fits) ?? desired;
      element.style.left = `${x}px`;
      element.style.top = `${y}px`;
      occupied.push({ left: x - half, right: x + half, top: y - height, bottom: y });
    }
  }

  function fittedDistance(direction: THREE.Vector3): number {
    const right = new THREE.Vector3().crossVectors(camera.up, direction).normalize();
    const up = new THREE.Vector3().crossVectors(direction, right);
    const vertical = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * 0.94;
    let distance = 1;
    for (const point of fitPoints) {
      const depth = point.dot(direction);
      distance = Math.max(distance, depth + Math.abs(point.dot(up)) / vertical,
        depth + Math.abs(point.dot(right)) / (vertical * camera.aspect));
    }
    return distance;
  }

  function constrainView(): void {
    const direction = camera.position.clone().sub(target).normalize();
    const minimum = fittedDistance(direction);
    controls.minDistance = minimum * 1.025;
    controls.maxDistance = minimum * 1.65;
    const distance = THREE.MathUtils.clamp(camera.position.distanceTo(target),
      controls.minDistance, controls.maxDistance);
    camera.position.copy(target).addScaledVector(direction, distance);
  }

  function draw(): void {
    if (disposed || !available) return;
    constrainView();
    renderer.render(scene, camera);
    structure.updateMatrixWorld(true);
    positionLabels();
  }

  function animate(): void {
    frame = 0;
    if (disposed || !available) return;
    const strength = reducedMotion.matches ? 1 : 0.2;
    let pending = false;
    for (const domain of domains) {
      const height = domain.id === selected ? 0.4 : 0.16;
      domain.group.position.y += (height - domain.group.position.y) * strength;
      if (Math.abs(height - domain.group.position.y) <= 0.001) domain.group.position.y = height;
      else pending = true;
    }
    pending = controls.update() || pending;
    draw();
    if (pending && !disposed) schedule();
  }

  function schedule(): void {
    if (!frame && !disposed) frame = window.requestAnimationFrame(animate);
  }

  function resize(): void {
    const width = Math.max(1, stage.clientWidth);
    const height = Math.max(1, stage.clientHeight);
    const relativeDistance = controls.minDistance
      ? camera.position.distanceTo(target) / controls.minDistance : initialScale / 1.025;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    const direction = camera.position.clone().sub(target).normalize();
    camera.position.copy(target).addScaledVector(direction,
      fittedDistance(direction) * 1.025 * relativeDistance);
    controls.update();
    renderer.setSize(width, height, false);
    draw();
  }

  function zoom(direction: -1 | 1): void {
    if (!available) return;
    const offset = camera.position.clone().sub(target);
    const distance = THREE.MathUtils.clamp(offset.length() * (direction === 1 ? 0.9 : 1.1),
      controls.minDistance, controls.maxDistance);
    camera.position.copy(target).add(offset.setLength(distance));
    controls.update();
    schedule();
  }
  function wheel(event: WheelEvent): void {
    if (!available || coarsePointer.matches || event.ctrlKey || event.metaKey || !event.deltaY) return;
    event.preventDefault();
    zoom(event.deltaY < 0 ? 1 : -1);
  }
  function focus(event: PointerEvent): void {
    controls.enabled = available && !coarsePointer.matches && event.pointerType !== 'touch';
    if (controls.enabled) canvas.focus({ preventScroll: true });
  }
  function motion(): void { controls.enableDamping = !reducedMotion.matches; schedule(); }
  function lost(event: Event): void {
    event.preventDefault(); available = false; controls.enabled = false; onAvailability(false);
    for (const element of [coreLabel, ...Object.values(labels)]) {
      element.style.removeProperty('left');
      element.style.removeProperty('top');
    }
  }
  function restored(): void {
    available = true; controls.enabled = !coarsePointer.matches; resize(); onAvailability(true);
  }
  const observer = new ResizeObserver(resize);
  observer.observe(stage);
  controls.addEventListener('change', schedule);
  canvas.addEventListener('pointerdown', focus, true);
  canvas.addEventListener('wheel', wheel, { passive: false });
  reducedMotion.addEventListener('change', motion);
  canvas.addEventListener('webglcontextlost', lost);
  canvas.addEventListener('webglcontextrestored', restored);
  resize();
  onAvailability(true);

  return {
    rotate(direction): void {
      if (!available) return;
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(target));
      spherical.theta += direction * 0.15;
      camera.position.copy(target).add(new THREE.Vector3().setFromSpherical(spherical));
      controls.update();
      schedule();
    },
    zoom,
    reset(): void {
      selected = null;
      controls.enableDamping = false;
      controls.reset();
      const distance = fittedDistance(initialDirection);
      controls.minDistance = distance * 1.025;
      controls.maxDistance = distance * 1.65;
      camera.position.copy(target).addScaledVector(initialDirection, distance * initialScale);
      controls.update();
      controls.enableDamping = !reducedMotion.matches;
      for (const domain of domains) {
        domain.group.position.y = 0.16;
        domain.route.material.color.setHex(0xb5c8df);
      }
      schedule();
    },
    select(domain): void {
      selected = domain;
      for (const node of domains) {
        node.route.material.color.setHex(node.id === selected ? node.color : 0xb5c8df);
      }
      schedule();
    },
    dispose(): void {
      disposed = true;
      observer.disconnect();
      window.cancelAnimationFrame(frame);
      controls.removeEventListener('change', schedule);
      controls.dispose();
      canvas.removeEventListener('pointerdown', focus, true);
      canvas.removeEventListener('wheel', wheel);
      reducedMotion.removeEventListener('change', motion);
      canvas.removeEventListener('webglcontextlost', lost);
      canvas.removeEventListener('webglcontextrestored', restored);
      const resources = new Set<THREE.BufferGeometry | THREE.Material>();
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
          resources.add(object.geometry);
          for (const surface of Array.isArray(object.material) ? object.material : [object.material]) {
            resources.add(surface);
          }
        }
      });
      resources.forEach((resource) => resource.dispose());
      sun.shadow.map?.dispose();
      renderer.dispose();
    },
  };
}
