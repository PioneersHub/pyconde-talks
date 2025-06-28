group "default" {
  targets = ["django", "staticfiles-export"]
}

target "django" {
  context    = ".."
  dockerfile = "docker/Dockerfile"
  platforms  = ["linux/amd64"]
}

target "staticfiles-export" {
  inherits  = ["django"]
  target    = "staticfiles-stage"
  output    = ["type=local,dest=./staticfiles"]
  platforms = ["linux/amd64"]
}
