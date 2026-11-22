# Apple container-machine base image (Ubuntu 24.04 + systemd), per
# apple/container docs/container-machine.md. Used as the host VM that runs
# docker-ce + docker-compose for the entramado stack (dev `sm-dev` AND, since
# the 2026-06-24 cutover, prod `sm-prod` — same image, see prod-machine.sh).
FROM ubuntu:24.04

ENV container container

RUN apt-get update && \
    apt-get install -y \
    dbus systemd openssh-server net-tools iproute2 iputils-ping curl wget vim-tiny man sudo && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    yes | unminimize

# lazydocker — TUI to inspect the in-machine docker engine (`./prod-machine.sh top`
# or `./dev-machine.sh` equivalent). Apple container machines are always arm64.
ARG LAZYDOCKER_VERSION=0.25.2
RUN curl -fsSL "https://github.com/jesseduffield/lazydocker/releases/download/v${LAZYDOCKER_VERSION}/lazydocker_${LAZYDOCKER_VERSION}_Linux_arm64.tar.gz" \
      | tar xz -C /usr/local/bin lazydocker

RUN >/etc/machine-id
RUN >/var/lib/dbus/machine-id

RUN systemctl set-default multi-user.target
RUN systemctl mask \
      dev-hugepages.mount \
      sys-fs-fuse-connections.mount \
      systemd-update-utmp.service \
      systemd-tmpfiles-setup.service \
      console-getty.service
RUN systemctl disable \
      networkd-dispatcher.service

RUN sed -i -e 's/^AcceptEnv LANG LC_\*$/#AcceptEnv LANG LC_*/' /etc/ssh/sshd_config
