# Migrating Chappie3D service data to Windows bind directories

Run these commands **manually** in PowerShell from the Chappie3D repository root during a maintenance window. Nothing here deletes the old Docker volumes. Do not run `docker compose down -v`, `docker volume rm`, or overwrite a nonempty destination. Keep the old volumes and the backup until application and database verification succeeds and a separate retention decision is made. Docker Desktop must be running; these commands assume the old Compose project is `chappie3d`, the old containers are `chappie3d-db-1`, `chappie3d-redis-1`, `chappie3d-backend-1`, and `chappie3d-agent-1`, and the default database is `chappie_db` owned by `chappie`. Check `docker ps -a` and `docker volume ls` first; adjust names and credentials for your deployment. Do **not** export the unrelated `chappie_redis_data` volume.

## 1. Freeze writers and export

Ensure enough free disk for two cold snapshots plus the logical dump, and choose a new backup directory outside the data destination. Stop application writers, but keep the old database and Redis up long enough to export a consistent logical dump. Other clients connected to the database must also stop writing.

```powershell
$backup = Join-Path (Get-Location) ('migration-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
docker ps -a --format 'table {{.Names}}\t{{.Status}}'
docker volume inspect 'chappie3d_postgres_data' 'chappie3d_redis_data'
docker stop 'chappie3d-backend-1' 'chappie3d-agent-1'
if ($LASTEXITCODE -ne 0) { throw 'Application writers could not be stopped' }
docker exec 'chappie3d-db-1' pg_dump --format=custom --no-owner --no-acl --username=chappie --dbname=chappie_db --file=/tmp/chappie-db.dump
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL export failed' }
docker exec 'chappie3d-db-1' pg_dumpall --globals-only --username=chappie --file=/tmp/chappie-globals.sql
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL globals export failed' }
docker cp 'chappie3d-db-1:/tmp/chappie-db.dump' (Join-Path $backup 'chappie-db.dump')
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL dump copy failed' }
docker cp 'chappie3d-db-1:/tmp/chappie-globals.sql' (Join-Path $backup 'chappie-globals.sql')
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL globals copy failed' }
# Snapshot exact table counts (including empty tables) while the old database is still running.
# Use the identical query on the restored database below; its names and counts are sorted.
$inventorySql = @'
CREATE TEMP TABLE migration_counts (schema_name text, table_name text, row_count bigint);
DO $$
DECLARE item record;
DECLARE total bigint;
BEGIN
  FOR item IN SELECT n.nspname AS schema_name, c.relname AS table_name
              FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
              WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                AND n.nspname NOT LIKE 'pg_toast%' AND c.relpersistence <> 't'
              ORDER BY n.nspname, c.relname LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I', item.schema_name, item.table_name) INTO total;
    INSERT INTO migration_counts VALUES (item.schema_name, item.table_name, total);
  END LOOP;
END $$;
SELECT schema_name, table_name, row_count FROM migration_counts ORDER BY schema_name, table_name;
'@
$oldCounts = @(docker exec 'chappie3d-db-1' psql -X -v 'ON_ERROR_STOP=1' -qAt -F '|' -U chappie -d chappie_db -c $inventorySql)
if ($LASTEXITCODE -ne 0) { throw 'Old table inventory failed' }
$oldCounts | Set-Content -Encoding UTF8 (Join-Path $backup 'tables-before.txt')
$oldRevision = @(docker exec 'chappie3d-db-1' psql -X -v 'ON_ERROR_STOP=1' -qAt -U chappie -d chappie_db -c 'SELECT version_num FROM alembic_version ORDER BY version_num;')
if ($LASTEXITCODE -ne 0) { throw 'Old Alembic revision query failed; inspect unversioned schema manually' }
$oldRevision | Set-Content -Encoding UTF8 (Join-Path $backup 'alembic-before.txt')
docker exec 'chappie3d-db-1' pg_restore --list /tmp/chappie-db.dump
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL dump archive is invalid' }
$oldRedisCount = @(docker exec 'chappie3d-redis-1' redis-cli --raw DBSIZE)
if ($LASTEXITCODE -ne 0) { throw 'Old Redis key count failed' }
$oldRedisCount | Set-Content -Encoding UTF8 (Join-Path $backup 'redis-before.txt')
docker exec 'chappie3d-redis-1' redis-cli SAVE
if ($LASTEXITCODE -ne 0) { throw 'Redis snapshot failed' }
docker stop 'chappie3d-redis-1' 'chappie3d-db-1'
if ($LASTEXITCODE -ne 0) { throw 'Stopping old database failed; do not copy a live cluster' }
docker cp 'chappie3d-db-1:/var/lib/postgresql/data/.' (Join-Path $backup 'postgres-volume')
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL cold snapshot failed' }
docker cp 'chappie3d-redis-1:/data/.' (Join-Path $backup 'redis-volume')
if ($LASTEXITCODE -ne 0) { throw 'Redis cold snapshot failed' }
Get-FileHash (Join-Path $backup 'chappie-db.dump') -Algorithm SHA256
Get-ChildItem (Join-Path $backup 'postgres-volume')
Get-ChildItem (Join-Path $backup 'redis-volume')
```

If the old database is unversioned, the Alembic query fails: investigate and record this before proceeding, and compare schema/table contents before cutover instead. Check every exit code and the dump size, checksum and snapshot contents. `pg_dumpall --globals-only` exports roles/tablespaces for inspection or recovery on another cluster; the default bind-mounted database initializes its own application role, so **do not** replay globals blindly. The cold PostgreSQL snapshot must never be copied over a running server. Store the backup securely: it contains credentials and private data.

