# Security Documentation

## Overview

The Code Interpreter API implements multiple layers of security to ensure safe code execution and protect against common web application vulnerabilities.

## Authentication

### API Key Authentication

All API endpoints (except health checks and documentation) require authentication using an API key.

#### Providing API Key

The API key can be provided in two ways:

1. **x-api-key header** (recommended):

   ```bash
   curl -H "x-api-key: your-api-key" https://api.example.com/sessions
   ```

2. **Authorization header**:
   ```bash
   curl -H "Authorization: Bearer your-api-key" https://api.example.com/sessions
   ```

#### Configuration

Set the API key in your environment:

```bash
export API_KEY="your-secure-api-key-here"
```

Or in your `.env` file:

```
API_KEY=your-secure-api-key-here
```

**Important**: Use a strong, randomly generated API key in production.

### Rate Limiting

The API implements rate limiting to prevent abuse:

- **Authentication failures**: Max 10 failed attempts per IP per hour
- **API key validation**: Results are cached for 5 minutes to improve performance
- **Request rate limiting**: Additional rate limiting can be configured per endpoint

## Security Middleware

### Security Headers

All responses include security headers:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'` (varies by endpoint)
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`

### Request Validation

- **Content-Type validation**: Only allowed content types are accepted
- **Request size limits**: Configurable maximum request size
- **Input sanitization**: All user inputs are validated and sanitized

### File Security

#### Filename Validation

Uploaded files are validated for:

- **Path traversal prevention**: `../` and `\` characters are blocked
- **Null byte injection**: Null bytes in filenames are rejected
- **File extension whitelist**: Only allowed file extensions are accepted
- **Filename length limits**: Maximum 255 characters
- **Suspicious characters**: Special characters that could be dangerous are blocked

#### Allowed File Extensions

```
.txt, .csv, .json, .xml, .yaml, .yml,
.py, .js, .ts, .go, .java, .c, .cpp, .h, .hpp,
.rs, .php, .rb, .r, .f90, .d,
.md, .rst, .html, .css,
.png, .jpg, .jpeg, .gif, .svg,
.pdf, .doc, .docx, .xls, .xlsx
```

### Code Execution Security

#### Code Validation

Code is analyzed for potentially dangerous patterns:

- **System imports**: `import os`, `import subprocess`, etc.
- **Dangerous functions**: `eval()`, `exec()`, `__import__()`, etc.
- **File operations**: `open()`, `file()`, etc.
- **Input functions**: `input()`, `raw_input()`, etc.

**Note**: Dangerous patterns generate warnings but don't block execution, as the code runs in isolated Kubernetes pods.

#### Pod Isolation

- **Kubernetes pods**: All code runs in isolated Kubernetes pods
- **Resource limits**: Memory and CPU limits enforced via Kubernetes
- **Network isolation**: NetworkPolicy denies all egress by default
- **Security context**: Pods run as non-root (`runAsUser: 65532`)
- **Ephemeral execution**: Pods destroyed immediately after execution

#### Namespace Sharing Security (nsenter)

The sidecar container uses Linux `nsenter` to execute code in the main container's mount namespace. This requires specific pod and image configuration.

**The Problem:**

When the sidecar runs as non-root (UID 65532), `nsenter` fails with:
```
nsenter: reassociate to namespaces failed: Operation not permitted
```

This happens even with the correct Kubernetes `securityContext.capabilities.add` settings.

**Root Cause:**

Linux capabilities for non-root users only populate the *bounding set*, not the *effective/permitted sets*. The bounding set limits what capabilities a process *can* have, but doesn't actually grant them. From the [Linux capabilities(7) man page](https://man7.org/linux/man-pages/man7/capabilities.7.html):

> During an execve(2), the kernel calculates the new capabilities of the process...

For a non-root user, the permitted set after exec is essentially empty unless file capabilities are set on the binary.

**Solution:**

We use **file capabilities** via `setcap` on the `nsenter` binary in the Docker image:

```dockerfile
# In sidecar Dockerfile
RUN apt-get install -y libcap2-bin
RUN setcap 'cap_sys_ptrace,cap_sys_admin,cap_sys_chroot+eip' /usr/bin/nsenter
```

This grants the nsenter binary the ability to gain these capabilities when executed, even by non-root users.

**Why File Capabilities?**

| Approach | Pros | Cons |
|----------|------|------|
| **File capabilities (setcap)** ✅ | Only nsenter gets caps, simple entrypoint, most secure | Requires image rebuild |
| **Run as root (UID 0)** | Simple to implement | Broader attack surface |
| **capsh wrapper entrypoint** | Works without rebuild | Complex entrypoint, all processes get caps |
| **Ambient capabilities** | Clean solution | [Not yet in Kubernetes (KEP-2763)](https://github.com/kubernetes/enhancements/issues/2763) |

**Required Pod Settings:**
```yaml
spec:
  shareProcessNamespace: true    # Containers can see each other's processes
  containers:
  - name: sidecar
    securityContext:
      runAsUser: 65532
      runAsNonRoot: true
      allowPrivilegeEscalation: true  # Required for file capabilities to be honored
      capabilities:
        add: ["SYS_PTRACE", "SYS_ADMIN", "SYS_CHROOT"]  # Must be in bounding set
        drop: ["ALL"]
