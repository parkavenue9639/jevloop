# Immutable sandbox image for isolated agent lanes.
#
# One image (built from a seed root, content-addressed by the host runtime)
# seeds any number of independent containers: /workspace is declared as a
# volume, so every `docker run` copies the seeded content into a private
# anonymous volume — writable even under a read-only root filesystem, and
# never shared between containers.
FROM python:3.12-slim

# bash: the shell primitive (explicit here so the contract does not depend on
# base-image drift); python3 ships with the base image. A non-root user owns
# everything the sandbox may write.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir \
        "fastapi==0.115.0" \
        "uvicorn[standard]==0.30.6" \
        "httpx==0.27.2" \
        "pytest==8.3.3" \
    && groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash sandbox

# File and command primitives driven by the host runtime through `docker exec`.
# Shell source reaches sandboxexec over stdin, so it never appears in `/proc`
# command lines where broad process-name matching could select the caller.
COPY --chown=sandbox:sandbox sandboxfs.py /usr/local/bin/sandboxfs
COPY --chown=sandbox:sandbox sandboxexec.py /usr/local/bin/sandboxexec
RUN chmod 0755 /usr/local/bin/sandboxfs /usr/local/bin/sandboxexec

# Seed /workspace from the build context BEFORE the VOLUME declaration:
# changes to a volume path made after VOLUME are discarded. Each container's
# anonymous volume is initialized from this seeded copy.
COPY --chown=sandbox:sandbox seed/ /workspace/

USER sandbox
WORKDIR /workspace
VOLUME ["/workspace"]
ENV HOME=/home/sandbox \
    PYTHONDONTWRITEBYTECODE=1
CMD ["sleep", "infinity"]
