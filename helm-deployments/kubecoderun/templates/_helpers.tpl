{{/*
Expand the name of the chart.
*/}}
{{- define "kubecoderun.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "kubecoderun.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "kubecoderun.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "kubecoderun.labels" -}}
helm.sh/chart: {{ include "kubecoderun.chart" . }}
{{ include "kubecoderun.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "kubecoderun.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubecoderun.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use for API
*/}}
{{- define "kubecoderun.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "kubecoderun.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Create the name of the executor service account
*/}}
{{- define "kubecoderun.executorServiceAccountName" -}}
{{- if .Values.execution.serviceAccount.create }}
{{- default (printf "%s-executor" (include "kubecoderun.fullname" .)) .Values.execution.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.execution.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Execution namespace
*/}}
{{- define "kubecoderun.executionNamespace" -}}
{{- default .Release.Namespace .Values.execution.namespace }}
{{- end }}

{{/*
Redis URL — honours TLS setting to switch between redis:// and rediss://
*/}}
{{- define "kubecoderun.redisUrl" -}}
{{- if .Values.redis.url }}
{{- .Values.redis.url }}
{{- else if .Values.redis.host }}
{{- $scheme := ternary "rediss" "redis" .Values.redis.tls.enabled }}
{{- if .Values.redis.password }}
{{- printf "%s://:%s@%s:%d/%d" $scheme .Values.redis.password .Values.redis.host (int .Values.redis.port) (int .Values.redis.db) }}
{{- else }}
{{- printf "%s://%s:%d/%d" $scheme .Values.redis.host (int .Values.redis.port) (int .Values.redis.db) }}
{{- end }}
{{- else }}
{{- $scheme := ternary "rediss" "redis" .Values.redis.tls.enabled }}
{{- printf "%s://redis:6379/0" $scheme }}
{{- end }}
{{- end }}

{{/*
Check if Helm-managed secret is needed
Returns true if any of the following conditions are met:
- api.existingSecret is not set (API_KEY will be auto-generated)
- redis.existingSecret is not set (REDIS_URL needs to be generated)
- minio.existingSecret is not set AND minio.useIAM is false (S3 credentials needed)
*/}}
{{- define "kubecoderun.needsHelmSecret" -}}
{{- if or (not .Values.api.existingSecret) (not .Values.redis.existingSecret) (and (not .Values.minio.existingSecret) (not .Values.minio.useIAM)) }}
{{- true }}
{{- end }}
{{- end }}

{{/*
Validate MinIO/S3 configuration
When not using existingSecret or IAM, accessKey and secretKey must be provided.
*/}}
{{- define "kubecoderun.validateMinioConfig" -}}
{{- if and (not .Values.minio.existingSecret) (not .Values.minio.useIAM) }}
{{- if or (not .Values.minio.accessKey) (not .Values.minio.secretKey) }}
{{- fail "minio.accessKey and minio.secretKey are required when not using existingSecret or IAM" -}}
{{- end }}
{{- end }}
{{- end }}