## 2. Prepare fresh bind mounts and restore

The new Compose file declares `./service-data/postgres` and `./service-data/redis` as bind mounts with `create_host_path: false`; specify `POSTGRES_DATA_PATH`/`REDIS_DATA_PATH` in `.env` first if using different host directories. **Abort if either target already exists and is nonempty**. For a database initialized by Docker, restore the logical dump into the new, empty cluster. The Redis directory can be restored directly from the cold snapshot (including `appendonlydir` and `dump.rdb`), or left empty only if its keys have been confirmed disposable. Do not start the backend yet: it can run Alembic/seed before you validate the restore.

```powershell
$pgTarget = Join-Path (Get-Location) 'service-data\postgres'
$redisTarget = Join-Path (Get-Location) 'service-data\redis'
if ((Test-Path $pgTarget) -and @(Get-ChildItem $pgTarget -Force).Count -gt 0) { throw 'PostgreSQL destination is not empty' }
if ((Test-Path $redisTarget) -and @(Get-ChildItem $redisTarget -Force).Count -gt 0) { throw 'Redis destination is not empty' }
New-Item -ItemType Directory -Force -Path $pgTarget, $redisTarget | Out-Null
# Use the existing Redis image to preserve its AOF directory layout and file metadata.
docker run --rm --mount 'type=volume,source=chappie3d_redis_data,target=/from,readonly' --mount "type=bind,source=$redisTarget,target=/to" --entrypoint sh 'redis:7-alpine' -c 'cp -a /from/. /to/'
if ($LASTEXITCODE -ne 0) { throw 'Redis copy failed' }
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose configuration is invalid' }
docker compose up -d db redis
if ($LASTEXITCODE -ne 0) { throw 'New database or Redis did not start' }
docker compose exec -T db pg_isready -U chappie -d chappie_db
if ($LASTEXITCODE -ne 0) { throw 'New database is not ready' }
docker compose cp (Join-Path $backup 'chappie-db.dump') 'db:/tmp/chappie-db.dump'
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL dump transfer failed' }
docker compose exec -T db pg_restore --exit-on-error --no-owner --no-acl -U chappie -d chappie_db /tmp/chappie-db.dump
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL restore failed; do not start backend' }
```

For a nondefault database/user, adapt the role, dump and restore flags consistently. Do not restore into a previously initialized schema: a failed/retried import into a nonempty database needs investigation or a **new empty destination**, never automatic deletion of the current one. The old named volumes remain available for rollback.

## 3. Verify before releasing traffic

Compare **every** table name and exact row count, including `users` and `alembic_version`, and the separately recorded Alembic revision against the restored instance. The query also detects extra or missing tables; table counts do not prove row-level equality, so spot-check data and authentication with known accounts. Check Redis responds and its key count/AOF data matches the old snapshot as applicable. Then start the backend/UI and the host agent (see README) and check HTTP and WebSocket routing. If checks disagree, stop the new services, investigate using the backup and old volumes, and **do not** switch clients. Never assume Redis is the only copy of business history.

```powershell
$newCounts = @(docker compose exec -T db psql -X -v 'ON_ERROR_STOP=1' -qAt -F '|' -U chappie -d chappie_db -c $inventorySql)
if ($LASTEXITCODE -ne 0) { throw 'Restored table inventory failed' }
$newCounts | Set-Content -Encoding UTF8 (Join-Path $backup 'tables-after.txt')
$differences = @(Compare-Object -CaseSensitive (Get-Content (Join-Path $backup 'tables-before.txt')) (Get-Content (Join-Path $backup 'tables-after.txt')))
if ($differences.Count -gt 0) { $differences; throw 'Table names or exact row counts differ; do not start backend' }
$newRevision = @(docker compose exec -T db psql -X -v 'ON_ERROR_STOP=1' -qAt -U chappie -d chappie_db -c 'SELECT version_num FROM alembic_version ORDER BY version_num;')
if ($LASTEXITCODE -ne 0) { throw 'Restored Alembic revision query failed' }
$newRevision | Set-Content -Encoding UTF8 (Join-Path $backup 'alembic-after.txt')
$differences = @(Compare-Object -CaseSensitive (Get-Content (Join-Path $backup 'alembic-before.txt')) (Get-Content (Join-Path $backup 'alembic-after.txt')))
if ($differences.Count -gt 0) { $differences; throw 'Alembic revision differs; do not start backend' }
$newRedisCount = @(docker compose exec -T redis redis-cli --raw DBSIZE)
if ($LASTEXITCODE -ne 0) { throw 'Restored Redis key count failed' }
$newRedisCount | Set-Content -Encoding UTF8 (Join-Path $backup 'redis-after.txt')
$differences = @(Compare-Object -CaseSensitive (Get-Content (Join-Path $backup 'redis-before.txt')) (Get-Content (Join-Path $backup 'redis-after.txt')))
if ($differences.Count -gt 0) { $differences; throw 'Redis key counts differ; investigate before cutover' }
docker compose ps
docker compose up -d --build workspace backend ui
Invoke-RestMethod 'http://localhost:8004/api/backend/health'
Invoke-RestMethod 'http://localhost:3001/api/health'
```

Projects and agent metadata in `./volumes`, `./data` and `./logs` are **not** removed by this procedure. Back them up independently before migrating project storage/history in later steps, and compare project/thread counts and contents before detaching the previous paths. Neither this document nor Compose performs automatic data deletion.