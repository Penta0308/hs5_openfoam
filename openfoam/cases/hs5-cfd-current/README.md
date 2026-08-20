# hs5 Inventor-derived OpenFOAM v14 case

Generated only from `hs5_cfd.ipt`.

## Raw vs Normalized Surface

The canonical profile exports are `constant/triSurface/master.obj`, `mrf.obj`, and `master_1.obj`. The latter two are copied byte-for-byte to `constant/geometry/mrf-zone.obj` and `aluminum-zone.obj` for createZones. Only the normalized master OBJ, `constant/triSurface/hs5_cfd.openfoam.obj`, is consumed by snappyHexMesh. It is derived from `master.obj` by replacing RGB-valued material runs with `g` region names.

## Color-Based Region Classification

The normalizer maps RGB colors from the raw OBJ's `usemtl` names into OpenFOAM `g` regions:

  - `(0,92,255)` blue     -> `patch_inlet` (inlet)
  - `(255,64,0)` red-orange -> `patch_heatsource` (heat_source)
  - `(0,180,80)` green    -> `patch_outlet` (outlet)
  - `(255,0,255)` magenta  -> `patch_blade` (blade_cavity_wall)
  - `(160,160,160)` / `(191,191,191)` gray -> `wall`

OpenFOAM.org v14 reads patch regions from the OBJ `g` field only and ignores `usemtl`/MTL. The classification manifest is recorded as `obj_region_manifest.json`.

## CHT smoke boundary

The rendered `chtMultiRegionFoam` control dictionary is deliberately a bounded smoke setup, not mesh acceptance. Regional checkMesh evidence still includes 42 small-determinant cells and concavity findings; it is not suppressed or relaxed by the CHT templates.

Run `bash Allrun` on the remote host after sourcing OpenFOAM v14 or let the script source it.
