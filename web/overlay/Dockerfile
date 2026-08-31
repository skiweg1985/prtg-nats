# The overlay hub: one WireGuard interface in the host's network namespace.
#
# Nothing in here is specific to this installation - the key and the peer list
# are rendered into the runtime volume and mounted read-only. Rebuilding the
# image therefore never touches a tunnel, and the container can be replaced
# while the probes keep their handshakes.

FROM alpine:3.21

# bash because wg-quick is a bash script, iproute2 because it uses "ip" for
# every address and route it installs.
RUN apk add --no-cache bash iproute2 wireguard-tools

COPY web/overlay/entrypoint.sh /usr/local/bin/prtg-nats-overlay
RUN chmod 0755 /usr/local/bin/prtg-nats-overlay

ENTRYPOINT ["/usr/local/bin/prtg-nats-overlay"]
