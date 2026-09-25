# The desktop client's jar, built where Docker is rather than where Java is.
#
# This image is not a deployment. Nothing runs the desktop client in a
# container -- it draws a window on somebody's own machine -- so what this
# carries is one file and the means to hand it over. That is why it exists at
# all: the rest of this repository asks for Docker and nothing else, and
# requiring Maven as well, for one component, would make that claim false.
#
# The platform is a build argument because the two machines are different
# ones. OpenJFX publishes its native code under a classifier and picks the
# host's automatically; here the host is a Linux container and the target is
# whatever the user is sitting at, so `launch.sh` works it out from `uname`
# and passes it in. One jar is one platform: the same library file names are
# used on macOS x86-64 and arm64, so a jar carrying both would carry one of
# them twice under one name and load whichever came first. The published tags
# say which -- `v4_5-mac-aarch64`, `v4_5-linux` and so on.
FROM --platform=$BUILDPLATFORM maven:3.9.16-eclipse-temurin-21 AS build

# mac-aarch64, mac, linux, linux-aarch64 or win.
ARG JAVAFX_PLATFORM=linux

WORKDIR /src

# The dependencies first, on their own layer, so editing a source file does
# not re-download JavaFX.
COPY desktop/pom.xml ./pom.xml
RUN mvn -B -q -Djavafx.platform=${JAVAFX_PLATFORM} dependency:go-offline

COPY desktop/src ./src
# Tests are not run here. They need a toolkit this image has no display for,
# and `pytest --run-java` is where a failure means the checkout is wrong
# rather than one machine's Docker.
RUN mvn -B -q -Djavafx.platform=${JAVAFX_PLATFORM} -DskipTests package

# --- what ships -------------------------------------------------------------
# Maven, a JDK and half a gigabyte of dependency cache have no business in a
# published artefact whose entire job is to hold a twelve-megabyte file. This
# stage is *not* pinned to $BUILDPLATFORM: it is the one that runs on the
# user's machine, so it is the one a multi-architecture manifest needs a
# variant of. The jar inside is byte-identical in both, because the builder
# above produced it once.
FROM alpine:3.21

COPY --from=build /src/target/nl2sql-desktop.jar /opt/nl2sql/nl2sql-desktop.jar

LABEL org.opencontainers.image.title="nl2sql-desktop build" \
      org.opencontainers.image.description="Carries the nl2sql desktop client's jar, built for one JavaFX platform." \
      org.opencontainers.image.version="4.5.0" \
      org.opencontainers.image.licenses="Apache-2.0"

# The default command copies the jar to a bind mount, which is the whole of
# how it leaves this image:
#
#     docker compose --profile desktop run --rm desktop
WORKDIR /opt/nl2sql
CMD ["cp", "/opt/nl2sql/nl2sql-desktop.jar", "/out/nl2sql-desktop.jar"]
