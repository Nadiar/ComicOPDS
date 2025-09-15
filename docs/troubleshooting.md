## 🛠️ Troubleshooting

> Access logs are disabled in the docker image. Enable them by removing `--no-access-log` from the `CMD` command in the Dockerfile. 

### Common Issues

#### No Comics Appearing
- **Check mount**: Ensure your comics folder is mounted at `/library`
- **File format**: Only `.cbz` files are supported
- **Metadata**: Ensure CBZ files contain `ComicInfo.xml`
- **Permissions**: Verify read permissions on comic files

#### Missing Thumbnails
- **First load**: Thumbnails generate on first request
- **Check permissions**: Ensure `/data/thumbs` is writable
- **View errors**: Check error log via dashboard or `/data/thumbs_errors.log`

#### Authentication Problems
- **Verify credentials**: Check `OPDS_BASIC_USER` and `OPDS_BASIC_PASS`
- **Client support**: Ensure your client supports HTTP Basic Auth
- **Disable auth**: Set `DISABLE_AUTH=true` for testing

#### Wrong Links/URLs
- **Behind proxy**: Set `SERVER_BASE` and `URL_PREFIX` correctly
- **Protocol mismatch**: Ensure HTTP/HTTPS consistency (see [Notes on Clients and HTTPS](clients.md#notes-on-clients-and-https) for more)

#### Scan starts by itself:
- `AUTO_INDEX_ON_START`: "false"

### Debug Commands

> Access logs are disabled in the docker image. Enable them by removing `--no-access-log` from the `CMD` command in the Dockerfile. 

```bash
# View container logs
docker compose logs -f comicopds

# Check container health
docker compose ps

# Inspect configuration
docker compose exec comicopds env | grep -E "(SERVER_BASE|URL_PREFIX|CONTENT_BASE_DIR)"

# Test internal connectivity  
docker compose exec comicopds wget -qO- http://localhost:8080/healthz

# Check FTS5 availability
curl -u admin:password http://localhost:8382/debug/fts
```

### Log Files
- **Application logs**: `docker compose logs -f`
- **Thumbnail errors**: `/data/thumbs_errors.log`

### Performance Tuning
- **Large libraries**: Disable `AUTO_INDEX_ON_START`
- **Slow thumbnails**: Increase `THUMB_WORKERS`
- **Memory usage**: Reduce `PAGE_SIZE`
- **Network issues**: Check `SERVER_BASE` configuration