```

**Security Implications:**

| Setting | Purpose | Risk Mitigation |
|---------|---------|-----------------|
| `shareProcessNamespace` | Allows sidecar to find main container's PID | Only affects containers within the same pod |
| `SYS_PTRACE` | Access `/proc/<pid>/ns/` of other processes | Scoped to pod only, not host |
| `SYS_ADMIN` | Call `setns()` to enter namespaces | Required for namespace entry; scoped to pod |
| `SYS_CHROOT` | Mount namespace operations | Required for `nsenter -m`; scoped to pod |
| `allowPrivilegeEscalation` | Permits file capabilities to elevate process caps | Only nsenter binary can escalate, not arbitrary code |
| `setcap` on nsenter | Grants caps to specific binary only | Other binaries cannot gain these capabilities |

**Why This Is Secure:**

1. **Pod-scoped isolation**: `shareProcessNamespace` only shares PIDs between containers in the same pod, not with other pods or the host.

2. **Namespace entry, not privilege escalation**: The sidecar enters the main container's *mount namespace* only (`nsenter -m`), gaining access to its filesystem but not elevated privileges.

3. **Code runs in main container's context**: User code executes using the main container's isolated filesystem, subject to all the same resource limits and network policies.

4. **No host namespace access**: The capabilities are limited to pod-level process visibility and cannot be used to access host processes or namespaces.

5. **Non-root execution**: Both containers run as non-root (`runAsUser: 65532`). The sidecar uses file capabilities rather than running as root.

6. **Minimal capabilities**: All capabilities are dropped except the three required for `nsenter` to function.

7. **Binary-specific capabilities**: Only the `nsenter` binary has elevated capabilities via `setcap`. Other processes and binaries in the container cannot gain these capabilities.

**Alternatives Considered:**

1. **Running sidecar as root (UID 0)**: Rejected because running as root is generally less secure, even with minimal capabilities. File capabilities provide the same functionality with a smaller attack surface.

2. **Ambient capabilities (Kubernetes KEP-2763)**: This would allow non-root containers to have effective capabilities without `setcap`, but it's not yet available in Kubernetes. When released, this could simplify the approach.

3. **Running code directly in the sidecar**: Rejected because it would require installing all language runtimes in the sidecar (bloated image), lose the clean separation between executor and runtime, and make per-language resource limits harder to enforce.

**References:**

- [Linux capabilities(7) man page](https://man7.org/linux/man-pages/man7/capabilities.7.html) - Explains capability sets and inheritance rules
- [setcap(8) man page](https://man7.org/linux/man-pages/man8/setcap.8.html) - File capabilities documentation
- [Kubernetes KEP-2763: Ambient Capabilities](https://github.com/kubernetes/enhancements/issues/2763) - Future Kubernetes solution (not yet available)
- [Beyond Container Capabilities: Understanding Linux Capability Sets](https://www.utam0k.jp/en/blog/2025/12/14/linux-capability-sets/) - Explains why K8s caps don't work for non-root
- [Kubernetes SecurityContext Capabilities Explained](https://www.golinuxcloud.com/kubernetes-securitycontext-capabilities/) - Overview of capabilities in Kubernetes

#### Pod Hardening (Host Info Protection)

Execution pods are hardened to prevent information leakage about the host infrastructure.
This prevents reconnaissance attacks that could reveal details about your cloud provider,
Kubernetes cluster, or internal network configuration.

**Currently Implemented**:

| Feature | Protection |
|---------|------------|
| Generic hostname | All pods use hostname "sandbox" instead of revealing node info |
| Non-root execution | Pods run as `runAsUser: 65532` with `runAsNonRoot: true` |
| Network policies | Egress denied by default, blocks cloud metadata endpoints |
| Public DNS only | Execution pods use only public DNS (8.8.8.8, 1.1.1.1) |

**Note**: Kernel version (`/proc/version`) and CPU/memory info (`/proc/cpuinfo`, `/proc/meminfo`)
remain accessible because many libraries depend on them. The pod security context and network
policies address the primary concern of revealing cloud provider and internal network details.

### Network Isolation

Execution pods are isolated via Kubernetes NetworkPolicy:

#### Configuration

```yaml
# In helm values.yaml
execution:
  networkPolicy:
    enabled: true      # Enable NetworkPolicy enforcement
    denyEgress: true   # Block all egress (default: true)
