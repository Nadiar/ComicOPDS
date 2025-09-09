## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- Comic collection in CBZ format with `ComicInfo.xml` metadata

### Folder Structure

Your comics should be organized like this:
```
/your/comics/
├── Batman (2016)/
│   ├── Batman (2016) - 001.cbz  # contains ComicInfo.xml
│   └── Batman (2016) - 002.cbz
├── Saga/
│   └── Saga - 001.cbz
└── ...
```

**Recommended**: I use [ComicRack CE](https://github.com/maforget/ComicRackCE) to organize my comic library and generate proper `ComicInfo.xml` metadata. For a comprehensive guide on setting up an optimal comic library structure and metadata management, see my detailed guide at: **[https://comicrack.baerentsen.space/](https://comicrack.baerentsen.space/)**


### Docker Compose Setup

Create a `docker-compose.yml` file:

```yaml
services:
  comicopds:
    image: gitea.baerentsen.space/frederikbaerentsen/comicopds:latest
    restart: unless-stopped
    ports:
      - "8382:8080"
    environment:
      CONTENT_BASE_DIR: /library
      SERVER_BASE: "http://192.168.10.10:8382"   # Replace with your server IP/domain
      URL_PREFIX: ""                             # set to "/comics" if behind a reverse proxy subpath
      DISABLE_AUTH: "false"                      # set to true to disable authentication 
      OPDS_BASIC_USER: "admin"
      OPDS_BASIC_PASS: "change-me-please"        # Use a strong password!
      ENABLE_WATCH: "false"                      # auto-update index on file changes
      PRECACHE_THUMBS: "false"                   # set true to pre-cache thumbs on reindex
      AUTO_INDEX_ON_START: "false"               # skip reindexing on each container start
    volumes:
      - "./comics:/library:ro"
      - "./data:/data"
```

### Launch Commands

```bash
# Build and start
docker compose build
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Access Points

- 📡 **OPDS Feed**: http://localhost:8382/
- 📊 **Dashboard**: http://localhost:8382/dashboard  
- 🧠 **Smart Lists**: http://localhost:8382/search