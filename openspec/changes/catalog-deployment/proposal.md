## Why

The catalog service runs locally but has no production deployment path. It needs a Helm chart, Terraform infrastructure (Cloud SQL, IAM), integration with AnnotationFrameworkInfoService for datastack URL discovery, and deployment documentation so it can be rolled out to the CAVE GKE cluster alongside existing services.

## What Changes

- Create a Helm chart for the catalog service (deployment, service, ingress, ConfigMap for env vars) following existing cave-helm-charts patterns.
- Add a Terraform module for the catalog's Cloud SQL instance, cloud IAM bindings (GCS credential vending service account), and service account provisioning.
- Register the catalog service URL in AnnotationFrameworkInfoService's datastack configuration so CAVEclient can auto-discover the endpoint.
- Write deployment documentation covering environment variables, required IAM roles, and Helmfile values.

## Capabilities

### New Capabilities

- `catalog-deployment`: Helm chart, Terraform infrastructure, AFIS integration, and deployment documentation for production rollout of the catalog service.

### Modified Capabilities

<!-- None — deployment is infrastructure-only. -->

## Impact

- `submodules/cave-helm-charts/charts/` — new `catalog/` chart directory
- `submodules/terraform-google-cave/` — new module for catalog infrastructure
- `submodules/AnnotationFrameworkInfoService/` — add catalog URL to datastack config schema
- `submodules/CAVEdeployment/` — Helmfile values for catalog
- Documentation: new deployment guide in catalog repo README or separate doc
