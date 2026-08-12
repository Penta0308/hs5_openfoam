# hs5 Inventor-derived OpenFOAM v14 case

Generated only from `hs5_cfd.ipt`.

## Raw vs Normalized Surface

The raw export provenance is `constant/triSurface/hs5_cfd.obj`, produced by Inventor COM export. It is **not** used as OpenFOAM input. The normalized single OBJ, `constant/triSurface/hs5_cfd.openfoam.obj`, is the only surface consumed by OpenFOAM v14. It is derived from the raw OBJ by replacing RGB-valued material runs with `g` region names.

## Color-Based Region Classification

The normalizer maps RGB colors from the raw OBJ's `usemtl` names into OpenFOAM `g` regions:

  - `(0,92,255)` blue     -> `patch_inlet` (inlet)
  - `(255,64,0)` red-orange -> `patch_heatsource` (heat_source)
  - `(0,180,80)` green    -> `patch_outlet` (outlet)
  - `(160,160,160)` / `(191,191,191)` gray -> `wall`
  - `mrf` group           -> `mrf` (fan_mrf_zone)
  - `master_1` group       -> `aluminum`

OpenFOAM.org v14 reads patch regions from the OBJ `g` field only and ignores `usemtl`/MTL. The classification manifest is recorded as `obj_region_manifest.json`.

Run `bash Allrun` on the remote host after sourcing OpenFOAM v14 or let the script source it.
