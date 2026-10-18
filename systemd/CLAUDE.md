# Staged instance units

The project-sync service and daily timer are staged here for the parent to move
into gp-core's systemd directory. Host paths and the root user belong to that
instance, following its existing compute and gsync units. Framework bootstrap
does not install or enable these units.

The service runs `g-projects sync --apply` on h: it appends to the manifest,
commits with the instance's rendering hook and pushes. The timer persists missed
daily runs and spreads them over an hour, like genv-sync.