```

#### Network Modes

1. **Full Isolation (default)**: `denyEgress: true`
   - Blocks all outbound connections
   - Pods cannot access internet or cluster services
   - Maximum security for untrusted code

2. **Selective Egress**: `denyEgress: false`
   - Allows DNS (UDP 53) and HTTPS (TCP 443/80)
   - Enables package downloads (pip, npm, etc.)
   - Note: Does not block private IP ranges

#### Security Considerations

1. **NetworkPolicy Required**: Your Kubernetes cluster must have a CNI that
   supports NetworkPolicy (Calico, Cilium, etc.).

2. **Default Deny**: All egress is blocked by default for maximum security.

3. **No Inter-Pod Communication**: NetworkPolicy denies all ingress from other pods.

### State Persistence Security

Python state persistence introduces additional security considerations:

#### Serialization Security

- **Serialization inside pods**: State is serialized within the isolated execution pod, not on the API server. The API never unpickles user data.
- **cloudpickle usage**: We use cloudpickle for serialization. While pickle-based formats can execute code during deserialization, this only occurs inside the sandboxed pod.
- **Compression**: State is compressed with lz4 before storage, providing minor obfuscation and reducing attack surface.
- **Base64 encoding**: Final storage uses base64 encoding for safe transport.

#### Storage Security

- **Redis encryption**: Consider enabling Redis TLS in production for encrypted state storage
- **MinIO encryption**: Enable server-side encryption for archived states
- **TTL-based cleanup**: States automatically expire (2 hours in Redis, 7 days in MinIO archives)
- **Size limits**: `STATE_MAX_SIZE_MB` prevents denial-of-service via large states

#### Session Isolation

- **Session binding**: State is bound to `session_id`, not directly accessible by other sessions
- **User scoping**: Sessions are scoped by `user_id` and `entity_id`
- **No cross-session access**: One user's session cannot access another user's state

#### Disabling State Persistence

If state persistence poses unacceptable risk for your use case:

```bash
STATE_PERSISTENCE_ENABLED=false
```

This ensures each execution starts with a clean namespace.

#### Audit Events

State persistence operations are logged:

- State save (size, session_id)
- State load (session_id, source: redis/minio)
- State archive (session_id)
- State size limit exceeded (warning)

## Security Monitoring

### Audit Logging

All security-relevant events are logged:

- **Authentication attempts**: Success and failure
- **File operations**: Upload, download, delete
- **Code execution**: Language, warnings, success/failure
- **Rate limiting**: When limits are exceeded

### Log Format

```json
{
  "event_type": "authentication",
  "success": true,
  "api_key_prefix": "abc123...",
  "client_ip": "192.168.1.100",
  "endpoint": "GET /sessions",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

### Monitoring Endpoints

- **Authentication stats**: Get authentication failure statistics
- **Rate limit status**: Check current rate limit status
- **Security events**: Query recent security events

## Configuration

### Environment Variables

```bash
# API Key (required)
API_KEY=your-secure-api-key

# Resource Limits
MAX_EXECUTION_TIME=30          # seconds
MAX_MEMORY_MB=512             # megabytes
MAX_FILE_SIZE_MB=10           # megabytes per file
MAX_FILES_PER_SESSION=50      # files per session
MAX_OUTPUT_FILES=10           # output files per execution

# Redis for caching and rate limiting
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=optional-password
```

### Security Best Practices

1. **Use strong API keys**: Generate cryptographically secure random keys
2. **Enable HTTPS**: Always use HTTPS in production
3. **Monitor logs**: Regularly review security logs for suspicious activity
4. **Update dependencies**: Keep all dependencies up to date
5. **Network isolation**: Deploy in a private network when possible
6. **Resource monitoring**: Monitor resource usage and set appropriate limits

## Incident Response

### Authentication Failures

If you see repeated authentication failures:

1. Check the source IP in logs
2. Verify the API key is correct
3. Consider blocking suspicious IPs at the network level
4. Rotate API keys if compromise is suspected

### Suspicious Code Execution

If dangerous code patterns are detected:

1. Review the code content in logs
2. Check the session and user context
3. Consider additional code validation rules
4. Monitor pod resource usage via Kubernetes metrics

### File Upload Issues

For suspicious file uploads:

1. Check filename validation logs
2. Review file content if necessary
3. Verify file size and type restrictions
4. Monitor storage usage

## Security Updates

This security documentation should be reviewed and updated regularly as new threats emerge and security measures are enhanced.
