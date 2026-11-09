import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js';

export type PlatformDomain = 'assets' | 'monitoring' | 'automation' | 'governance';
type Material = THREE.MeshStandardMaterial;
type Point = [number, number, number];

const material = (color: number) => new THREE.MeshStandardMaterial({
  color, roughness: 0.46, metalness: 0.08,
});

function box(parent: THREE.Group, size: Point, position: Point, surface: Material) {
  const radius = Math.min(0.045, Math.min(...size) * 0.16);
  const geometry = Math.min(...size) > 0.055
    ? new RoundedBoxGeometry(...size, 3, radius) : new THREE.BoxGeometry(...size);
  const mesh = new THREE.Mesh(geometry, surface);
  mesh.position.set(...position);
  mesh.castShadow = true;
  mesh.receiveShadow = size[1] <= 0.2;
  parent.add(mesh);
  return mesh;
}

function line(parent: THREE.Group, points: Point[], color: number) {
  const geometry = new THREE.BufferGeometry().setFromPoints(
    points.map((point) => new THREE.Vector3(...point)),
  );
  const route = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color }));
  parent.add(route);
  return route;
}

export function buildPlatformModel() {
  const structure = new THREE.Group();
  const white = material(0xf9fcff);
  const edge = material(0xd5e1ee);
  const blue = material(0x326ae8);
  const pale = material(0xe3ebf4);
  const green = material(0x239a86);
  const amber = material(0xc99b48);
  const coral = material(0xc8766f);
  box(structure, [5.8, 0.18, 4.15], [0, -0.11, 0], edge);
  box(structure, [5.8, 0.12, 4.15], [0, 0.04, 0], white);
  box(structure, [1.44, 0.16, 1.09], [0, 0.23, 0], pale);
  box(structure, [1.25, 0.38, 0.95], [0, 0.5, 0], blue);
  box(structure, [1.2, 0.035, 0.9], [0, 0.705, 0], material(0x4f87f2));
  const definitions: { id: PlatformDomain; x: number; z: number; color: number; surface: Material }[] = [
    { id: 'assets', x: -1.8, z: -0.95, color: 0x326ae8, surface: blue },
    { id: 'monitoring', x: 1.8, z: -0.95, color: 0x239a86, surface: green },
    { id: 'automation', x: -1.8, z: 1.05, color: 0xc99b48, surface: amber },
    { id: 'governance', x: 1.8, z: 1.05, color: 0xc8766f, surface: coral },
  ];
  const domains = definitions.map((definition) => {
    const group = new THREE.Group();
    const { x, z } = definition;
    group.position.set(x, 0.14, z);
    structure.add(group);
    box(group, [1.4, 0.13, 1.01], [0, 0.025, 0], pale);
    box(group, [1.28, 0.1, 0.91], [0, 0.14, 0], white);
    box(group, [0.27, 0.025, 0.045], [-0.39, 0.2, 0.36], definition.surface);
    const route = line(
      structure, [[x, 0.13, z], [x * 0.55, 0.13, z], [x * 0.55, 0.13, 0], [0, 0.13, 0]],
      0xb5c8df,
    );
    return { ...definition, group, route };
  });
  const assets = domains[0].group;
  for (let tower = 0; tower < 3; tower += 1) {
    const x = -0.36 + tower * 0.34;
    box(assets, [0.25, 0.78, 0.48], [x, 0.59, 0], white);
    box(assets, [0.22, 0.018, 0.44], [x, 0.99, 0], blue);
    for (let row = 0; row < 3; row += 1) {
      box(assets, [0.17, 0.052, 0.012], [x, 0.35 + row * 0.16, 0.246], edge);
      box(assets, [0.032, 0.022, 0.016], [x - 0.04, 0.36 + row * 0.16, 0.255], blue);
    }
  }
  const monitoring = domains[1].group;
  box(monitoring, [0.19, 0.18, 0.17], [0, 0.29, 0], pale);
  box(monitoring, [0.96, 0.61, 0.09], [0, 0.64, -0.04], white);
  box(monitoring, [0.84, 0.46, 0.014], [0, 0.66, 0.014], material(0xedf7f5));
  line(monitoring, [
    [-0.34, 0.58, 0.04], [-0.19, 0.58, 0.04], [-0.11, 0.75, 0.04],
    [0.02, 0.48, 0.04], [0.13, 0.67, 0.04], [0.33, 0.67, 0.04],
  ], 0x239a86);
  box(monitoring, [0.4, 0.04, 0.3], [0, 0.21, 0.02], white);
  const automation = domains[2].group;
  for (let step = 0; step < 3; step += 1) {
    const x = -0.34 + step * 0.34;
    const y = 0.38 + step * 0.19;
    box(automation, [0.25, 0.2, 0.42], [x, y, -0.08], white);
    box(automation, [0.25, 0.025, 0.42], [x, y + 0.115, -0.08], amber);
  }
  line(automation, [
    [-0.34, 0.26, 0.22], [-0.34, 0.3, 0.22], [0, 0.3, 0.22],
    [0, 0.48, 0.22], [0.34, 0.48, 0.22], [0.34, 0.67, 0.22],
  ], 0xc99b48);
  const governance = domains[3].group;
  box(governance, [0.64, 0.7, 0.13], [0, 0.56, -0.04], white);
  box(governance, [0.66, 0.055, 0.15], [0, 0.93, -0.04], coral);
  box(governance, [0.27, 0.11, 0.03], [0, 0.92, 0.05], pale);
  for (let row = 0; row < 3; row += 1) {
    box(governance, [0.07, 0.06, 0.014], [-0.18, 0.39 + row * 0.17, 0.034], coral);
    box(governance, [0.25, 0.021, 0.014], [0.055, 0.39 + row * 0.17, 0.034], edge);
  }
  return { structure, domains };
}
