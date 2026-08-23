# AWS Marketplace Listing Guide

This document outlines the steps to list K8s Telemetry MCP Server on AWS Marketplace.

## Prerequisites

1. **AWS Seller Account** — Register at https://aws.amazon.com/marketplace/management/
2. **AWS ECR Repository** — For hosting the container image
3. **Tax and Banking Information** — Required for receiving payments

## Step 1: Prepare the Container Image

### Build and Tag

```bash
# Build the image
docker build -t k8s-telemetry-mcp:1.0.0 .

# Tag for ECR
docker tag k8s-telemetry-mcp:1.0.0 \
  <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp:1.0.0
```

### Push to ECR

```bash
# Authenticate with ECR
aws ecr get-login-password --region <REGION> | \
  docker login --username AWS --password-stdin \
  <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com

# Create repository (if not exists)
aws ecr create-repository --repository-name k8s-telemetry-mcp

# Push image
docker push <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp:1.0.0
```

## Step 2: Create Product Listing

### Product Information

| Field | Value |
|-------|-------|
| **Product Title** | K8s Telemetry MCP Server |
| **Short Description** | Connect AI assistants to your Kubernetes observability stack |
| **Product Category** | DevOps > Monitoring & Observability |
| **Keywords** | MCP, Kubernetes, Observability, Loki, Prometheus, Bedrock, LLM, AI |

### Long Description

```
K8s Telemetry MCP Server enables AI assistants like Amazon Q, Claude, and Amazon Bedrock to query your Kubernetes cluster's observability data.

KEY FEATURES:
• Query pod logs from Loki with automatic PII/secret redaction
• Get pod metrics from Prometheus (CPU, memory, restarts, network)
• Search and retrieve distributed traces from Tempo
• Enterprise-grade security with least-privilege RBAC
• Network policies to restrict access to observability stack only

USE CASES:
• Developers can ask "Why did the payment pod crash?" without kubectl access
• SREs can investigate incidents faster with AI-assisted log analysis
• Platform teams can provide safe, read-only observability access

SECURITY:
• Automatic sanitization of credit cards, SSNs, API keys, JWTs, and more
• No Kubernetes API access — only HTTP to observability endpoints
• Runs as non-root user with read-only filesystem
• Includes NetworkPolicy for egress restriction

DEPLOYMENT:
• Helm chart included for easy EKS deployment
• Configurable via environment variables
• Works with existing Loki, Prometheus, and Tempo installations
```

### Pricing

| Dimension | Price | Description |
|-----------|-------|-------------|
| Standard | $29/month | Per AWS account, 1 namespace, 100 log lines, 8 core tools |
| Professional | $99/month | Per AWS account, unlimited namespaces, 500 log lines, all 12 tools |
| Enterprise | $299/month | Per AWS account, unlimited namespaces, 5000 log lines, 72h query range, all 12 tools |

## Step 3: Technical Requirements

### Container Requirements

- [x] Image runs as non-root user
- [x] No hardcoded credentials
- [x] Health check endpoint (lightweight `find_spec` check, not full module import)
- [x] Configurable via environment variables
- [x] Minimal base image (python:3.11-slim)
- [x] Periodic metering via `RegisterUsage` (every hour, not just startup)
- [x] `RegisterUsage` response signature verified
- [x] Entitlement tier verified via `GetEntitlements` on startup (authoritative tier overrides env var)
- [x] License check cannot be disabled at runtime (`MCP_LOCAL_DEV` is for dev only)

### Helm Chart Requirements

- [x] Configurable image repository/tag (real ECR URI, no placeholder)
- [x] ServiceAccount with minimal permissions
- [x] SecurityContext with least-privilege
- [x] Resource limits defined
- [x] NetworkPolicy included (port 443 egress for Marketplace Metering + Entitlement Service)
- [x] Tier enforcement (Standard: 1 namespace/100 lines/8 tools, Professional/Enterprise: unlimited)

## Step 4: Submit for Review

1. Go to AWS Marketplace Management Portal
2. Select "Create Product" > "Container Product"
3. Fill in product information
4. Upload container image reference
5. Set pricing dimensions
6. Submit for AWS review (typically 2-5 business days)

## Step 5: Post-Launch

### Monitoring

- Track usage via AWS Marketplace Metering Service
- Monitor customer feedback and reviews
- Set up alerts for support requests

### Updates

```bash
# Build new version
docker build -t k8s-telemetry-mcp:1.1.0 .

# Push to ECR
docker tag k8s-telemetry-mcp:1.1.0 \
  <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp:1.1.0
docker push <AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com/k8s-telemetry-mcp:1.1.0

# Update Marketplace listing with new version
```

## Support Resources

- AWS Marketplace Seller Guide: https://docs.aws.amazon.com/marketplace/latest/userguide/
- Container Product Requirements: https://docs.aws.amazon.com/marketplace/latest/userguide/container-product-getting-started.html
- Pricing Models: https://docs.aws.amazon.com/marketplace/latest/userguide/pricing-container-products.html
- Support Email: kubeopsai@gmail.com
