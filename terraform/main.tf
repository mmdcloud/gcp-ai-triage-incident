# -----------------------------------------------------------------------------------------
# Getting project information
# -----------------------------------------------------------------------------------------
data "google_project" "project" {}

# -----------------------------------------------------------------------------------------
# VPC
# -----------------------------------------------------------------------------------------
module "vpc" {
  source                          = "./modules/vpc"
  vpc_name                        = "vpc"
  delete_default_routes_on_create = false
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  region                          = var.location
  subnets                         = []
  firewall_data                   = []
}

# -----------------------------------------------------------------------------------------
# Serverless VPC Connectors
# -----------------------------------------------------------------------------------------
module "vpc_connectors" {
  source   = "./modules/vpc-connector"
  vpc_name = module.vpc.vpc_name
  serverless_vpc_connectors = [
    {
      name          = "connector"
      ip_cidr_range = "10.8.0.0/28"
      min_instances = 2
      max_instances = 3
      machine_type  = "e2-micro"
    }
  ]
}

# -----------------------------------------------------------------------------------------
# Artifact Registry Configuration
# -----------------------------------------------------------------------------------------
module "artifact_registry" {
  source        = "./modules/artifact-registry"
  location      = var.location
  description   = "Artifact repository"
  repository_id = "cloudrun-service"
}

resource "null_resource" "build_and_push" {
  triggers = {
    always_run = timestamp()
  }
  provisioner "local-exec" {
    command = "bash ${path.cwd}/../src/service/artifact_push.sh cloudrun-service ${var.location} ${var.project_id}"
  }

  depends_on = [
    module.artifact_registry
  ]
}

# -----------------------------------------------------------------------------------------
# Cloud Run Configuration
# -----------------------------------------------------------------------------------------
module "cloud_run_service_account" {
  source        = "./modules/service-account"
  account_id    = "cloud-run-sa"
  display_name  = "Cloud Run Service Account"
  project_id    = data.google_project.project.project_id
  member_prefix = "serviceAccount"
  permissions = [
    "roles/secretmanager.secretAccessor",
    # "roles/storage.admin",
    "roles/storage.objectAdmin",
    "roles/iam.serviceAccountTokenCreator"
  ]
}

module "cloudrun_iam_permissions" {
  source = "./modules/cloud-run-iam"
  members = [
    module.cloudrun_service.name
  ]
}

module "cloudrun_service" {
  source                           = "./modules/cloud-run"
  deletion_protection              = false
  ingress                          = "INGRESS_TRAFFIC_ALL"
  vpc_connector_name               = module.vpc_connectors.vpc_connectors[0].id
  service_account                  = module.cloud_run_service_account.sa_email
  location                         = var.location
  min_instance_count               = 2
  max_instance_count               = 5
  max_instance_request_concurrency = 80
  name                             = "cloudrun-service"
  volumes                          = []
  traffic = [
    {
      traffic_type         = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
      traffic_type_percent = 100
    }
  ]
  containers = [
    {
      env               = []
      volume_mounts     = []
      cpu_idle          = true
      startup_cpu_boost = true
      port              = 8080
      image             = "${var.location}-docker.pkg.dev/${data.google_project.project.project_id}/cloudrun-service/cloudrun-service:latest"
    }
  ]
  depends_on = [null_resource.build_and_push, module.cloud_run_service_account]
}
