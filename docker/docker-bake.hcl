# Buildx bake definition for the talks app.
#
# Local build (loads the app image into the daemon, exports static files to ./staticfiles):
#   docker buildx bake --allow=fs.read=..
#
# CI build (pushes both images to a registry, tagged with the git sha):
#   REGISTRY=ghcr.io/<owner> IMAGE_TAG=<sha> docker buildx bake --allow=fs.read=.. \
#     --set '*.output=type=registry'
#
# IMPORTANT: bake does NOT read docker/.env. Its variables come from the real environment only, so
# for a local build the image name has to be exported or the defaults below apply and the image is
# tagged something compose does not run. IMAGE_NAME and IMAGE_TAG are deliberately spelled the same
# way as in compose.yaml so one export serves both:
#
#   export IMAGE_NAME="$(grep -E '^IMAGE_NAME=' .env | cut -d= -f2-)"
#   export IMAGE_TAG="$(grep -E '^IMAGE_TAG=' .env | cut -d= -f2-)"
#
# See docs/development/docker-local.md.
#
# The image is event-agnostic: the same build serves every deployment target (talks.pycon.de,
# videos.pydata-berlin.org, ...), which differ only by the runtime .env on each server. So CI
# builds one shared image. The two targets share one build (django is the runtime app,
# staticfiles-export is the collected, content-hashed assets nginx serves). Tagging both with the
# same IMAGE_TAG is what guarantees the staticfiles.json manifest baked into the app image matches
# the assets.

# When empty, build for local use (daemon load + local export). When set (CI), push to
# "${REGISTRY}/<image>:${IMAGE_TAG}".
#
# Only the git sha is published: a ":latest" that any deploy of any commit could move (a manual
# run, a rollback tag on an older sha) names nothing in particular, and neither the deploy script
# nor compose on the server ever reads it.
variable "REGISTRY" {
  default = ""
}

variable "LOCAL_PLATFORM" {
  default = "linux/amd64"
}

variable "CI_PLATFORM" {
  default = "linux/amd64"
}

variable "IMAGE_TAG" {
  default = "latest"
}

variable "IMAGE_NAME" {
  default = "event-talks"
}

variable "STATIC_IMAGE" {
  default = "event-talks-static"
}

# Provenance for the OCI labels below, set by CI. REVISION is the full commit (IMAGE_TAG carries
# only the first 12 characters) and VERSION is the human-facing "<version>" half of the deploy tag.
# Empty locally, where neither is known and neither means anything.
variable "REVISION" {
  default = ""
}

variable "VERSION" {
  default = ""
}

group "default" {
  targets = ["django", "staticfiles-export"]
}

target "django" {
  context    = ".."
  dockerfile = "docker/Dockerfile"
  platforms  = REGISTRY != "" ? [CI_PLATFORM] : [LOCAL_PLATFORM]
  tags = REGISTRY != "" ? [
    "${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}",
  ] : ["${IMAGE_NAME}:${IMAGE_TAG}"]
  # "source" is what links the package to this repository on GHCR; the other two say which commit
  # and which deploy tag produced the image, so a pulled image can be traced back without the
  # registry's own metadata. staticfiles-export inherits all three.
  labels = {
    "org.opencontainers.image.source"   = "https://github.com/PioneersHub/pyconde-talks"
    "org.opencontainers.image.revision" = REVISION
    "org.opencontainers.image.version"  = VERSION
  }
}

target "staticfiles-export" {
  inherits = ["django"]
  target   = "staticfiles-stage"
  # Local default: dump the assets into ./staticfiles. CI overrides this to
  # "type=registry" so the assets are pushed as their own (scratch-based) image that
  # the server extracts on deploy.
  output    = ["type=local,dest=./staticfiles"]
  platforms = REGISTRY != "" ? [CI_PLATFORM] : [LOCAL_PLATFORM]
  tags = REGISTRY != "" ? [
    "${REGISTRY}/${STATIC_IMAGE}:${IMAGE_TAG}",
  ] : []
}
