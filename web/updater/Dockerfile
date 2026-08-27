# The updater.
#
# The one container that touches the checkout on the host. It exists because
# the API container cannot: that one mounts the runtime volume and the Docker
# socket and nothing else, while the images are built from a checkout it never
# sees.
#
# It also has to outlive the process that starts it. `docker compose up`
# replaces prtg-nats-web-api, so whatever drives that replacement cannot be
# running inside it - see
# docs/architecture/decisions/0007-update-the-stack-from-the-interface.md.
#
# Built by compose.yaml like the other two images, rather than pulled: it
# carries the git and compose versions this installation was tested against,
# and an update is the worst moment to find out that a floating tag moved.

FROM docker:28-cli

# git for the checkout, openssh-client for the deploy key that reaches the
# repository, docker-cli-compose because rebuilding the stack is the whole
# point. The base image ships some of these already; apk is idempotent about
# that, and naming them here is what keeps the file honest about what it needs.
RUN apk add --no-cache bash git openssh-client docker-cli-compose

COPY web/updater/update.sh /usr/local/bin/update.sh
RUN chmod 0755 /usr/local/bin/update.sh

ENTRYPOINT ["/usr/local/bin/update.sh"]

# Compose starts this service once and it exits immediately. The point of that
# run is not the run - it is that `docker compose up -d --build` builds the
# image, so the real update has something to start. The real runs are created
# through the Docker API, without Compose labels, so that recreating the stack
# does not take the container driving it down with it.
CMD ["noop"]
