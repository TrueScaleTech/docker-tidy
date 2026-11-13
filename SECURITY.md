# Security

## Reporting a vulnerability

Please report it privately, not in a public issue:
<https://github.com/TrueScaleTech/docker-tidy/security/advisories/new>

Include the version (`docker-tidy --version`), what you did, and what you expected. Please
do not include real container, image or volume names: invented ones are enough.

## What docker-tidy does, and does not do

docker-tidy runs the `docker` command and removes containers, images, volumes and
networks that nothing uses. Because it deletes things, its safety rules are part of the
security of the tool:

- It never removes anything that is in use: a running (or paused, restarting) container,
  an image a container was created from, a volume a container mounts, a network a
  container is connected to, or a built-in network. A cleanup plan refuses outright if it
  contains one, and the check is repeated against a fresh scan right before the removal.
- It never passes `--force` or `-f`, and never calls `prune` or `system prune`. Docker's
  own refusal to remove something in use therefore still applies.
- Volumes are never selected by default, and removing one is called out as deleting data.
- Every command is run as a list of arguments and never through a shell, so a container,
  image or volume name cannot be interpreted as a command.

It does **not**:

- make network connections of its own, or send any data anywhere;
- write files, keep settings, or read anything other than `docker`'s output;
- run with elevated privileges, or ask for them. It needs only the access you already
  have to the Docker daemon, which on many systems is itself equivalent to root: use it
  on hosts where you would be willing to run `docker rm` yourself.

## Where data lives

Nowhere. The program stores nothing. Its output contains the names of your containers,
images and volumes, so treat a pasted report like any other description of your
infrastructure.

## Supported versions

Only the latest release receives fixes.
