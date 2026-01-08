# Sidecar container for Kubernetes pod execution.
FROM python:3.11-slim

# Install curl for health checks, util-linux for nsenter, and libcap2-bin for setcap
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    util-linux \
    libcap2-bin \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r -g 1000 appuser && useradd -r -u 1000 -g appuser appuser

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy sidecar code
COPY main.py .

# Create working directory
RUN mkdir -p /mnt/data && chown -R appuser:appuser /mnt/data /app

# Add file capabilities to nsenter binary so non-root users can use it
# This allows the binary to gain these capabilities when executed:
# - cap_sys_ptrace: access /proc/<pid>/ns/ of other processes
# - cap_sys_admin: call setns() to enter namespaces
# - cap_sys_chroot: required for mount namespace operations
# The +eip flags mean: add to Effective, Inheritable, and Permitted sets
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter

# Kubernetes pod spec still requires:
# - shareProcessNamespace: true (so sidecar can see main container's processes)
# - securityContext.capabilities.add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]
#   (to allow the bounding set to include these caps)
# - securityContext.allowPrivilegeEscalation: true
#   (required for file capabilities to be honored)

# Environment defaults
ENV WORKING_DIR=/mnt/data \
    LANGUAGE=python \
    SIDECAR_PORT=8080 \
    MAX_EXECUTION_TIME=120 \
    PYTHONUNBUFFERED=1

# Expose sidecar port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run as non-root user - file capabilities on nsenter allow this user
# to use nsenter with the required privileges
USER appuser

# Run sidecar
CMD ["python", "main.py"]
