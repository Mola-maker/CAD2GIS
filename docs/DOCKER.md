# CAD2GIS Docker runtime

Build from the repository root using Docker Desktop in Linux-container mode or Docker Engine:

```sh
docker build --build-arg VCS_REF=$(git rev-parse HEAD) -t cad2gis:0.4.0 .
docker run --rm cad2gis:0.4.0
```

The repository-maintained image includes the canonical Python package, GDAL,
LibreDWG, MCP and Web review dependencies. LibreDWG is installed by the existing
checksum-pinned runtime installer. The final stage excludes the compiler and
runs as UID 10001. Drawing files, results and Git history are excluded from the
build context. This is a CAD2GIS project image, not a Docker Official Images certification.

On PowerShell, replace `$(git rev-parse HEAD)` with the commit ID or use
`--build-arg "VCS_REF=$(git rev-parse HEAD)"`.

Mount a writable working directory at `/data` and run the same CLI as on the host:

```sh
docker run --rm --mount type=bind,source=/absolute/project,target=/data cad2gis:0.4.0 inspect /data/input.dwg --json
docker run --rm -i --mount type=bind,source=/absolute/project,target=/data --entrypoint cad2gis-agent-mcp cad2gis:0.4.0
docker run --rm -p 127.0.0.1:8765:8765 --mount type=bind,source=/absolute/project,target=/data cad2gis:0.4.0 review /data/run --host 0.0.0.0 --port 8765
```

Linux bind-mounted directories must grant the container UID access. Keep the
full run/source tree available: copying a delivery GeoPackage alone does not
provide the source evidence needed by the review server. Existing Windows runs
may contain absolute host paths and need rebinding or regeneration inside the
container; mounting a directory does not translate stored paths.

The Linux reader is LibreDWG, not Windows AutoCAD COM. A successful runtime
check does not certify native CAD rendering or independent GCP accuracy. Keep
the existing source-bound validation and CONDITIONAL review gates.

Dependencies follow the ranges in pyproject.toml; build labels identify source
revision but do not imply bit-reproducible dependency resolution. Record the
image ID and `pip freeze` output when archiving a production run.

No registry publication occurs during local build. Registry tags and access
policy are separate from building and testing the local image.

The `CAD2GIS Docker` GitHub Actions workflow builds the image and checks full
runtime readiness and the MCP entrypoint. The image remains on the ephemeral
runner; the workflow does not upload an image archive or publish to a registry.
