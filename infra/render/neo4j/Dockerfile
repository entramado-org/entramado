# Neo4j for Render: stock 5.26 + a seed entrypoint. Render disks are per-service
# and there's no way to stop the DB while restoring from outside, so the restore
# happens INSIDE the container, before neo4j starts: if SEED_VERSION differs from
# the marker on the persistent disk, the store is wiped and re-extracted from the
# R2 tarball. Graph updates (post-Path A) = upload new tarball + bump SEED_VERSION.
FROM neo4j:5.26

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY seed-entrypoint.sh /seed-entrypoint.sh
RUN chmod +x /seed-entrypoint.sh

ENTRYPOINT ["/seed-entrypoint.sh"]
CMD ["neo4j"]
