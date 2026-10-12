FROM python:3.12-slim

# Build/pull images in a trusted preparation step. Test runs themselves use
# --network none and --pull never, so no package installation occurs at runtime.
RUN python -m pip install --no-cache-dir pytest==8.4.2

WORKDIR /workspace
