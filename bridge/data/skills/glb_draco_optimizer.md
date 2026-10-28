---
name: GLB Draco Optimizer
description: 3D Web / GLB Draco & Three.js optimization checklist, compression flags, and budget rules
domain: pipeline
safety_tier: READ_ONLY
triggers:
  - /glb_draco
  - glb
  - draco
  - three.js
  - webgl
---
# GLB Draco & Three.js Optimizer

## Overview
Automated checklist and optimization guidelines for preparing 3D assets for WebGL and Three.js runtime delivery using Google Draco geometric compression.

## Optimization Protocol
1. **Geometry Cleanup**:
   - Merge redundant coplanar vertices and remove unreferenced UV channels before compression.
   - Limit high-poly meshes to <= 250,000 triangles for mobile compatibility.
2. **Draco Compression Parameters**:
   - Position quantization: 14 bits (preserves architectural and naval precision).
   - Normal quantization: 10 bits.
   - Texture coordinate (UV) quantization: 12 bits.
   - Generic attributes: 10 bits.
3. **Asset Budget**:
   - Single asset size target: <= 5.0 MB Draco-compressed GLB.
   - Combined scene budget: <= 25.0 MB total.
4. **Three.js Integration**:
   - Ensure `DRACOLoader` is configured with `setDecoderPath('/draco/')` and instantiated before `GLTFLoader`.
