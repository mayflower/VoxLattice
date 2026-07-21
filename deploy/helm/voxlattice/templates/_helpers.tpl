{{/* Chart name, overridable. */}}
{{- define "voxlattice.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified release name. */}}
{{- define "voxlattice.fullname" -}}
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

{{- define "voxlattice.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "voxlattice.labels" -}}
helm.sh/chart: {{ include "voxlattice.chart" . }}
{{ include "voxlattice.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "voxlattice.selectorLabels" -}}
app.kubernetes.io/name: {{ include "voxlattice.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "voxlattice.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "voxlattice.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "voxlattice.headlessServiceName" -}}
{{- printf "%s-headless" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Secret holding the bearer token, whether chart-managed or external. */}}
{{- define "voxlattice.authSecretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- printf "%s-auth" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Secret holding the server certificate and key. Empty when TLS is off. */}}
{{- define "voxlattice.tlsSecretName" -}}
{{- if eq .Values.tls.mode "existingSecret" }}
{{- required "tls.existingSecret is required when tls.mode is existingSecret" .Values.tls.existingSecret }}
{{- else if eq .Values.tls.mode "certManager" }}
{{- printf "%s-server-tls" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "voxlattice.selfSignedIssuerName" -}}
{{- printf "%s-selfsign" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "voxlattice.caIssuerName" -}}
{{- printf "%s-ca" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "voxlattice.caSecretName" -}}
{{- printf "%s-ca-tls" (include "voxlattice.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Cluster-internal DNS name the server certificate is validated against. */}}
{{- define "voxlattice.serviceFQDN" -}}
{{- printf "%s.%s.svc.cluster.local" (include "voxlattice.fullname" .) .Release.Namespace }}
{{- end }}

{{/* SANs covering every in-cluster name a client may dial. */}}
{{- define "voxlattice.certificateDnsNames" -}}
{{- $full := include "voxlattice.fullname" . }}
{{- $ns := .Release.Namespace }}
{{- $names := list $full (printf "%s.%s" $full $ns) (printf "%s.%s.svc" $full $ns) (printf "%s.%s.svc.cluster.local" $full $ns) }}
{{- if .Values.service.headless.enabled }}
{{- $headless := include "voxlattice.headlessServiceName" . }}
{{- $names = concat $names (list $headless (printf "%s.%s" $headless $ns) (printf "%s.%s.svc" $headless $ns) (printf "%s.%s.svc.cluster.local" $headless $ns)) }}
{{- end }}
{{- if and .Values.ingressRoute.enabled .Values.ingressRoute.host }}
{{- $names = append $names .Values.ingressRoute.host }}
{{- end }}
{{- $names = concat $names .Values.tls.certManager.extraDnsNames }}
{{- toYaml (uniq $names) }}
{{- end }}

{{- define "voxlattice.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{/* Grace period must outlast the server's own gRPC drain. */}}
{{- define "voxlattice.terminationGracePeriodSeconds" -}}
{{- if .Values.terminationGracePeriodSeconds }}
{{- .Values.terminationGracePeriodSeconds }}
{{- else }}
{{- add (int (ceil (float64 .Values.server.gracefulShutdownS))) 20 }}
{{- end }}
{{- end }}

{{/* Rejects value combinations the server would fail on at startup. */}}
{{- define "voxlattice.validate" -}}
{{- if not (has .Values.tls.mode (list "certManager" "existingSecret" "insecure")) }}
{{- fail (printf "tls.mode must be certManager, existingSecret or insecure, got %q" .Values.tls.mode) }}
{{- end }}
{{- if and .Values.auth.existingSecret .Values.auth.token }}
{{- fail "set only one of auth.existingSecret or auth.token" }}
{{- end }}
{{- if and (not .Values.auth.existingSecret) (not .Values.auth.token) }}
{{- fail "auth.existingSecret or auth.token is required; the server refuses to start without a bearer token" }}
{{- end }}
{{- if and .Values.auth.token (lt (len .Values.auth.token) 16) }}
{{- fail "auth.token must be at least 16 characters" }}
{{- end }}
{{- if and .Values.ingressRoute.enabled (eq .Values.tls.mode "insecure") }}
{{- fail "ingressRoute.enabled requires TLS on the backend; set tls.mode to certManager or existingSecret" }}
{{- end }}
{{- if and .Values.ingressRoute.enabled (not .Values.ingressRoute.host) }}
{{- fail "ingressRoute.host is required when ingressRoute.enabled is true" }}
{{- end }}
{{- if and .Values.tls.clientCA.enabled (not .Values.tls.clientCA.existingSecret) }}
{{- fail "tls.clientCA.existingSecret is required when tls.clientCA.enabled is true" }}
{{- end }}
{{- if and .Values.tls.clientCA.enabled (eq .Values.tls.mode "insecure") }}
{{- fail "mutual TLS cannot be combined with tls.mode insecure" }}
{{- end }}
{{- if and .Values.gpu.mps.enabled (not .Values.gpu.enabled) }}
{{- fail "gpu.mps.enabled requires gpu.enabled" }}
{{- end }}
{{- end }}
