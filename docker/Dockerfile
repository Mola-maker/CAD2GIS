# syntax=docker/dockerfile:1.7

# ── 1. base ──────────────────────────────────────────────────────────────
FROM mambaorg/micromamba:2.0.5 AS gis
USER root
ENV MAMBA_ROOT_PREFIX=/opt/mamba

# ── 2. gis-runtime ───────────────────────────────────────────────────────
COPY env/environment.yml /app/env/environment.yml
RUN micromamba create -y -n cad2gis -f /app/env/environment.yml \
    && micromamba clean -ay

# ── 3. libredwg（源码编译 + SWIG python 绑定）────────────────────────────
# 构建依赖一次性安装，避免残留在最终层
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf automake libtool gcc g++ make swig python3-dev pkg-config texinfo \
    && rm -rf /var/lib/apt/lists/*
COPY --from=libredwg / /usr/src/libredwg/
WORKDIR /usr/src/libredwg
RUN export PATH=/opt/mamba/envs/cad2gis/bin:$PATH \
    && autoreconf -fi \
    && ./configure --prefix=/usr/local --enable-python \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && printf '%s\n' /usr/local/lib/python3.12/site-packages \
       > /opt/mamba/envs/cad2gis/lib/python3.12/site-packages/libredwg.pth

# ── 4. code ──────────────────────────────────────────────────────────────
COPY pyproject.toml /app/pyproject.toml
COPY --from=src / /app/src/
RUN /opt/mamba/envs/cad2gis/bin/pip install pillow \
    && /opt/mamba/envs/cad2gis/bin/pip install /app \
    && /opt/mamba/envs/cad2gis/bin/python -c "import LibreDWG, PIL; print('LibreDWG OK; Pillow', PIL.__version__)"

# ── 5. config + 6. data ──────────────────────────────────────────────────
COPY --from=baselines / /app/baselines/
COPY --from=raw / /app/raw/

# ── 7. entrypoint ────────────────────────────────────────────────────────
COPY scripts/run_all.sh /usr/local/bin/run_all.sh
RUN chmod 0755 /usr/local/bin/run_all.sh

ENV CAD2GIS_READER_BACKEND=libredwg
ENV PATH=/opt/mamba/envs/cad2gis/bin:$PATH
ENV PROJ_DATA=/opt/mamba/envs/cad2gis/share/proj
ENV GDAL_DATA=/opt/mamba/envs/cad2gis/share/gdal
ENV GDAL_DRIVER_PATH=/opt/mamba/envs/cad2gis/lib/gdalplugins
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/run_all.sh"]
