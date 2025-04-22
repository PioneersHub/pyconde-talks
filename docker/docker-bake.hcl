group "default" {
    targets = ["django", "staticfiles-export"]
}

target "django" {
    context = ".."
    dockerfile = "docker/Dockerfile"
}

target "staticfiles-export" {
    inherits = ["django"]
    target = "staticfiles-stage"
    output = ["type=local,dest=./staticfiles"]
}
